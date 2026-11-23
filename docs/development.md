# Development

[← Back to README](../README.md)

## Local setup

Create `.env` from `.env.example` and set for development, in particular:

```dotenv
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000
USE_HTTPS=False
APP_PORT=8000
COMPOSE_FILE=docker-compose.env.yml:docker-compose.dev.yml
```

Development uses the Compose file with `.env` (`docker-compose.env.yml`); `docker-compose.yml` is the file without `.env` for installations.

Then start:

```bash
docker compose up --build
```

With `COMPOSE_FILE`, Compose loads `docker-compose.dev.yml` automatically: the image is built locally from the `Dockerfile` (as `contracts-manager-app`) instead of being downloaded, the database lives in the Docker volume `postgres_data`, the code is mounted and gunicorn runs with `--reload`, so code changes take effect without a restart. After changing background tasks, restart the worker (`docker compose restart worker`). The containers are named `contracts-manager-app`, `contracts-manager-db` and `contracts-manager-worker`; `docker compose` commands use the service names `app`, `db` and `worker`.

The interface runs at <http://localhost:8000/>, the Django admin at <http://localhost:8000/admin/>.

On the first visit of an empty database the app asks for the first administrator in the browser (see [configuration](configuration.md#first-administrator)); alternatively:

```bash
docker compose exec app python manage.py createsuperuser
```

The start script `docker/entrypoint.sh` is part of the image, so rebuild after changing it (`docker compose up --build`). The containers start as root, the script gives `media` and `config` to `APP_UID:APP_GID` and then runs everything as that user (`setpriv`); `manage.py` does the same when called as root, e.g. with `docker compose exec app python manage.py …`. The generated `SECRET_KEY` (only without `SECRET_KEY` in `.env`) lands in `./config-data`.

Without SMTP settings, emails (reminders, invitations, password reset) are printed to the `app` or `worker` log.

## Tests

```bash
docker compose exec app python manage.py test
```

The suite covers the core logic (current amount, totals, reference numbers), permissions per role, all pages, reminders, email, single sign-on and the administration area.

`config/tests.py` checks that every variable belongs to one of the four groups (required, optional, advanced, archived), that [`docs/variables.md`](variables.md) lists all of them, the README the required and optional ones, `.env.example` all but the archived ones, and that `docker-compose.yml` contains the required and optional ones and nothing from a `.env`. When you add a variable, put it into a group there and document it. This test needs the README and is skipped inside the Docker image. The test runner (`config.test_runner`) removes the archived variables from the environment, so tests do not depend on your `.env`.

## Translations

Texts in code and templates are English source strings (`gettext`, `{% translate %}`); the German translation lives in `locale/de/LC_MESSAGES/django.po`. German is the default; each user can switch under **Settings**.

After adding or changing texts, update the catalog, translate the new entries (`msgstr ""`) and restart (translations are compiled on start):

```bash
docker compose exec app python manage.py makemessages -l de --ignore "venv/*" --ignore "data/*" --ignore "staticfiles/*" --no-obsolete
```

```bash
docker compose restart app
```

## Project structure

| Path | Purpose |
|---|---|
| `config/` | Django project: settings (all from environment variables), URLs, admin site for superusers only |
| `contracts/` | Domain logic: models, migrations, permissions (`permissions.py`), owner backend, logos, email (`mail.py`, `templates/emails/`), reminders (`reminders.py`, `tasks.py`), Django admin |
| `web/` | Web interface: views, forms, templates, HTMX, Bootstrap and icons in `static/web/vendor/`; administration area in `manage_views.py` |
| `locale/de/` | German translation |
| `docker/entrypoint.sh` | Applies migrations and compiles translations on start |
| `scripts/backup.sh` | Database backup with `pg_dump` |
| `docs/` | Guides and screenshots |
| `AGENTS.md` | Working rules and specification for the AI-assisted development (German) |

## Conventions

- Strictly phase by phase: plan first, then small steps with tests.
- Identifiers, paths, URL names, templates and CSS classes in English; code comments and commit messages in German; README and `docs/` in English.
- Django conventions first: built-in auth, admin, forms and ORM instead of custom replacements.
- No secrets in the code; configuration only via environment variables.
- Permissions only via `contracts.permissions` (`can_view`, `can_change`, `visible_to`, …).

## Releases and the Docker image

The workflow [`.github/workflows/docker-image.yml`](../.github/workflows/docker-image.yml) builds the image on GitHub:

- **A tag `v…`** (e.g. `v0.3.0`) builds the image, runs the tests inside it and, if they pass, publishes it for `amd64` and `arm64` as `brainx9/contracts-manager:<version>` and `:latest` on Docker Hub, and under the same tags as `ghcr.io/brainx9/contracts-manager`.
- **Run manually** (Actions › Docker image › Run workflow) builds and tests only; nothing is published.

To release a version: set `APP_VERSION` in `config/settings.py`, merge into `main`, then create and push the tag:

```bash
git tag v0.3.0 && git push origin v0.3.0
```

### Docker Hub and the GitHub Container Registry

Since 0.3.0 the Compose file downloads the image from Docker Hub; the GitHub Container Registry gets the same image as a mirror, so installations with an older Compose file keep working.

**Docker Hub** needs two secrets in the GitHub repository (*Settings › Secrets and variables › Actions › New repository secret*):

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | the Docker Hub user name |
| `DOCKERHUB_TOKEN` | an access token from Docker Hub (*Account settings › Personal access tokens*, permission *Read & Write*), not the password |

The repository on Docker Hub is created by the first push; it is public by default. Without the secrets the workflow stops at the login and publishes nothing, neither to Docker Hub nor to the GitHub Container Registry. In a fork, change `DOCKERHUB_IMAGE` in the workflow to your own account.

**GitHub Container Registry:** if `docker pull ghcr.io/…` asks for a login, open the package on GitHub (your profile › *Packages* › `contracts-manager` › *Package settings* › *Change package visibility*) and make it **Public**; the visibility of a package is independent of the one of the repository.
