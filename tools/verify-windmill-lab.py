"""Start one saved synthetic job on the disposable CI Windmill instance."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib import error, request

from lab_gate import NoRedirect, fixture, ready


def client_call(route, *, token=None, payload=None):
    url = "http://127.0.0.1:8000/api" + route
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    body = json.dumps(payload).encode() if payload is not None else None
    client = request.build_opener(request.ProxyHandler({}), NoRedirect())
    try:
        with client.open(request.Request(url, data=body, headers=headers), timeout=12) as response:
            return response.read(65537)
    except error.HTTPError as failure:
        status = failure.code
        failure.close()
        raise RuntimeError(f"{route} returned HTTP {status}.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    ready("windmill", 8000, 150)

    # This fixture account exists only in the newly initialized loopback CI lab.
    token = client_call("/auth/login", payload={
        "email": "admin@windmill.dev", "password": "changeme",
    }).decode().strip()
    if not token or any(char.isspace() for char in token):
        raise RuntimeError("The loopback lab returned an invalid login response.")

    workspace, username = "workflowlab", "labadmin"
    client_call("/workspaces/create", token=token, payload={
        "id": workspace, "name": "Workflow lab", "username": username,
    })
    marker = secrets.token_hex(16)
    script = f"u/{username}/workflow_hosting_upgrade_smoke"
    base = f"/w/{workspace}/scripts"
    client_call(base + "/create", token=token, payload={
        "path": script,
        "summary": "Synthetic workflow hosting verification",
        "description": "",
        "content": fixture("windmill", marker),
        "language": "bash",
        "kind": "script",
        "job_token_scopes": [],
        "schema": {
            "type": "object",
            "properties": {"nonce": {"type": "string"}},
            "required": ["nonce"],
        },
    })
    deadline = time.monotonic() + 60
    while True:
        response = client_call(base + "/get/p/" + script, token=token)
        current = json.loads(response)
        if current.get("lock_error_logs"):
            raise RuntimeError("The fixture script dependency job failed.")
        if current.get("lock") is not None:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("The fixture script did not become runnable.")
        time.sleep(1)

    gate = Path(__file__).with_name("lab_gate.py")
    subprocess.run(
        [sys.executable, str(gate), "run", "--stack", "windmill",
         "--project", "workflow-lab-windmill", "--env-file", str(args.env_file),
         "--marker", marker, "--phase", "before", "--workspace", workspace,
         "--script-path", script, "--receipt", str(args.receipt)],
        env={**os.environ, "WINDMILL_SMOKE_TOKEN": token}, check=True,
    )
    print("The isolated Windmill worker returned the expected synthetic result.")


if __name__ == "__main__":
    main()
