# Deploy and verify Windmill

This is a reference deployment. It uses the Community Edition [Windmill 1.877.0 container](https://github.com/windmill-labs/windmill/pkgs/container/windmill/versions), pinned by tag and manifest digest for both server and worker, and [PostgreSQL 17.12 Alpine](https://hub.docker.com/_/postgres/tags?name=17.12-alpine).

The upstream [v1.886.0 release](https://github.com/windmill-labs/windmill/releases/tag/v1.886.0) is a candidate for a later rehearsal. This example retains the recorded 1.877.0 image digest until a newer manifest is verified. An application upgrade and a PostgreSQL major migration are separate rehearsals.

**Verification.** The pinned server, PostgreSQL and worker started in a disposable Compose lab. The authenticated workspace saved and invoked the Bash fixture. The worker returned the expected two-item result and the receipt recorded its image and mounts. A Windmill upgrade, database restore and Komodo deployment are separate operator rehearsals. [Verification run](https://github.com/j-zulick/workflow-hosting-with-komodo/actions/runs/45962276422)

## Start with Docker Compose

Use Docker with the Compose plugin, a browser, `curl`, and `openssl`. Run `docker version` and check for the Server section. Use a disposable Docker host or VM with registry access and at least 2 CPUs and 4 GB of memory as a starting allocation. Make sure port 8000 is free. For Komodo, use an existing Komodo Server with connected Periphery and Git access. The worker runs scripts from trusted lab users only. Review Windmill's [worker isolation guidance](https://www.windmill.dev/docs/advanced/security_isolation) and [self-hosting guide](https://www.windmill.dev/docs/advanced/self_host) before deploying a shared or internet-facing instance.

From the repository root, move into the [example directory](../examples/windmill). The initialization block preserves an existing `.env`.

```sh
cd examples/windmill
if [ ! -e .env ]; then
  (
    set -eu
    umask 077
    password=$(openssl rand -hex 32)
    sed "s/REPLACE_WITH_RANDOM_HEX/$password/" .env.example > .env
  )
fi
docker compose -p windmill-guide --env-file .env config --quiet
docker compose -p windmill-guide --env-file .env pull
docker compose -p windmill-guide --env-file .env up -d
docker compose -p windmill-guide --env-file .env ps
curl -fsS 'http://localhost:8000/api/health/status?force=true'
for service in server worker; do
  container=$(docker compose -p windmill-guide --env-file .env ps -q "$service")
  docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' "$container"
done
```

Expect PostgreSQL healthy, the server and worker running, and a healthy API response after the worker starts sending heartbeats. Both containers should report the same pinned image reference and image ID. If a service is still starting, inspect its logs and retry. Open [the local Windmill editor](http://localhost:8000). For a remote lab, use an SSH tunnel such as `ssh -N -L 8000:127.0.0.1:8000 example-host`. Follow [Windmill's first-login instructions](https://www.windmill.dev/docs/advanced/self_host#first-time-login) on the fresh instance. Replace the bootstrap superadmin credentials, then create a test workspace.

PostgreSQL stores the workspace, scripts, job records, and application data in `windmill-guide_database`. A separate volume shares job log files between the server and worker. Keep the generated database password in the private `.env` or a Komodo secret. Changing the environment value after PostgreSQL initialization requires a coordinated database password change. PostgreSQL is reachable only on the Compose network in this lab.

## Run a script and check the completed job

1. In the test workspace, create a [Bash script](https://www.windmill.dev/docs/getting_started/scripts_quickstart/bash) named `update_verification`. Place it under your user or test folder.
2. Paste this script, then select **Deploy** to save it.

   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   printf '{"items":%d,"status":"ok"}\n' "$((1 + 1))" > result.json
   ```

3. Open the deployed script and select **Run** with no inputs. In **Runs**, open its completed job. Expect success and the JSON result `{"items":2,"status":"ok"}`. Check the worker identity in the run details or match the job ID against the worker service logs.
4. Record the image version, completed status, and synthetic result. A web page that loads or a job that stays queued leaves this check incomplete.

## Point Komodo at the same Compose file

Create a [Komodo Stack](https://komo.do/docs/deploy/compose) and select a test Server.

| Setting | Value |
| --- | --- |
| Source | Git repository |
| Repository and branch | `j-zulick/workflow-hosting-with-komodo`, `main` or your fork and branch |
| Run directory | `examples/windmill` |
| File path | `compose.yaml` |
| Project name | `windmill-guide` |
| Stack environment | Copy the image, PostgreSQL version, and port from [.env.example](../examples/windmill/.env.example). Set `WINDMILL_DB_PASSWORD=[[WINDMILL_DB_PASSWORD]]` using a secret you created in Komodo. |

Use [Komodo's secret settings](https://komo.do/docs/configuration/variables) for the hex password. When adopting the CLI lab on the same host, use its original database password and project name. Check `docker compose ls` and the existing volumes first. A different host starts with empty local volumes. Save the Stack, select **Deploy**, and inspect the update plus all three service states. Repeat the health and completed job checks against this deployment. Let Komodo own subsequent deploys for a Komodo-managed Stack.

## Upgrade and verify again

The [automated isolated verification gate](verification.md) requires Windmill health and runs the saved Bash fixture through a worker.

1. Record the server and worker image references and IDs using the inspect loop above. Record the current health and worker state, then [back up PostgreSQL and job logs](upgrades-and-recovery.md#windmill-postgresql-and-job-logs).
2. Review [Windmill releases](https://github.com/windmill-labs/windmill/releases) and choose a target Community Edition image from the [container registry](https://github.com/windmill-labs/windmill/pkgs/container/windmill/versions). Update `WINDMILL_IMAGE` to the new tag and matching digest in the Stack environment or local `.env`. Both services read that one setting. Keep the PostgreSQL major version fixed for this application upgrade.
3. In Komodo, save and deploy, then inspect the deploy and container logs. For CLI, repeat `config --quiet`, `pull`, `up -d`, and `ps` using the same project. Confirm the server and worker show the target image.
4. Check the health endpoint. Run the **saved** script again and confirm a worker completed the job and returned the same JSON.
5. If the new server migrated the database and the old release cannot read that schema, pair the prior image with a [pre-upgrade restore](upgrades-and-recovery.md#restore-and-accept). A PostgreSQL major upgrade has [its own procedure](https://www.windmill.dev/docs/advanced/self_host#upgrade-postgresql-to-18).

## First checks when something fails

Use `docker compose -p windmill-guide --env-file .env logs --tail=100 db server worker` from the directory that contains the Compose file.

| Symptom | First check and expected result |
| --- | --- |
| Server and worker restart | Check PostgreSQL health and matching database credentials. Expect the `db` healthcheck to pass and both clients to connect without authentication errors. |
| UI opens and the job remains queued | Look for a live default worker in the health response and worker logs. Expect a registered worker on the default group that can pick up the job. |
| Worker starts the job and it fails | Open the run details and worker logs. Expect Bash to run the sample and write valid JSON to `result.json`. |
| Health reports version mismatch | Check the server and worker images. Both should resolve to the same target digest. Redeploy them together and run a fresh job after the worker registers. |
| Workspace is missing after redeploy | Compare the Compose project and database volume name against the previous deployment. Expect the original data volume to be attached. |
