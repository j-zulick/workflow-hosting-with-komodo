# Verify an isolated update

The gate checks the rendered Compose model and the containers running on the local daemon. It runs a saved synthetic workflow with a fresh nonce. Two passing receipts are comparable only for the same stack, project, volume identities, and saved workflow marker.

The [hosted verification](https://github.com/j-zulick/workflow-hosting-with-komodo/actions/runs/45962276422) checked the same n8n workflow before and after the image upgrade from 2.49.6 to 2.49.7, then restored the backup on the prior image in a separate Compose project. The Windmill lab ran one saved job on its pinned worker. CI retains the before, after, restored and worker receipts.

## Check the Compose examples offline

From the checkout, run the checks below with Python 3.10+, Docker Compose, Node.js, and Bash. Compose rendering uses its CLI and can complete without an engine. The tests exercise Compose rendering, local fixture code, and the HTTP and daemon contracts with test doubles.

~~~sh
python3 -B tools/lab_gate.py config --stack n8n
python3 -B tools/lab_gate.py config --stack windmill
python3 -B -m unittest discover -s tests -v
~~~

The gate reports stack and service names, image references, and the published port. Passwords, API tokens, input bodies, and daemon environments stay out of its output. A rejected configuration, missing daemon, or failed workflow returns a nonzero result and leaves the requested receipt absent.

## Prepare a disposable lab

Use an isolated Docker host and a dedicated Compose project for one service. Start from the setup in the [n8n](n8n.md) or [Windmill](windmill.md) guide. Use `workflow-lab-n8n` or `workflow-lab-windmill` in place of the guide's example project for every Compose operation, including backups and upgrades. This gate accepts only project names beginning with `workflow-lab-`. Use the same name before and after. If the ordinary guide is already running, choose an unused loopback port.

From the checkout, generate a retained marker and create a private working directory. Keep the marker until the after receipt is finished.

~~~sh
umask 077
LAB_DIR=$(mktemp -d)
MARKER=$(openssl rand -hex 16)
printf '%s\n' "$MARKER" > "$LAB_DIR/fixture-marker"
~~~

### n8n fixture

Set the pre-upgrade exact version in `examples/n8n/.env`, start that project, and create an owner in the editor on the isolated host.

~~~sh
python3 -B tools/lab_gate.py fixture --stack n8n --marker "$MARKER" > "$LAB_DIR/n8n-smoke.json"
~~~

Import the generated JSON using the editor, inspect the two nodes, and publish **that synthetic workflow within the isolated instance** so the local `/webhook/workflow-hosting-upgrade-smoke` endpoint is active. Leave the saved fixture in place for the after run. Remove or unpublish the fixture when the rehearsal ends. [n8n Webhook development](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/workflow-development/)

~~~sh
python3 -B tools/lab_gate.py run --stack n8n --project workflow-lab-n8n \
  --env-file examples/n8n/.env --marker "$MARKER" --phase before \
  --receipt "$LAB_DIR/n8n-before.receipt.json"
~~~

The runner checks `/healthz/readiness`, observes the declared image reference and image ID, and requires the right marker and fresh nonce in the response. [n8n monitoring](https://docs.n8n.io/hosting/logging-monitoring/monitoring/)

### Windmill fixture

Set up a test workspace and create a Bash script named `workflow_hosting_upgrade_smoke`, with a path such as `f/lab/workflow_hosting_upgrade_smoke`. Generate and paste the code below, then deploy it **inside the isolated test workspace**. Use a token scoped to running the fixture and export it into the local shell as `WINDMILL_SMOKE_TOKEN`. Use the actual workspace ID and script path shown in that test instance.

~~~sh
python3 -B tools/lab_gate.py fixture --stack windmill --marker "$MARKER" > "$LAB_DIR/windmill-smoke.sh"
~~~

~~~sh
python3 -B tools/lab_gate.py run --stack windmill --project workflow-lab-windmill \
  --env-file examples/windmill/.env --marker "$MARKER" --phase before \
  --workspace rehearsal --script-path f/lab/workflow_hosting_upgrade_smoke \
  --receipt "$LAB_DIR/windmill-before.receipt.json"
~~~

The runner requires a healthy database and active worker, observes both image IDs, and sends one synchronous request to the saved fixture. Use Windmill run history to investigate a lost response. The runner leaves the execution unretried. [Windmill self-hosting health checks](https://www.windmill.dev/docs/advanced/self_host#health-endpoints) · [Windmill Bash script](https://www.windmill.dev/docs/getting_started/scripts_quickstart/bash) · [Windmill path execution example](https://hub.windmill.dev/scripts/windmill/2099/run-script-by-path-windmill)

## Protect state and verify the after receipt

Back up the original state, review the target release notes, and update the application pin through the owner of this project. Keep the data layout and database version fixed for this rehearsal. Repeat the same run command with `--phase after` and a new receipt filename, then compare it with the prior receipt.

~~~sh
python3 -B tools/lab_gate.py compare "$LAB_DIR/n8n-before.receipt.json" "$LAB_DIR/n8n-after.receipt.json"
~~~

Use the Windmill filenames for a Windmill run. The comparator requires a changed application image ID, unchanged saved output and retained named volumes. It checks for persistence through the tested upgrade. Restore is a separate acceptance run because a separate restore project has fresh volume identities. If either gate fails, inspect its first failed check and the service logs. Preserve the receipts and pair the pre-upgrade backup with the original pin when migration rollback is incompatible. [Backup, upgrade, and recovery](upgrades-and-recovery.md)
