#!/usr/bin/env python3
"""Validate lab Compose models and capture loopback upgrade evidence."""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_SERVICES = {"n8n": ("n8n",), "windmill": ("server", "worker")}
READY = {"n8n": "/healthz/readiness", "windmill": "/api/health/status?force=true"}
STATE = {
    "n8n": {"n8n": ("/home/node/.n8n",)},
    "windmill": {"db": ("/var/lib/postgresql/data",), "worker": ("/tmp/windmill/logs",)},
}
PIN = re.compile(r"\d+\.\d+\.\d+(?:[-.][a-z0-9.-]+)?")
POSTGRES_PIN = re.compile(r"\d+\.\d+(?:[-.][a-z0-9.-]+)?")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
IDENTIFIER = re.compile(r"[0-9a-f]{12,64}")
MARKER = re.compile(r"[0-9a-f]{24,64}")
WORKSPACE = re.compile(r"[A-Za-z0-9_-]{1,100}")
PROJECT = re.compile(r"workflow-lab-[a-z0-9][a-z0-9_-]{0,45}")
SMOKE_PATH = "workflow-hosting-upgrade-smoke"


class GateError(Exception):
    """A diagnostic that intentionally omits runtime environment and body values."""


def need(test, message):
    if not test:
        raise GateError(message)


def quiet_output(command):
    try:
        environment = os.environ.copy()
        environment.pop("WINDMILL_SMOKE_TOKEN", None)
        result = subprocess.run(command, check=True, capture_output=True, timeout=25, env=environment)
    except (OSError, subprocess.SubprocessError) as error:
        raise GateError("Docker Compose or daemon operation failed. Check the isolated lab and run the command locally for details.") from error
    return result.stdout


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def stack_file(stack):
    return ROOT / "examples" / stack / "compose.yaml"


def compose(stack, environment, project):
    return [
        "docker", "compose", "--project-name", project,
        "--env-file", str(environment), "--file", str(stack_file(stack)),
    ]


def inspect_config(stack, model):
    services = model.get("services", {})
    expected = {"n8n": {"n8n"}, "windmill": {"db", "server", "worker"}}[stack]
    need(set(services) == expected, "Unexpected services in the rendered model.")
    app = next(iter(APP_SERVICES[stack]))
    host_port = None
    images = {}
    for name, service in services.items():
        image = service.get("image", "")
        version, separator, digest = image.rsplit("/", 1)[-1].partition("@")
        tag = version.partition(":")[2]
        need(bool((POSTGRES_PIN if name == "db" else PIN).fullmatch(tag)) and (not separator or bool(DIGEST.fullmatch(digest))),
             f"Service {name} needs an exact release tag and a valid digest if supplied.")
        if name in APP_SERVICES[stack]:
            prefix = {"n8n": "docker.n8n.io/n8nio/n8n:", "windmill": "ghcr.io/windmill-labs/windmill:"}[stack]
        else:
            prefix = "postgres:"
        need(image.startswith(prefix), f"Unexpected image repository for {name}.")
        images[name] = image
        for capability in ("privileged", "network_mode", "cap_add", "devices", "build"):
            need(not service.get(capability), f"Service {name} has an unsupported host-access setting.")
        mounts = service.get("volumes", [])
        need(all(mount.get("type") == "volume" for mount in mounts),
             f"Service {name} must use project-scoped named volumes.")
        for mount in mounts:
            label = mount.get("source", "")
            volume = model.get("volumes", {}).get(label, {})
            name_prefix = model.get("name", "") + "_"
            need(volume.get("name") == name_prefix + label and not volume.get("external"),
                 f"Service {name} uses a volume outside its Compose project.")
        ports = service.get("ports", [])
        if name == app:
            need(len(ports) == 1 and ports[0].get("host_ip") == "127.0.0.1",
                 "Application publishing must use one IPv4 loopback port.")
            port = ports[0]
            need(port.get("target") == {"n8n": 5678, "windmill": 8000}[stack] and port.get("protocol") == "tcp",
                 "Unexpected application target port.")
            host_port = int(port["published"])
            need(1024 <= host_port <= 65535, "Use a nonprivileged loopback port.")
        else:
            need(not ports, f"Service {name} must not publish a port.")
    for service, targets in STATE[stack].items():
        rendered_mounts = {mount.get("target") for mount in services[service].get("volumes", [])}
        need(set(targets) <= rendered_mounts, f"Expected persistent mount missing on {service}.")
    if stack == "windmill":
        need(images["server"] == images["worker"] and "@" in images["server"],
             "Windmill server and worker must share one digest-pinned image.")
        need(images["db"].startswith("postgres:17."), "This example uses the PostgreSQL 17 state layout.")
        need("healthcheck" in services["db"], "Windmill PostgreSQL must declare a database healthcheck.")
        for service in APP_SERVICES[stack]:
            definition = services[service]
            need(definition.get("environment", {}).get("MODE") == service, "Windmill service mode mismatch.")
            need(definition.get("depends_on", {}).get("db", {}).get("condition") == "service_healthy",
                 "Windmill must wait for a healthy PostgreSQL service.")
    return {"stack": stack, "project": model.get("name"), "port": host_port, "images": images}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, status, reason, headers, location):
        return None


