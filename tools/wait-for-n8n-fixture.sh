#!/usr/bin/env bash
set -euo pipefail
# Inspect only the fixture registration on the disposable SQLite-backed CI app.
compose=(docker compose -p "$1" --env-file "$2" -f examples/n8n/compose.yaml)
for attempt in {1..40}; do
  if "${compose[@]}" exec -T n8n node --no-warnings - 2>/dev/null <<'JS'
const { DatabaseSync } = require("node:sqlite");
const database = new DatabaseSync("/home/node/.n8n/database.sqlite", { readOnly: true });
const registered = database.prepare(
  'SELECT 1 FROM webhook_entity WHERE "workflowId" = ? AND "webhookPath" = ? AND method = ?'
).get("WorkflowHostingSmoke", "workflow-hosting-upgrade-smoke", "POST");
database.close();
process.exitCode = registered ? 0 : 1;
JS
  then
    exit 0
  fi
  sleep 1
done
printf '%s\n' 'The saved n8n fixture webhook did not become registered.' >&2
exit 1
