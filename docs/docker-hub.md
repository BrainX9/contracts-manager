<!--
  Text for the "Repository overview" on Docker Hub (hub.docker.com/r/brainx9/contracts-manager).
  Docker Hub does not read this file automatically: copy everything below this comment
  into the overview there after a release. Links are absolute, because Docker Hub shows the text outside GitHub.
-->
# Contracts Manager

**Keep track of every contract, subscription and insurance policy in your household – self-hosted, private and shareable.**

![Overview with monthly and yearly totals, costs by category and the most expensive contracts](https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docs/screenshots/dashboard.png)

Source code, documentation and issues: **[github.com/BrainX9/contracts-manager](https://github.com/BrainX9/contracts-manager)**

## What it does

Electricity, internet, mobile, insurance, streaming, the gym – most households have a dozen or more running contracts. Contracts Manager brings them together in one place:

- See **what you pay per month and per year**, and where the money goes.
- Record **price changes over time**; the current amount is picked by date.
- Keep track of every contract with a **status traffic light** – active, notice sent, cancelled, archived – and the date a cancellation takes effect. Archived contracts leave the overview and the totals.
- Link **predecessor and successor** contracts, e.g. when you change tariff or insurer.
- Get an **email reminder** before a notice period ends.
- Attach **documents** to contracts: upload files or use **Paperless-ngx** – via its API, Paperless documents open inside the app and uploads can be transferred to Paperless.
- **Share** contracts with family members – read-only or with edit rights.
- Optional **AI assistant** (Claude, **Mistral AI**, OpenAI, an OpenAI-compatible provider such as IONOS, or **Ollama** with local models): ask questions about your contracts or let it propose a new contract from an uploaded document. Changes only after your approval.
- **Scans and photos** are read by local text recognition (Tesseract) or an optional **document AI** – an OCR or vision model, e.g. **Mistral OCR**, a model at IONOS or in Ollama.
- **Notes with Markdown** on contracts, contract partners and consumers.
- **Email, single sign-on (OpenID Connect, e.g. Synology SSO Server) and the AI assistant are set up in the app**, with a test button and without a restart.
- **Backup and import** of all data and files as a single ZIP file.
- Superusers see the installed version and a **hint when an update is available**.
- Interface in **English and German**, light and dark mode, works on the phone.
- All assets served locally – no CDN, no tracking.

## Tags

| Tag | Meaning |
|---|---|
| `latest` | newest release |
| `0.8.0`, `0.7.0`, … | one tag per version |

Platforms: `linux/amd64` and `linux/arm64`. The image is built by GitHub Actions on every version tag, after the tests passed. The same image is also available as `ghcr.io/brainx9/contracts-manager`.

## Quick start

The image needs a PostgreSQL database and a background worker, so it is started with **Docker Compose**, not with a single `docker run`. Choose one of three Compose files:

| File | Settings | For |
|---|---|---|
| [`docker-compose.yml`](https://github.com/BrainX9/contracts-manager/blob/main/docker-compose.yml) | in a separate `.env` file | the usual setup |
| [`docker-compose.no-env.yml`](https://github.com/BrainX9/contracts-manager/blob/main/docker-compose.no-env.yml) | **no `.env`** – all settings in the file, optional ones commented out | one file to paste, e.g. into the Synology Container Manager |
| [`docker-compose.no-env-minimal.yml`](https://github.com/BrainX9/contracts-manager/blob/main/docker-compose.no-env-minimal.yml) | **no `.env`** – only the required values | the quickest start |

### Without a `.env` file

```bash
mkdir contracts-manager && cd contracts-manager
curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.no-env-minimal.yml -o docker-compose.yml
```

Open `docker-compose.yml` and replace every `CHANGE_ME` (secret key, database password). For HTTPS behind a reverse proxy set `USE_HTTPS: "True"`. A `$` in a value must be written as `$$`. Then:

```bash
mkdir db media
# On Linux hosts only: the containers run as user 1000, not root
sudo chown -R 1000:1000 media
docker compose up -d
```

### With a `.env` file

```bash
mkdir contracts-manager && cd contracts-manager
curl -fsSLO https://raw.githubusercontent.com/BrainX9/contracts-manager/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/BrainX9/contracts-manager/main/.env.example -o .env
```

Edit `.env`: set `SECRET_KEY` and `POSTGRES_PASSWORD`, and for a local test also

```dotenv
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000
USE_HTTPS=False
```

Then create the folders and start as above (`mkdir db media`, `docker compose up -d`).

Open <http://localhost:8000/>. A new installation asks for the **first administrator** in the browser – create it right away, before the app is reachable from the internet.

## What runs

| Container | Image | Purpose |
|---|---|---|
| `contracts-manager-app` | `brainx9/contracts-manager` | web application (gunicorn, port 8000) |
| `contracts-manager-worker` | `brainx9/contracts-manager` | background jobs: reminders, update check, AI assistant |
| `contracts-manager-db` | `pgvector/pgvector:pg17` | PostgreSQL 17 |

Data stays outside the containers, next to the Compose file:

| Folder on the host | In the container | Content |
|---|---|---|
| `db/` | `/var/lib/postgresql/data` | database |
| `media/` | `/app/media` | uploads, logos, documents |

The containers run as user `1000:1000` (not root). Database migrations are applied automatically on every start.

## Configuration

There are two places for settings:

- **Environment variables** (in `.env` or directly in the Compose file) hold what Docker and the database need before the app runs.
- **Administration** in the app (superusers only) holds email, single sign-on and the AI assistant. Changes apply immediately, without a restart.

### Required variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | secret key of Django, e.g. from `openssl rand -hex 32` |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | database |

### Optional variables (selection)

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_HOSTS` | every address | recommended: host names the app is reached at, comma separated |
| `CSRF_TRUSTED_ORIGINS` | – | only if the reverse proxy changes the address or the scheme |
| `USE_HTTPS` | `False` | `True` behind a reverse proxy with HTTPS |
| `APP_PORT` | `8000` | published port (`.env` setup) |
| `APP_IMAGE_TAG` | `latest` | image version, e.g. `0.8.0` to stay on one version |
| `APP_UID`, `APP_GID` | `1000` | user and group the containers run as |

The complete list, and the older variables for email, single sign-on and the AI assistant that still work but should no longer be used: [README, section “Configuration”](https://github.com/BrainX9/contracts-manager#configuration).

## Update

Create a backup first, then:

```bash
docker compose pull
docker compose up -d
```

To stay on one version, use a fixed tag such as `brainx9/contracts-manager:0.8.0`. Superusers see a hint in the app when a new version is out. Release notes: [GitHub releases](https://github.com/BrainX9/contracts-manager/releases).

## Documentation

- [Installation on a Synology NAS](https://github.com/BrainX9/contracts-manager/blob/main/docs/installation-synology.md) (reverse proxy with HTTPS, daily backup)
- [Schritt-für-Schritt-Anleitung Synology (deutsch)](https://github.com/BrainX9/contracts-manager/blob/main/docs/installation-synology-de.md)
- [Configuration](https://github.com/BrainX9/contracts-manager/blob/main/docs/configuration.md)
- [Operations](https://github.com/BrainX9/contracts-manager/blob/main/docs/operations.md) (updates, backup and restore)
- [Development](https://github.com/BrainX9/contracts-manager/blob/main/docs/development.md)

## Support

Questions and bug reports: [GitHub issues](https://github.com/BrainX9/contracts-manager/issues).

## License

[MIT](https://github.com/BrainX9/contracts-manager/blob/main/LICENSE) © 2026 BrainX9
