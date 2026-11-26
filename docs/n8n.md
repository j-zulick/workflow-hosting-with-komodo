# Deploy and verify n8n

This guide draws on operating n8n as a Komodo Stack. The example uses a new project, a single n8n process, and SQLite. It pins [n8n 2.49.6](https://github.com/n8n-io/n8n/releases/tag/n8n%402.49.6).

**Verification.** Docker Compose 5.7.0 validated the example with its example environment file. Image pulls, n8n startup, workflow execution, Komodo deployment, and recovery are pending. The test runner could parse Compose configuration but could not access a Docker daemon.

## Start with Docker Compose

Install Docker Engine or Docker Desktop with the Compose plugin. Check `docker version` for a Server section and `docker compose version` for the plugin. You need registry access, a browser, `curl`, and an available local port (default 5678). For Komodo, you also need an existing Komodo Server with a connected Periphery agent and access to the repository. See the [official n8n Docker guide](https://docs.n8n.io/deploy/host-n8n/install-options/install-with-docker.md) and [Compose guide](https://docs.n8n.io/deploy/host-n8n/install-options/install-using-docker-compose.md).

From the repository root, use the [example files](../examples/n8n):

```sh
cd examples/n8n
(umask 077; cp -n .env.example .env)
docker compose -p n8n-guide --env-file .env config --quiet
docker compose -p n8n-guide --env-file .env pull
docker compose -p n8n-guide --env-file .env up -d
docker compose -p n8n-guide --env-file .env ps
curl -fsS http://localhost:5678/healthz/readiness
docker compose -p n8n-guide --env-file .env exec -T n8n n8n --version
```

When n8n is ready, expect HTTP 200 and version `2.49.6`. If it is still starting, check the logs and retry the readiness request once the migrations and startup finish. Open [the local n8n editor](http://localhost:5678) and create an owner account. On a remote test VM, tunnel its loopback port over SSH using your own host alias, for example `ssh -N -L 5678:127.0.0.1:5678 example-host`. Use a browser that supports secure cookies on `localhost` or configure HTTPS.

The named volume `n8n-guide_state` holds SQLite, the generated credential encryption key in the n8n configuration, and other instance state under `/home/node/.n8n`. Preserve that directory and its key together. Store any manually configured encryption key alongside the backup in a private secret store.

## Run a workflow and check its output

1. In the editor, create a workflow named **Update verification**. Start with **Manual Trigger**, then add **Edit Fields (Set)**.
2. In **Manual Mapping**, add a Number field named `items` with value `2` and a String field named `status` with value `ok`.
3. Save the workflow and select **Execute workflow**. Inspect the Edit Fields node's JSON output. Expect one item, `{"items":2,"status":"ok"}`, with the number stored as a number.
4. Check **Executions** for the successful run. If manual history is disabled, enable **Save manual executions** for this test workflow and rerun it. Record the n8n version, execution outcome, and synthetic output in your verification notes.

## Point Komodo at the same Compose file

Create a [Komodo Stack](https://komo.do/docs/deploy/compose) and select a test Server. Use the following values for the Stack configuration. Substitute your fork in **Repository** when you want to update the pin independently.

| Setting | Value |
| --- | --- |
| Source | Git repository |
| Repository and branch | `j-zulick/workflow-hosting-with-komodo`, `main` |
| Run directory | `examples/n8n` |
| File path | `compose.yaml` |
| Project name | `n8n-guide` |
| Stack environment | `N8N_VERSION=2.49.6` and `N8N_PORT=5678` on separate lines |

For adoption of the CLI lab on the same host, keep the same project name, Compose file, port, and named volume. Compare `docker compose ls` before deploying. On a different test host, Komodo starts with a new local volume. Save the Stack and select **Deploy**. Inspect its update log and n8n service logs, then run the readiness, version, and workflow checks on the target instance. Use the same Compose commands from the Stack checkout on that host if you have shell access. Let Komodo own subsequent deploys for a Komodo-managed Stack.

## Upgrade and verify again

For a rehearsal on a fresh, isolated project, start with [2.49.5](https://github.com/n8n-io/n8n/releases/tag/n8n%402.49.5) and upgrade to `2.49.6`. Keep the project name and volume the same across both runs.

1. Record `n8n --version` as above and the image reference from `docker compose -p n8n-guide --env-file .env images`. Save the current Compose inputs privately and [back up the state volume](upgrades-and-recovery.md#n8n-sqlite-and-encryption-key).
2. Review [n8n releases](https://github.com/n8n-io/n8n/releases) between the current and target tags. Set `N8N_VERSION` to the approved exact version in the Stack environment, or edit the local `.env` for a CLI-managed lab.
3. In Komodo, save and deploy, then inspect the update and container logs. For CLI, repeat `config --quiet`, `pull`, `up -d`, and `ps` using the same project and environment file.
4. Confirm readiness, query the running version, and execute the **saved** workflow again. Accept the update when the target version, execution success, and the same JSON all agree.
5. If the update ran a migration and the prior image cannot use the resulting database, [restore the pre-update state](upgrades-and-recovery.md#restore-and-accept) into a fresh project paired with the old image and original encryption key.

## First checks when something fails

| Symptom | First check and expected result |
| --- | --- |
| Port is in use | Read the Compose error and check the listener. Pick an unused `N8N_PORT`, then use that port in the browser and readiness URL. |
| Container is running and readiness fails | Run `docker compose -p n8n-guide --env-file .env logs --tail=100 n8n`. Expect successful migration and startup with no restart loop. |
| Editor opens and execution fails | Open the failed execution and inspect the first failed node. The test has two connected nodes and requires zero credentials. |
| Workflow is missing after redeploy | Compare the Compose project name and the `state` mount with the prior deployment. The mounted volume should have the same name and contain the prior SQLite database. |