def exchange(port, path, payload=None, auth=None, timeout=4):
    need(1024 <= port <= 65535, "Invalid loopback port.")
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = "Bearer " + auth
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers)
    try:
        with client.open(request, timeout=timeout) as response:
            need(response.status == 200, "Expected HTTP 200 from the selected lab endpoint.")
            body = response.read(65537)
    except urllib.error.HTTPError as error:
        error.close()
        raise GateError(f"Lab HTTP check failed with status {error.code}.") from error
    except (urllib.error.URLError, OSError) as error:
        raise GateError("Lab HTTP endpoint is unavailable.") from error
    need(len(body) <= 65536, "Lab HTTP response exceeded its size limit.")
    return body


def ready(stack, port, deadline, *, now=time.monotonic, sleep=time.sleep):
    end = now() + deadline
    while True:
        try:
            body = exchange(port, READY[stack], timeout=min(4, max(0.05, end - now())))
            if stack == "windmill":
                payload = json.loads(body)
                need(isinstance(payload, dict) and payload.get("status") == "healthy",
                     "Windmill reports a degraded or unhealthy database or worker state.")
            return
        except (GateError, ValueError) as error:
            if now() >= end:
                raise GateError(f"{stack} readiness failed before the deadline.") from error
            sleep(min(1, max(0, end - now())))


def run_fixture(stack, port, marker, *, workspace=None, script_path=None, token=None):
    nonce = secrets.token_hex(16)
    if stack == "windmill":
        need(WORKSPACE.fullmatch(workspace or "") and bool(token), "Set a test workspace and Windmill smoke token.")
        parts = script_path.split("/") if script_path else []
        need(len(parts) == 3 and parts[0] in {"f", "u"} and all(WORKSPACE.fullmatch(x) for x in parts)
             and parts[-1] == "workflow_hosting_upgrade_smoke",
             "Use the dedicated Windmill smoke script path.")
        path = "/api/w/" + workspace + "/jobs/run_wait_result/p/" + "/".join(parts)
    else:
        path = "/webhook/" + SMOKE_PATH
    # Run exactly once, even if the response is lost. An operator can reconcile execution history.
    data = json.loads(exchange(port, path, {"nonce": nonce}, token, timeout=30))
    expect = {"items": 2, "status": "ok", "marker": marker, "nonce": nonce}
    need(isinstance(data, dict) and type(data.get("items")) is int and data == expect,
         "Smoke result differs from the expected output, marker or fresh nonce.")
    return {"items": data["items"], "status": data["status"], "marker": marker}


def inspect_runtime(stack, project, images, runner=quiet_output):
    loaded = {}
    state = {}
    for service, expected in images.items():
        handles = runner(project + ["ps", "--status", "running", "-q", service]).decode().split()
        need(len(handles) == 1 and IDENTIFIER.fullmatch(handles[0]),
             f"Expected one running {service} container.")
        # Query only selected inspect fields. The full container environment contains private values.
        query = "{{json .Config.Image}}\n{{json .Image}}\n{{json .Mounts}}"
        config, content, mounts = [json.loads(line) for line in runner(["docker", "inspect", "--format", query, handles[0]]).splitlines()]
        need(config == expected and bool(DIGEST.fullmatch(content)), f"Runtime image for {service} differs from the expected image.")
        loaded[service] = {"reference": config, "id": content}
        for target in STATE[stack].get(service, ()):
            candidates = [m for m in mounts if m.get("Type") == "volume" and m.get("Destination") == target]
            need(len(candidates) == 1 and bool(LABEL.fullmatch(candidates[0].get("Name", ""))),
                 f"Missing volume instance for {service} at {target}.")
            state[f"{service}:{target}"] = candidates[0]["Name"]
    if stack == "windmill":
        need(loaded["server"] == loaded["worker"], "Windmill server and worker resolved to different image IDs.")
    return loaded, state


