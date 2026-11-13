# Installation on a Synology NAS

[← Back to README](../README.md)

This guide sets up Contracts Manager on a Synology NAS with Docker Compose only. Two files in one folder are all you need: `docker-compose.yml` describes the containers, `.env` holds your settings. Nothing is cloned or built on the Synology; the app comes as a [ready-made Docker image](#the-docker-image). For a detailed walkthrough for beginners in German see [Schritt-für-Schritt-Anleitung](installation-synology-de.md).

## Requirements

- DSM 7.2 with the **Container Manager** package (it creates the shared folder `docker`)
- SSH access (Control Panel › Terminal & SNMP)
- Internet access for the Synology (it downloads the images from GitHub and Docker Hub)
- On the Synology, Docker commands need `sudo`

## 1. Create the project folder

The default location is `/volume1/docker/contracts-manager`. Everything lives in this one folder:

```
/volume1/docker/contracts-manager/
├── docker-compose.yml   the containers (from GitHub)
├── .env                 your settings and passwords
├── db/                  database (PostgreSQL)  →  /var/lib/postgresql/data
├── media/               uploaded files (logos, documents)  →  /app/media
└── backups/             backups
```

```bash
sudo mkdir -p /volume1/docker/contracts-manager/db /volume1/docker/contracts-manager/media /volume1/docker/contracts-manager/backups
```

```bash
sudo chown admin /volume1/docker/contracts-manager
```

(replace `admin` with your user name, so you can create files in the folder without `sudo`)

The app and worker containers do not run as root but as user and group `1000` (configurable with `APP_UID` and `APP_GID` in `.env`). Give that user the media folder, where logos and documents are stored:

```bash
sudo chown -R 1000:1000 /volume1/docker/contracts-manager/media
```

> [!TIP]
> To protect the data if the NAS is stolen, create an **encrypted shared folder** (Control Panel › Shared Folder › Create › *Encrypt this shared folder*, keep the key in a safe place) and use it instead of `docker`: replace `/volume1/docker` by its path in this guide. After a reboot the folder stays locked until you mount it; the containers then deliberately do not start instead of creating an empty database elsewhere (see [operations](operations.md#restart-with-an-encrypted-folder)).

## 2. Download the Compose file and `.env`

```bash
cd /volume1/docker/contracts-manager
```

```bash
curl -fsSLO https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml
```

```bash
curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/.env.example -o .env
```

```bash
chmod 600 .env
```

The second file is the template for your settings and is saved as `.env` right away. `main` fetches the current files; use a version tag (e.g. `v0.2.0`) in the address for the files of one release.

## 3. Configure `.env`

Change at least:

| Variable | Value |
|---|---|
| `SECRET_KEY` | a new key, e.g. from `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | your own password, letters and digits only (it is part of a URL), e.g. from `openssl rand -hex 24` |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | the address the app is reached at, e.g. `contracts.example.com` and `https://contracts.example.com` |
| `APP_PORT` | a free port on the Synology, e.g. `8000` |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | your first administrator, created on the first start (see step 5). Use a strong password, e.g. from `openssl rand -base64 18 \| tr -d '/+='` |
| `APP_BASE_URL` | the public address, e.g. `https://contracts.example.com` |
| `APP_UID`, `APP_GID` | optional: user and group for the containers (default `1000`), must own `media` |

Keep `DEBUG=False`, and `USE_HTTPS=True` when the app is reached through the reverse proxy. Leave `DATA_PATH` and `COMPOSE_FILE` commented out: the data lives in `db` and `media` next to the Compose file, and `COMPOSE_FILE` is for development only.

Email, single sign-on and Paperless are optional – see the [configuration guide](configuration.md).

## 4. Start

```bash
cd /volume1/docker/contracts-manager && sudo docker compose up -d
```

Docker downloads the ready-made image `ghcr.io/brainx9/contracts-manager` (built for `amd64` and `arm64`); nothing is built on the Synology.

On the first start PostgreSQL creates the database in `db`, then the app applies the migrations. Check the status:

```bash
sudo docker compose ps
```

`db` and `app` should be `healthy` after about a minute; then `worker` starts. Logs if something goes wrong:

```bash
sudo docker compose logs app
```

Three containers run. In `docker compose` commands you use the service name, in the DSM Container Manager and with `docker exec` the container name:

| Service | Container | Purpose |
|---|---|---|
| `db` | `contracts-manager-db` | PostgreSQL 17 |
| `app` | `contracts-manager-app` | the Django application, port `APP_PORT` |
| `worker` | `contracts-manager-worker` | background tasks (reminders, AI assistant) |

On older Synology versions the command may be called `docker-compose`.

## The Docker image

`app` and `worker` run the image `ghcr.io/brainx9/contracts-manager` from the GitHub Container Registry. GitHub builds it whenever a new version is released; nothing is built on the Synology.

| | |
|---|---|
| Image | `ghcr.io/brainx9/contracts-manager` |
| Tags | `latest` (newest version) and one per version, e.g. `0.2.0` |
| Platforms | `linux/amd64` (Intel/AMD) and `linux/arm64` (ARM). Other platforms such as 32-bit ARM are not published; [build the image yourself](../README.md#docker-image) there. |
| Database | `pgvector/pgvector:pg17` from Docker Hub |

**Pin a version.** `latest` moves with every release. To decide yourself when to update, set a fixed version in `.env`:

```dotenv
APP_IMAGE_TAG=0.2.0
```

`docker compose pull` then changes nothing until you raise it. Updating and going back: [operations › update](operations.md#update).

**Which version is running?** `sudo docker compose images` shows the tags; the app shows its version under Administration › System.

**Troubleshooting**

| Message | Cause and fix |
|---|---|
| `curl: (22) … 404` when downloading the files | The address is mistyped, or the tag in it does not exist |
| `manifest unknown` / `not found` | The version in `APP_IMAGE_TAG` does not exist (typo, or a leading `v`): use e.g. `0.2.0`, or remove the line for `latest` |
| `container name … is already in use` | An old stack with the same containers still runs: run `sudo docker compose down` in its folder |

## 5. Sign in as the first administrator

The app has no preset account. On the first start it creates the administrator from `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env` (step 3), but only if no superuser exists yet. Check that it worked:

```bash
sudo docker compose logs app | grep -i administrator
```

(`Administrator '…' wurde angelegt.`) Sign in with these values, then change the password in the app (user menu › Settings) and remove the line `ADMIN_PASSWORD` from `.env`. If the log says the password is invalid (too short, too common, only digits), fix it in `.env` and run `sudo docker compose up -d` again. Without the variables you can create the account by hand instead:

```bash
sudo docker compose exec app python manage.py createsuperuser
```

Then log in at `https://<your-address>/`. Further users and groups are managed in the sidebar under **Administration** (see [users and sharing](configuration.md#users-and-sharing)). The Django admin is available to superusers via the user menu (**Django administration**) or at `/admin/`.

## 6. Reverse proxy with HTTPS

Control Panel › Login Portal › Advanced › Reverse Proxy › Create:

| | Protocol | Host name | Port |
|---|---|---|---|
| Source | HTTPS | e.g. `contracts.example.com` | 443 |
| Destination | HTTP | `localhost` | `APP_PORT` from `.env` |

Assign a certificate for the host name under Control Panel › Security › Certificate (e.g. Let's Encrypt).

Django recognises HTTPS by the header `X-Forwarded-Proto: https`. If you get a CSRF error when logging in, add the custom header `X-Forwarded-Proto` with the value `$scheme` in the reverse proxy, and check that `CSRF_TRUSTED_ORIGINS` matches the address exactly (with `https://`, without a trailing slash).

To make sure the app cannot be reached directly, bypassing the proxy, block `APP_PORT` for external access in the Synology firewall (Control Panel › Security › Firewall).

## 7. Daily backup

Control Panel › Task Scheduler › Create › Scheduled Task › User-defined script. User `root`, schedule daily (e.g. 03:00), optionally an email on failure, and this script:

```
PATH=/usr/local/bin:/usr/bin:/bin:$PATH
DIR=/volume1/docker/contracts-manager/backups
FILE="$DIR/contracts-$(date +%Y-%m-%d_%H%M%S).dump"
if docker exec contracts-manager-db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$FILE.partial" && [ -s "$FILE.partial" ]; then
  mv "$FILE.partial" "$FILE" && chmod 600 "$FILE"
  find "$DIR" -maxdepth 1 -name 'contracts-*.dump' -mtime +30 -delete
else
  rm -f "$FILE.partial"
  echo "Backup failed" >&2
  exit 1
fi
```

It writes a compressed dump to `backups/`, only keeps complete files and deletes dumps older than 30 days (the number in the `find` line). Run the task once and check `ls -lh /volume1/docker/contracts-manager/backups`. Restoring: [operations › restore](operations.md#restore). Also copy `backups` and `media` to an external target with **Hyper Backup**.

## Existing installations (folders `app/` and `data/`)

Earlier versions of this guide cloned the repository to `/volume1/contracts/app` and kept the data in `/volume1/contracts/data`. Such installations keep working: their `.env` contains `DATA_PATH`, and the containers keep using the folders they already use.

The public repository started a **new git history** with version 0.1.0, so `git pull` in an old clone no longer works. Either get the Compose file with `curl` as in step 2 (no git needed), or point the clone at the new history (`git fetch origin`, then `git reset --hard origin/main`; your `.env` is not tracked and stays). After the next `docker compose up -d` the containers are only renamed (`contracts-manager-app` etc.); the data stays where it is. A locally built image is replaced by the downloaded one (`sudo docker compose pull`, `sudo docker compose up -d`); the old local image `contracts-manager-app` can be removed with `sudo docker image rm contracts-manager-app`.

To move to the layout above (optional): create a backup, run `sudo docker compose down` in the old `app` folder, create the new folder (steps 1–2), copy the old `.env` to it and remove the `DATA_PATH` (and `BACKUP_DIR`) line, copy `data/db`, `data/media` and `data/backups` into `db`, `media` and `backups` with `sudo cp -a`, start with `sudo docker compose up -d`, check the app, switch the Task Scheduler to the script from step 7, and delete the old folder only when everything works. The German guide lists every command ([Umzug auf die neue Ordnerstruktur](installation-synology-de.md#bestehende-installation-mit-den-ordnern-app-und-data)).

## Next steps

- Set up email and reminders: [configuration › email](configuration.md#email-and-reminders)
- Update, pin a version, restore a backup: [operations](operations.md)
