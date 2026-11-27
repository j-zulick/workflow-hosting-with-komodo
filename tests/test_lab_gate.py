import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lab_gate", ROOT / "tools" / "lab_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
MARKER = "d" * 32


class ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker Compose CLI required for rendered-config tests")
        cls.models = {}
        try:
            for stack in ("n8n", "windmill"):
                env = ROOT / "examples" / stack / ".env.example"
                cmd = gate.compose(stack, env, "workflow-lab-ci") + ["config", "--format", "json"]
                cls.models[stack] = json.loads(gate.quiet_output(cmd))
        except gate.GateError as error:
            raise unittest.SkipTest("Docker Compose CLI plugin required") from error

    def test_accepts_real_rendered_configs(self):
        for stack, model in self.models.items():
            with self.subTest(stack=stack):
                summary = gate.inspect_config(stack, model)
                self.assertEqual(summary["project"], "workflow-lab-ci")

    def test_rejects_public_binding(self):
        model = copy.deepcopy(self.models["n8n"])
        model["services"]["n8n"]["ports"][0]["host_ip"] = "0.0.0.0"
        with self.assertRaisesRegex(gate.GateError, "loopback"):
            gate.inspect_config("n8n", model)

    def test_rejects_foreign_named_volume(self):
        model = copy.deepcopy(self.models["n8n"])
        model["volumes"]["state"]["name"] = "other_project_state"
        with self.assertRaisesRegex(gate.GateError, "outside"):
            gate.inspect_config("n8n", model)

    def test_rejects_floating_and_mixed_images(self):
        model = copy.deepcopy(self.models["n8n"])
        model["services"]["n8n"]["image"] = "docker.n8n.io/n8nio/n8n:latest"
        with self.assertRaisesRegex(gate.GateError, "exact release"):
            gate.inspect_config("n8n", model)
        model = copy.deepcopy(self.models["windmill"])
        model["services"]["worker"]["image"] = "ghcr.io/windmill-labs/windmill:1.886.0@" + "sha256:" + "a" * 64
        with self.assertRaisesRegex(gate.GateError, "share one"):
            gate.inspect_config("windmill", model)

    def test_rejects_host_socket_and_privilege(self):
        model = copy.deepcopy(self.models["windmill"])
        model["services"]["worker"]["privileged"] = True
        with self.assertRaisesRegex(gate.GateError, "host-access"):
            gate.inspect_config("windmill", model)
        model["services"]["worker"]["privileged"] = False
        model["services"]["worker"]["volumes"].append(
            {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"})
        with self.assertRaisesRegex(gate.GateError, "named volumes"):
            gate.inspect_config("windmill", model)


class HTTPChecks(unittest.TestCase):
    def test_uses_numeric_loopback_without_proxy_or_redirect(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, maximum):
                self.maximum = maximum
                return b"{}"

        class Client:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        client = Client()
        with patch.object(gate.urllib.request, "build_opener", return_value=client) as opener:
            gate.exchange(5678, "/healthz/readiness")
        self.assertEqual(client.request.full_url, "http://127.0.0.1:5678/healthz/readiness")
        proxy, redirects = opener.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsNone(redirects.redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_does_not_retry_or_consume_redirected_job(self):
        class Client:
            calls = 0

            def open(self, request, timeout):
                self.calls += 1
                raise urllib.error.HTTPError(request.full_url, 302, "Redirect", None, None)

        client = Client()
        with patch.object(gate.urllib.request, "build_opener", return_value=client):
            with self.assertRaisesRegex(gate.GateError, "302"):
                gate.run_fixture("n8n", 5678, MARKER)
        self.assertEqual(client.calls, 1)

    def test_windmill_requires_worker_health(self):
        clock = [0.]
        def sleep(duration):
            clock[0] += duration

        with patch.object(gate, "exchange", return_value=b'{"status":"degraded"}'):
            with self.assertRaisesRegex(gate.GateError, "deadline"):
                gate.ready("windmill", 8000, 3, now=lambda: clock[0], sleep=sleep)
        with patch.object(gate, "exchange", return_value=b'{"status":"healthy"}'):
            gate.ready("windmill", 8000, 3, now=lambda: clock[0], sleep=sleep)

    def test_requires_nonce_and_exact_saved_fixture(self):
        def http(port, path, payload, token, *, timeout):
            self.assertEqual(port, 8000)
            self.assertEqual(path, "/api/w/rehearsal/jobs/run_wait_result/p/f/lab/workflow_hosting_upgrade_smoke")
            self.assertEqual(token, "local-fixture-token")
            return json.dumps({"items": 2, "status": "ok", "marker": MARKER, "nonce": payload["nonce"]}).encode()

        with patch.object(gate, "exchange", side_effect=http):
            result = gate.run_fixture("windmill", 8000, MARKER, workspace="rehearsal",
                                      script_path="f/lab/workflow_hosting_upgrade_smoke", token="local-fixture-token")
            self.assertEqual(result["marker"], MARKER)
        with patch.object(gate, "exchange", return_value=b'{"items":2,"status":"ok","marker":"old","nonce":"cached"}'):
            with self.assertRaisesRegex(gate.GateError, "differs"):
                gate.run_fixture("n8n", 5678, MARKER)


class ArtifactTests(unittest.TestCase):
    def receipt(self, phase, tag, digest, date):
        return {
            "schema": "workflow-lab-receipt/1", "phase": phase, "stack": "n8n", "project": "workflow-lab-ci",
            "status": "pass", "images": {"n8n": {"reference": f"docker.n8n.io/n8nio/n8n:{tag}", "id": "sha256:" + digest * 64}},
            "state": {"n8n:/home/node/.n8n": "workflow-lab-ci_state"},
            "workflow": {"status": "ok", "items": 2, "marker": MARKER}, "completed_at": date,
        }

    def test_compares_changed_image_and_persisted_fixture(self):
        before = self.receipt("before", "2.49.6", "a", "2026-11-26T15:00:00+00:00")
        after = self.receipt("after", "2.49.7", "b", "2026-11-26T15:10:00+00:00")
        self.assertEqual(gate.compare(before, after)["status"], "pass")
        for field, value in (
            ("project", "other"), ("state", {}), ("workflow", {"status": "ok"}),
            ("images", before["images"]), ("completed_at", before["completed_at"]),
        ):
            with self.subTest(field=field), self.assertRaises(gate.GateError):
                gate.compare(before, {**after, field: value})

    def test_inspects_running_images_without_dumping_environment(self):
        key = "docker.n8n.io/n8nio/n8n:2.49.7"
        content = "sha256:" + "a" * 64
        mounts = [{"Type": "volume", "Name": "workflow-lab-ci_state", "Destination": "/home/node/.n8n"}]
        commands = []
        def runner(command):
            commands.append(command)
            if "ps" in command:
                return b"aabbccddeeff\n"
            return "\n".join(map(json.dumps, (key, content, mounts))).encode()

        images, state = gate.inspect_runtime("n8n", ["docker", "compose"], {"n8n": key}, runner)
        self.assertEqual(images["n8n"]["id"], content)
        self.assertEqual(state["n8n:/home/node/.n8n"], "workflow-lab-ci_state")
        self.assertNotIn(".Env", str(commands))

    def test_receipts_are_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.receipt.json"
            gate.write_new(path, {"status": "pass"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                gate.write_new(path, {"status": "new"})

    @unittest.skipUnless(shutil.which("node"), "Node.js required for fixture code execution")
    def test_n8n_fixture_code_executes_with_the_same_payload(self):
        fixture = json.loads(gate.fixture("n8n", MARKER))
        self.assertFalse(fixture["active"])
        node = next(item for item in fixture["nodes"] if item["name"] == "Echo fixture")
        program = "const $input={first:()=>({json:{body:{nonce: 'a'.repeat(32)}}})};\n"
        program += "const value=(() => {\n" + node["parameters"]["jsCode"] + "\n})();"
        program += "\nprocess.stdout.write(JSON.stringify(value[0].json));"
        result = subprocess.run(["node", "-e", program], check=True, capture_output=True, timeout=5)
        self.assertEqual(json.loads(result.stdout), {"items": 2, "status": "ok", "marker": MARKER, "nonce": "a" * 32})

    @unittest.skipUnless(shutil.which("bash"), "Bash required for fixture code execution")
    def test_windmill_fixture_code_executes_with_the_same_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "run.sh"
            script.write_text(gate.fixture("windmill", MARKER), encoding="utf-8")
            subprocess.run(["bash", str(script), "a" * 32], cwd=tmp, check=True, timeout=5)
            self.assertEqual(gate.read_json(Path(tmp) / "result.json"),
                             {"items": 2, "status": "ok", "marker": MARKER, "nonce": "a" * 32})


if __name__ == "__main__":
    unittest.main()