def compare(before, after):
    need(before.get("schema") == after.get("schema") == "workflow-lab-receipt/1", "Unsupported receipt schema.")
    need(before.get("phase") == "before" and after.get("phase") == "after", "Pass the before receipt first.")
    need(before.get("stack") == after.get("stack") and before.get("project") == after.get("project"),
         "Receipts must cover one stack and project.")
    need(before.get("status") == after.get("status") == "pass", "Both receipts must pass.")
    first = datetime.fromisoformat(before["completed_at"])
    second = datetime.fromisoformat(after["completed_at"])
    need(first.tzinfo is not None and second.tzinfo is not None and first < second,
         "Receipt completion times must be timezone-aware and strictly ordered.")
    need(bool(before.get("state")) and before["state"] == after.get("state"), "Persistent volume identity changed.")
    need(before.get("workflow") == after.get("workflow") and before["workflow"].get("status") == "ok",
         "Saved fixture marker or output differs.")
    service = APP_SERVICES[before["stack"]][0]
    a, b = before["images"][service], after["images"][service]
    need(a["reference"] != b["reference"] and a["id"] != b["id"], "No changed application image was observed.")
    if before["stack"] == "windmill":
        need(before["images"].get("db") == after["images"].get("db"),
             "The Windmill application rehearsal requires an unchanged PostgreSQL image.")
    return {"status": "pass", "stack": before["stack"], "before_image": a["reference"], "after_image": b["reference"]}


def write_new(path, data):
    content = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if path:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    else:
        sys.stdout.write(content)


def fixture(stack, marker):
    path = ROOT / "examples" / stack / ("upgrade-smoke.json" if stack == "n8n" else "upgrade-smoke.sh")
    return path.read_text(encoding="utf-8").replace("REPLACE_WITH_FIXTURE_MARKER", marker)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("config", "run", "fixture"):
        sub = commands.add_parser(action)
        sub.add_argument("--stack", choices=APP_SERVICES, required=True)
        if action in ("config", "run"):
            sub.add_argument("--env-file", type=Path)
            sub.add_argument("--project", default="workflow-lab-rehearsal")
        if action in ("run", "fixture"):
            sub.add_argument("--marker", required=True, help="A retained fixture label from openssl rand -hex 16.")
        if action == "run":
            sub.add_argument("--phase", choices=("before", "after"), required=True)
            sub.add_argument("--receipt", type=Path, required=True)
            sub.add_argument("--deadline", type=int, default=90)
            sub.add_argument("--workspace")
            sub.add_argument("--script-path")
    sub = commands.add_parser("compare")
    sub.add_argument("before", type=Path)
    sub.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "compare":
            write_new(None, compare(read_json(args.before), read_json(args.after)))
            return 0
        if args.action in {"fixture", "run"}:
            need(MARKER.fullmatch(args.marker), "Invalid fixture marker format.")
        if args.action == "fixture":
            sys.stdout.write(fixture(args.stack, args.marker))
            return 0
        need(PROJECT.fullmatch(args.project), "Use a dedicated project name beginning with workflow-lab-.")
        if args.action == "run":
            need(args.env_file is not None, "A private lab environment file is required for runtime checks.")
            need(1 <= args.deadline <= 180, "The deadline must be 1 to 180 seconds.")
            need(not args.receipt.exists(), "Refusing to replace an existing receipt.")
        default_env = ROOT / "examples" / args.stack / ".env.example"
        project = compose(args.stack, args.env_file or default_env, args.project)
        model = json.loads(quiet_output(project + ["config", "--format", "json"]))
        summary = inspect_config(args.stack, model)
        need(summary["project"] == args.project, "Unexpected Compose project after rendering.")
        if args.action == "config":
            write_new(None, summary)
            return 0
        if args.stack == "windmill":
            environment = model["services"]["db"]["environment"]
            need(MARKER.fullmatch(environment.get("POSTGRES_PASSWORD", "")),
                 "Windmill requires a private generated hexadecimal database password.")
        images, state = inspect_runtime(args.stack, project, summary["images"])
        ready(args.stack, summary["port"], args.deadline)
        marker = run_fixture(args.stack, summary["port"], args.marker, workspace=args.workspace,
                             script_path=args.script_path,
                             token=os.environ.get("WINDMILL_SMOKE_TOKEN") if args.stack == "windmill" else None)
        write_new(args.receipt, {
            "schema": "workflow-lab-receipt/1", "status": "pass", "phase": args.phase,
            "stack": args.stack, "project": args.project, "images": images,
            "state": state, "workflow": marker, "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        return 0
    except (GateError, KeyError, TypeError, AttributeError, OSError, ValueError) as error:
        sys.stderr.write((str(error) if isinstance(error, GateError) else
                          "Lab input, response or receipt is unavailable or malformed.") + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
