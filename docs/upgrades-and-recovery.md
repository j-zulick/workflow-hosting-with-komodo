# Upgrade and recover

Use a dedicated lab first. Record the Compose project name, the exact image references, the Compose revision, and the synthetic workflow result. Keep backups and resolved environment values in private operator storage. The commands here cover the volumes in this repository. Existing deployments may also use external databases, object storage, credentials, or bind mounts.

## Before an upgrade

1. Run the service check and the saved workflow. Record their outcomes and versions.
2. Review upstream release notes for migrations and any required intermediate version. Identify a maintenance window, pause intake, and let active jobs finish.
3. Set `BACKUP_DIR` to an absolute path on private storage. Create a new directory for each run. The example below creates it under your home directory. Preserve the prior Compose file and environment securely with the backup, including any externally supplied keys.
4. Back up the database and instance state. Rehearse the restore on an isolated Docker host before upgrading an instance you depend on.
5. Change one application pin at a time. Deploy and compare the running version, readiness, and result from the same saved workflow. For Windmill an application pin change applies to its server and worker together.

```sh
umask 077
BACKUP_DIR=$(mktemp -d "$HOME/workflow-backup.XXXXXXXX")
```

Run each sequence in order and stop on the first failure. Use the Compose files and environment for the deployment you are backing up.

## n8n SQLite and encryption key

From `examples/n8n`, stop n8n before archiving the volume so SQLite and its associated files are consistent. The one-off container mounts the same volume without starting n8n.

```sh
docker compose -p n8n-guide --env-file .env stop n8n &&
docker compose -p n8n-guide --env-file .env run --rm --no-deps -T \
  --user root --entrypoint tar n8n -C /home/node/.n8n -czf - . \
  > "$BACKUP_DIR/n8n-state.tar.gz" &&
tar -tzf "$BACKUP_DIR/n8n-state.tar.gz" >/dev/null
```

Check that the archive includes the SQLite database and the n8n configuration. Keep the archive private. If the deployment supplies `N8N_ENCRYPTION_KEY` separately, restore that same key through the original secret mechanism. The database and key together are sensitive. Resume the prior service after a backup-only test with `docker compose -p n8n-guide --env-file .env up -d`.

## Windmill PostgreSQL and job logs

From `examples/windmill`, stop the server and worker after jobs have drained. Keep PostgreSQL running. Use [pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html) for the database and export the roles so Windmill's database grants have their expected targets at restore time. The role export here omits role passwords. Preserve any custom login secrets and external workspace encryption keys separately.

```sh
docker compose -p windmill-guide --env-file .env stop server worker &&
docker compose -p windmill-guide --env-file .env exec -T db \
  pg_dump -U postgres -Fc windmill > "$BACKUP_DIR/windmill.dump" &&
docker compose -p windmill-guide --env-file .env exec -T db \
  pg_dumpall -U postgres --roles-only --no-role-passwords > "$BACKUP_DIR/windmill-roles.sql" &&
docker compose -p windmill-guide --env-file .env exec -T db \
  pg_restore --list < "$BACKUP_DIR/windmill.dump" > "$BACKUP_DIR/windmill-dump-index.txt" &&
docker compose -p windmill-guide --env-file .env run --rm --no-deps -T \
  --user root --volume windmill-guide_job-logs:/logs --entrypoint tar db \
  -C /logs -czf - . > "$BACKUP_DIR/windmill-job-logs.tar.gz" &&
tar -tzf "$BACKUP_DIR/windmill-job-logs.tar.gz" >/dev/null
```

Expect a nonempty dump index and a readable log archive. If either service uses an external log store, back it up separately. Resume the prior services after a backup-only test with `docker compose -p windmill-guide --env-file .env up -d`.

## Restore and accept

An image downgrade is safe only when the older application supports the on-disk data. If migration compatibility is uncertain, preserve the failed deployment and restore the last known good backup with the prior image and secrets in a fresh Compose project. Keep intake paused until reconciliation. Any writes after the backup require separate review before switching back.

Restore from a trusted backup. In the following examples, put the **pre-upgrade** Compose file and private environment in a separate restore directory as `compose.yaml` and `.env`. Confirm that the project names below are unused on the isolated host before running them. The override ports let you distinguish the restored instances.

### n8n

From the n8n restore directory, with `BACKUP_DIR` still pointing to the backup:

```sh
N8N_PORT=15678 docker compose -p n8n-guide-restore --env-file .env \
  run --rm --no-deps -T --user root --entrypoint sh n8n -ec '
    cd /home/node/.n8n
    test -z "$(ls -A)"
    tar -xzf -
    chown -R node:node .
  ' < "$BACKUP_DIR/n8n-state.tar.gz" &&
N8N_PORT=15678 docker compose -p n8n-guide-restore --env-file .env up -d
```

Check `http://localhost:15678/healthz/readiness` and the old n8n version. Log in to the restored editor and run the saved workflow. If credentials are part of what you depend on, test a credential-backed workflow against a safe target as well.

### Windmill

From the Windmill restore directory, with the original database password in `.env`, create a fresh PostgreSQL volume before starting the application. The role import omits the fresh image's already-existing `postgres` role creation. These commands assume the stock `postgres` user and `windmill` database in this repository.

```sh
sed '/^CREATE ROLE postgres;$/d' "$BACKUP_DIR/windmill-roles.sql" \
  > "$BACKUP_DIR/windmill-restore-roles.sql" &&
docker compose -p windmill-guide-restore --env-file .env up -d --wait db &&
docker compose -p windmill-guide-restore --env-file .env exec -T db \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 < "$BACKUP_DIR/windmill-restore-roles.sql" &&
docker compose -p windmill-guide-restore --env-file .env exec -T db \
  pg_restore -U postgres -d windmill --exit-on-error --single-transaction \
  < "$BACKUP_DIR/windmill.dump" &&
docker compose -p windmill-guide-restore --env-file .env run --rm --no-deps -T \
  --user root --volume windmill-guide-restore_job-logs:/logs --entrypoint tar db \
  -C /logs -xzf - < "$BACKUP_DIR/windmill-job-logs.tar.gz" &&
WINDMILL_PORT=18000 docker compose -p windmill-guide-restore --env-file .env up -d
```

Check `http://localhost:18000/api/health/status?force=true`, confirm the old server and worker images, and run the saved Bash script in the restored workspace. Confirm job success, worker identity, and the expected JSON. See [pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html) and [Windmill workspace secret encryption](https://www.windmill.dev/docs/core_concepts/workspace_secret_encryption).

If a restore command fails, capture the first error and inspect the old configuration, archive, and prerequisites. Retry with a fresh isolated project after fixing the error. Once a restore passes, document the recovery point and reconcile late jobs or writes before switching traffic.
