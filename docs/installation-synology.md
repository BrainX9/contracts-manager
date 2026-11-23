# Installation on a Synology NAS

[← Back to README](../README.md)

This guide sets up Contracts Manager on a Synology NAS with Docker Compose only. One file is all you need: `docker-compose.yml` describes the containers and holds the few settings; everything else is set in the app. Nothing is cloned or built on the Synology; the app comes as a [ready-made Docker image](#the-docker-image). For a detailed walkthrough for beginners in German see [Schritt-für-Schritt-Anleitung](installation-synology-de.md).

## Requirements

- DSM 7.2 with the **Container Manager** package (it creates the shared folder `docker`)
- Internet access for the Synology (it downloads the images from Docker Hub)
- Optional: SSH access (Control Panel › Terminal & SNMP) for the command line; on the Synology, Docker commands need `sudo`

## 1. Create the project folder

The default location is `/volume1/docker/contracts-manager`. Everything lives in this one folder:

```
/volume1/docker/contracts-manager/
├── docker-compose.yml   the containers and settings (from GitHub)
├── db/                  database (PostgreSQL)  →  /var/lib/postgresql/data
├── media/               uploaded files (logos, documents)  →  /app/media
├── config/              generated key, created automatically  →  /config
└── backups/             backups (step 7)
```

In **File Station** open the shared folder `docker`, create the folder `contracts-manager` and inside it the folders `db`, `media` and `backups`. Or with SSH:

```bash
sudo mkdir -p /volume1/docker/contracts-manager/db /volume1/docker/contracts-manager/media /volume1/docker/contracts-manager/backups
```

You do not need to change any owner: on every start the app container gives `media` and `config` to the user the app runs as (`1000:1000`, configurable with `APP_UID` and `APP_GID`) and then drops root.

> [!TIP]
> To protect the data if the NAS is stolen, create an **encrypted shared folder** (Control Panel › Shared Folder › Create › *Encrypt this shared folder*, keep the key in a safe place) and use it instead of `docker`: replace `/volume1/docker` by its path in this guide. After a reboot the folder stays locked until you mount it; the containers then deliberately do not start instead of creating an empty database elsewhere (see [operations](operations.md#restart-with-an-encrypted-folder)).

## 2. Get the Compose file and set the password

Download [`docker-compose.yml`](https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml) (or with SSH: `cd /volume1/docker/contracts-manager && curl -fsSLO https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml`). `main` is the current file; use a version tag (e.g. `v0.9.0`) in the address for the file of one release.

Open it in a text editor and replace `CHANGE_ME` with a password of your own, **letters and digits only** (e.g. from `openssl rand -hex 24`). That is the only required change. Optional settings are already in the file, commented out – remove the `#` to use them:

| Variable | When |
|---|---|
| `ALLOWED_HOSTS` | recommended: the host name the app is reached at, e.g. `contracts.example.com` |
| `CSRF_TRUSTED_ORIGINS` | only if the reverse proxy changes the address, e.g. `https://contracts.example.com` |
| `USE_HTTPS` | `"True"` when the app is reached through the reverse proxy with HTTPS (step 6) |
| `APP_UID`, `APP_GID` | if another user should own `media` and `config`, e.g. your NAS user |
| `SECRET_KEY` | only if you want to set the key yourself; otherwise the app creates one in `config` |

The app is published on port **8010** (`"8010:8000"` under `ports:`); change the left number if that port is taken. The file contains the database password – keep it private, and write a `$` in a value as `$$`. All variables: [environment variables](variables.md).

Email, single sign-on, the AI assistant and Paperless are set up later in the app under **Administration** – see the [configuration guide](configuration.md). For links in emails, enter the public address there under **Email settings › Address of the app**.

## 3. Create the project in Container Manager

**Container Manager › Project › Create**:

- Project name: `contracts-manager`
- Path: `/docker/contracts-manager` (the folder from step 1)
- Source: *Use existing docker-compose.yml* if the file is already in the folder, otherwise *Upload docker-compose.yml* or *Create docker-compose.yml* and paste it

Click **Next** (no web portal needed) until **Done**. Container Manager downloads the images and starts the project.

With SSH instead:

```bash
cd /volume1/docker/contracts-manager && sudo docker compose up -d
```

## 4. Check the start

On the first start PostgreSQL creates the database in `db`, then the app applies the migrations and creates its key in `config`. In Container Manager › Project the project should show three running containers after about a minute. With SSH:

```bash
sudo docker compose ps
```

`db` and `app` should be `healthy`; then `worker` starts. Logs if something goes wrong (Container Manager › Container › `contracts-manager-app` › Log, or):

```bash
sudo docker compose logs app
```

| Service | Container | Purpose |
|---|---|---|
| `db` | `contracts-manager-db` | PostgreSQL 17 |
| `app` | `contracts-manager-app` | the Django application, port 8010 |
| `worker` | `contracts-manager-worker` | background tasks (reminders, AI assistant, Paperless) |

In `docker compose` commands you use the service name, in the Container Manager and with `docker exec` the container name. On older Synology versions the command may be called `docker-compose`.

## The Docker image

`app` and `worker` run the image `brainx9/contracts-manager` from Docker Hub (the same image is also available as `ghcr.io/brainx9/contracts-manager`). GitHub builds it whenever a new version is released; nothing is built on the Synology.

| | |
|---|---|
| Image | `brainx9/contracts-manager` on Docker Hub |
| Tags | `latest` (newest version) and one per version, e.g. `0.9.0` |
| Platforms | `linux/amd64` (Intel/AMD) and `linux/arm64` (ARM). Other platforms such as 32-bit ARM are not published; [build the image yourself](../README.md#docker-image) there. |
| Database | `pgvector/pgvector:pg17` from Docker Hub |

**Pin a version.** `latest` moves with every release. To decide yourself when to update, write a fixed version under `image:` for `app` and `worker`:

```yaml
    image: brainx9/contracts-manager:0.9.0
```

`docker compose pull` then changes nothing until you raise it (with `.env`: `APP_IMAGE_TAG=0.9.0`). Updating and going back: [operations › update](operations.md#update).

**Which version is running?** `sudo docker compose images` shows the tags; the app shows its version under Administration › System.

**Troubleshooting**

| Message | Cause and fix |
|---|---|
| `curl: (22) … 404` when downloading the files | The address is mistyped, or the tag in it does not exist |
| `manifest unknown` / `not found` | The version under `image:` (or in `APP_IMAGE_TAG`) does not exist (typo, or a leading `v`): use e.g. `0.9.0`, or `latest` |
| `Bind mount failed: '…/db' does not exist` | The folder `db` or `media` is missing: create it (step 1) |
| `container name … is already in use` | An old stack with the same containers still runs: run `sudo docker compose down` in its folder |

## 5. Create the first administrator

Open the app in the browser right after the first start (e.g. `http://<synology-ip>:8010/`). A new installation shows **Create administrator**: choose user name, email address and a strong password (e.g. from `openssl rand -base64 18 | tr -d '/+='`). You are then signed in; the page disappears for good. Do this before you set up the reverse proxy for access from the internet – until then, whoever opens the page first becomes administrator.

On the command line it works too:

```bash
sudo docker compose exec app python manage.py createsuperuser
```

Later you log in at `https://<your-address>/`. Further users and groups are managed in the sidebar under **Administration** (see [users and sharing](configuration.md#users-and-sharing)). The Django admin is available to superusers via the user menu (**Django administration**) or at `/admin/`.

## 6. Reverse proxy with HTTPS

Control Panel › Login Portal › Advanced › Reverse Proxy › Create:

| | Protocol | Host name | Port |
|---|---|---|---|
| Source | HTTPS | e.g. `contracts.example.com` | 443 |
| Destination | HTTP | `localhost` | `8010` (the left number under `ports:`) |

Assign a certificate for the host name under Control Panel › Security › Certificate (e.g. Let's Encrypt).

Then set `USE_HTTPS: "True"` and `ALLOWED_HOSTS: "contracts.example.com"` in `docker-compose.yml` (remove the `#`) and apply it: Container Manager › Project › `contracts-manager` › Action › Build (or `sudo docker compose up -d`).

Django recognises HTTPS by the header `X-Forwarded-Proto: https`. If you get a CSRF error when logging in, add the custom header `X-Forwarded-Proto` with the value `$scheme` in the reverse proxy, or set `CSRF_TRUSTED_ORIGINS` to the exact address (with `https://`, without a trailing slash).

To make sure the app cannot be reached directly, bypassing the proxy, block port 8010 for external access in the Synology firewall (Control Panel › Security › Firewall).

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

## Existing installations

**Installed before 0.9.0 (with `.env`):** nothing to do. Your `docker-compose.yml` and `.env` stay as they are and keep working; `docker compose pull` and `docker compose up -d` update the app. On the first start of 0.9.0 the app copies email, single sign-on, AI and Paperless settings from `.env` into Administration once; the log lists the variables you can then remove. The Compose file with `.env` is now called [`docker-compose.env.yml`](../docker-compose.env.yml) on GitHub – download that one if you want the newest version of your file, and keep using your `.env` ([all variables](variables.md)).

**Folders `app/` and `data/`:** earlier versions of this guide cloned the repository to `/volume1/contracts/app` and kept the data in `/volume1/contracts/data`. Such installations keep working: their `.env` contains `DATA_PATH`, and the containers keep using the folders they already use. The public repository started a **new git history** with version 0.1.0, so `git pull` in an old clone no longer works; get the Compose file with `curl` instead (`docker-compose.env.yml`, saved as `docker-compose.yml`). If you keep a clone, add `COMPOSE_FILE=docker-compose.env.yml` to its `.env`: since 0.9.0 `docker-compose.yml` in the repository is the file without `.env`. The German guide lists every command for moving to the layout above ([Umzug auf die neue Ordnerstruktur](installation-synology-de.md#bestehende-installation-mit-den-ordnern-app-und-data)).

## Next steps

- Set up email and reminders: [configuration › email](configuration.md#email-and-reminders)
- Update, pin a version, restore a backup: [operations](operations.md)
