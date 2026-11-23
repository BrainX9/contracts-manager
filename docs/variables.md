# Environment variables

Contracts Manager needs only a few settings before it starts; everything else is set in the app under **Administration** (superusers). This page lists every variable, in four groups:

| Group | Meaning |
|---|---|
| [Required](#required) | must be set, otherwise the app does not start |
| [Optional](#optional) | have a sensible default; recommended for a production setup |
| [Advanced](#advanced) | fine tuning, rarely needed |
| [Archived](#archived) | no longer read since 0.9.0; the app copied their values once into Administration |

## Where to set them

**Without a `.env` file** (the default [`docker-compose.yml`](../docker-compose.yml)): the values are in the block `x-app-settings` at the top of the file, which app and worker share. Add a line there, for example

```yaml
x-app-settings: &app-settings
  POSTGRES_DB: &db-name "contracts_db"
  POSTGRES_USER: &db-user "contracts_user"
  POSTGRES_PASSWORD: &db-password "MySecretPassword1"
  DJANGO_LOG_LEVEL: "INFO"        # an advanced variable
```

and run `docker compose up -d` again. Write a `$` in a value as `$$`. The published port is the left number under `ports:` (`"8010:8000"`), the image version is in `image:`.

**With a `.env` file** ([`docker-compose.env.yml`](../docker-compose.env.yml), the file of installations before 0.9.0): copy [`.env.example`](../.env.example) to `.env` next to the Compose file and set the values there. Start with `docker compose -f docker-compose.env.yml up -d`, or put `COMPOSE_FILE=docker-compose.env.yml` into `.env` so that `docker compose up -d` is enough.

## Required

| Variable | Purpose |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER` | Name of the database and its user. The defaults in the Compose file (`contracts_db`, `contracts_user`) are fine. |
| `POSTGRES_PASSWORD` | Password of the database user. Use letters and digits only. It is set on the first start; changing it later needs a change in the database as well. |

## Optional

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_HOSTS` | every address | *Recommended.* Host names the app is reached at, comma separated, e.g. `contracts.example.com`. Without it the app accepts every address; links in emails use the address of the app from the email settings. |
| `CSRF_TRUSTED_ORIGINS` | – | Full addresses incl. `http://` or `https://`. Only needed if a reverse proxy changes the address or the scheme. |
| `USE_HTTPS` | `False` | `True` behind a reverse proxy with HTTPS. |
| `APP_UID`, `APP_GID` | `1000` | User and group the app runs as. On every start the container gives the folders `media` and `config` to this user, then drops root. Set them if another user should own the files, e.g. your NAS user. |
| `SECRET_KEY` | generated | Secret key of Django (sessions, password reset links). Without it the app creates a random key on the first start and stores it in the folder `config` (`config/secret_key`, readable only by the app user). If that file is lost, a new key is created: everyone is signed out and open invitation and reset links stop working; no data is lost. To set your own: `openssl rand -hex 32`. |

## Advanced

| Variable | Default | Purpose |
|---|---|---|
| `DEBUG` | `False` | Only for development. |
| `APP_PORT` | `8010` | Published port on the host (`.env` setup; without `.env` change the left number under `ports:`). |
| `APP_IMAGE_TAG` | `latest` | Version of the Docker image (`.env` setup), e.g. `0.9.0` to stay on one version; without `.env` change `image:`. |
| `DATA_PATH` | folder of the Compose file | Parent folder of `db`, `media` and `config` (`.env` setup, for older installations). |
| `AI_EFFORT` | model default | Thinking depth for Claude: `low`, `medium` or `high`. |
| `AI_MAX_TOKENS` | `16000` | Maximum length of one answer of the AI assistant. |
| `AI_MAX_TOOL_ROUNDS` | `8` | Tool rounds per answer. |
| `AI_TIMEOUT` | `120` | Timeout of a request to the AI provider in seconds. |
| `AXES_FAILURE_LIMIT`, `AXES_COOLOFF_MINUTES` | `5`, `15` | Lockout after failed logins (per user name and IP address). |
| `SECURE_HSTS_SECONDS` | `0` | HSTS in seconds, only with `USE_HTTPS`; off by default because a reverse proxy can set it. |
| `DJANGO_LOG_LEVEL` | `WARNING` | Log level in `docker compose logs`: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `GUNICORN_CMD_ARGS` | see image | Replaces the gunicorn options of the image (bind address, workers, timeout). |
| `BACKUP_DIR`, `BACKUP_RETENTION_DAYS` | – | Only for `scripts/backup.sh` (installations with a repository): target folder and days to keep database dumps. |

Set by the Compose file or the image, not by you: `DATABASE_URL` (built from the `POSTGRES_*` values if missing), `MEDIA_ROOT` (`/app/media`) and `CONFIG_DIR` (`/config`, the folder `config`).

## Archived

Since 0.9.0 these settings live only in the app. If one of the variables is still set, the app copies its value **once** into Administration on the first start of 0.9.0 (only into empty fields) and ignores the variable afterwards; the log of the app lists the ones you can remove.

| Variable | Now in the app under |
|---|---|
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Administration › Email settings: SMTP server, encryption, user, password, sender |
| `APP_BASE_URL` | Administration › Email settings: address of the app, used for links in emails |
| `REMINDER_SEND_TIME` | Administration › Email settings: daily time for reminders (default `07:00`) |
| `OIDC_ENABLED`, `OIDC_OP_BASE_URL`, `OIDC_RP_CLIENT_ID`, `OIDC_RP_CLIENT_SECRET`, `OIDC_CREATE_USERS` | Administration › Single sign-on: on/off, SSO server, client ID, client secret, create unknown users |
| `OIDC_USERNAME_CLAIM`, `OIDC_RP_SCOPES`, `OIDC_RP_SIGN_ALGO` | Administration › Single sign-on › Advanced |
| `OIDC_OP_AUTHORIZATION_ENDPOINT`, `OIDC_OP_TOKEN_ENDPOINT`, `OIDC_OP_USER_ENDPOINT`, `OIDC_OP_JWKS_ENDPOINT` | Administration › Single sign-on › Advanced: endpoints for providers other than the Synology SSO Server |
| `AI_ENABLED`, `AI_PROVIDER`, `AI_API_KEY`, `AI_MODEL`, `AI_BASE_URL` | Administration › AI settings |
| `PAPERLESS_URL` | Administration › Paperless (mode “Links only”) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL` | No longer used since 0.8.0: a new installation asks for the first administrator in the browser. Existing administrators keep working. |
