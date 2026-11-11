# Operations

[← Back to README](../README.md)

Commands assume the app lives in `/volume1/docker/contracts-manager` on a Synology (see the [installation guide](installation-synology.md)): the folder with `docker-compose.yml` and `.env`, with `db`, `media` and `backups` next to them. Elsewhere, drop `sudo` and adjust the paths. `docker compose` commands use the service names `app`, `db` and `worker`; the containers are called `contracts-manager-app`, `contracts-manager-db` and `contracts-manager-worker`.

## Update

Create a backup first (run the task `contracts-backup` once, see [Daily backups](#daily-backups)), then download the new image and restart:

```bash
cd /volume1/docker/contracts-manager && sudo docker compose pull
```

```bash
sudo docker compose up -d
```

`docker compose pull` downloads the new image, `up -d` restarts the containers with it. New migrations and translations are applied automatically on start, and the data in `db` and `media` is kept. Afterwards `sudo docker compose ps` should show `app`, `worker` and `db`.

**The Compose file is not updated automatically.** It rarely changes. If a version needs a changed file, the release notes on GitHub say so. Download it again under another name and compare:

```bash
curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml -o docker-compose.new.yml
```

```bash
diff docker-compose.yml docker-compose.new.yml
```

If `diff` prints nothing you are up to date (`rm docker-compose.new.yml`); otherwise `mv docker-compose.new.yml docker-compose.yml` and `sudo docker compose up -d`. For the file of one release instead of the newest, replace `main` in the address by the tag, e.g. `v0.1.0`.

**Pin a version.** With the default `latest`, every `docker compose pull` fetches the newest version. To decide yourself when to update, set `APP_IMAGE_TAG` in `.env` (e.g. `APP_IMAGE_TAG=0.1.0`) and raise it when you want to update. The available versions are listed on GitHub under *Tags* and *Packages*.

**Go back to an older version.** Set `APP_IMAGE_TAG` to the older version, then `sudo docker compose pull` and `sudo docker compose up -d`. Migrations are **not** undone: if the newer version changed the database, the older one may not start. In that case restore the backup made before the update (see [Restore](#restore)) first.

> [!NOTE]
> **Installations that cloned the repository** (folders `app/` and `data/`): the public repository started a new git history with version 0.1.0, so `git pull` in an old clone no longer works. Either get the Compose file with `curl` as above, or point the clone at the new history: `git fetch origin`, then `git reset --hard origin/main` (your `.env` is not tracked and stays). How to move such an installation to the new folder layout: [installation guide](installation-synology.md#existing-installations-folders-app-and-data).

**Changing the PostgreSQL major version** (e.g. 17 to 18): the database files only fit their major version. Back up, empty the `db` folder, start the new version and restore the backup as described below.

## Backup and restore

### Create a backup

The installation guide sets up a daily task in the DSM Task Scheduler ([installation › daily backup](installation-synology.md#7-daily-backup)). It runs `pg_dump` in the container `contracts-manager-db` and writes a compressed dump to `backups/contracts-YYYY-MM-DD_HHMMSS.dump`; dumps older than 30 days are deleted. To make a dump right now, run the task once (Task Scheduler › select the task › Run) or run `pg_dump` by hand:

```bash
sudo docker exec contracts-manager-db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > /volume1/docker/contracts-manager/backups/contracts-manual.dump
```

Installations that still use a clone of the repository can use `scripts/backup.sh` instead (`sudo sh scripts/backup.sh`). It writes to `DATA_PATH/backups` (or `backups` next to the Compose file without `DATA_PATH`), and `BACKUP_DIR` and `BACKUP_RETENTION_DAYS` in `.env` change the target and the retention.

### Daily backups

Control Panel › Task Scheduler › Create › Scheduled Task › User-defined script, user `root`, daily e.g. 03:00, optionally an email on failure, with the script from the [installation guide](installation-synology.md#7-daily-backup).

The backups are stored on the same Synology. To be safe against hardware failure, additionally copy `backups` and `media` (logos and contract documents) to an external target with **Hyper Backup**. Do not copy `db` while the database runs: a file copy of a running database is not reliable.

### Restore

Stop the app and the worker so nobody writes during the restore:

```bash
cd /volume1/docker/contracts-manager && sudo docker compose stop app worker
```

Restore the dump (adjust the file name). `--clean` replaces the current database content:

```bash
sudo docker compose exec -T db sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < /volume1/docker/contracts-manager/backups/contracts-2026-01-01_030000.dump
```

Start again:

```bash
sudo docker compose start app worker
```

You can safely check a backup in a separate database first:

```bash
sudo docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" restore_test && pg_restore --no-owner -U "$POSTGRES_USER" -d restore_test' < /volume1/docker/contracts-manager/backups/contracts-2026-01-01_030000.dump
```

```bash
sudo docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d restore_test -c "select count(*) from contracts_contract"'
```

```bash
sudo docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" restore_test'
```

## Backup file of the app (export and import)

In addition to the database dump above, the app can write **one ZIP file with all data and all uploaded files**. Use it to move the data to another installation or to keep a copy that does not depend on PostgreSQL. For daily backups the scheduled task above stays the better choice, because it runs unattended.

| | Database dump (scheduled task) | Backup file of the app |
|---|---|---|
| Content | Database only | Database content and files (logos, documents, attachments of the assistant) |
| Passwords | Included (as hashes) | **Not included** |
| Restore into | The same PostgreSQL major version | The same version of Contracts Manager |
| Automation | Task Scheduler | Manual, or the command below |

**In the app** (superusers only): Administration › Backup.

- *Download backup* creates the ZIP file.
- *Import backup*: choose the file and click *Check backup*. The next page lists the content. Nothing is changed until you click *Replace everything and import*.

**On the command line:**

```bash
sudo docker compose exec -T app python manage.py export_backup --output - > contracts-manager-backup.zip
```

```bash
sudo docker compose exec -T app python manage.py import_backup --yes - < contracts-manager-backup.zip
```

What an import does:

- It **replaces everything**: all existing data and files are removed, there is no merging. Users that are not in the backup no longer exist afterwards.
- All users are signed out.
- **No user has a password afterwards.** Everyone who had a local password before chooses a new one via *Forgot password?* on the sign-in page. This needs working email delivery and an email address in the account; otherwise set it with `sudo docker compose exec app python manage.py changepassword <user>`. Users who only sign in with single sign-on are not affected.
- The backup must have been created with the same version of Contracts Manager. To restore an older backup, install that version first, import, then update.
- Not part of the backup: the `.env` file, login lockouts and the history of the background worker.

The file contains personal data and is not encrypted. Store it in a safe place.

## Restart with an encrypted folder

After a reboot, an encrypted shared folder stays locked until it is mounted. In that case the containers deliberately do **not** start (`bind source path does not exist`) instead of creating an empty database in an unencrypted location.

After unlocking (Control Panel › Shared Folder › Encryption › Mount):

```bash
cd /volume1/docker/contracts-manager && sudo docker compose up -d
```

(For an encrypted folder the path is the one of your encrypted shared folder, e.g. `/volume1/contracts/contracts-manager`.) Alternatively let DSM mount the folder automatically at boot (key manager). That is more convenient, but then the data is only protected when the disks are removed, not when the whole NAS is stolen.

## Health check and logs

- Health check: `/health/` answers `{"status": "ok"}` when the database is reachable (used by the `app` container healthcheck).
- Logs: `sudo docker compose logs --tail 50 app` or `… worker`.
- Worker status, last reminder run and failed tasks: **Administration › System**.

## Locked login

After 5 wrong passwords for the same user name from the same IP address, logging in is locked for 15 minutes (`AXES_FAILURE_LIMIT`, `AXES_COOLOFF_MINUTES`). A successful login resets the counter. A superuser can lift a lock earlier in the Django admin under **Axes › Access attempts** (delete the entry), or lift all locks on the command line:

```bash
sudo docker compose exec app python manage.py axes_reset
```

## Forgotten password

Set a new password directly in the container, without signing in:

```bash
sudo docker compose exec app python manage.py changepassword <user name>
```
