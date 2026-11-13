# Development

[← Back to README](../README.md)

## Local setup

Create `.env` from `.env.example` and set for development, in particular:

```dotenv
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000
USE_HTTPS=False
COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml
```

Then start:

```bash
docker compose up --build
```

With `COMPOSE_FILE`, Compose loads `docker-compose.dev.yml` automatically: the image is built locally from the `Dockerfile` (as `contracts-manager-app`) instead of being downloaded, the database lives in the Docker volume `postgres_data`, the code is mounted and gunicorn runs with `--reload`, so code changes take effect without a restart. After changing background tasks, restart the worker (`docker compose restart worker`). The containers are named `contracts-manager-app`, `contracts-manager-db` and `contracts-manager-worker`; `docker compose` commands use the service names `app`, `db` and `worker`.

The interface runs at <http://localhost:8000/>, the Django admin at <http://localhost:8000/admin/>.

```bash
docker compose exec app python manage.py createsuperuser
```

Instead of `createsuperuser` you can set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env`: the first start then creates the administrator (see [configuration](configuration.md#first-administrator)). The start script `docker/entrypoint.sh` is part of the image, so rebuild after changing it (`docker compose up --build`).

Without SMTP settings, emails (reminders, invitations, password reset) are printed to the `app` or `worker` log.

## Tests

```bash
docker compose exec app python manage.py test
```

The suite covers the core logic (current amount, totals, reference numbers), permissions per role, all pages, reminders, email, single sign-on and the administration area.

`config/tests.py` compares the variables read in `config/settings.py` and `docker-compose.yml` with the table in the README and with `.env.example`. When you add a variable, document it in both places. This test needs the README and is skipped inside the Docker image.

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

- **A tag `v…`** (e.g. `v0.2.0`) builds the image, runs the tests inside it and, if they pass, publishes it for `amd64` and `arm64` as `ghcr.io/brainx9/contracts-manager:<version>` and `:latest`.
- **Run manually** (Actions › Docker image › Run workflow) builds and tests only; nothing is published.

To release a version: set `APP_VERSION` in `config/settings.py`, merge into `main`, then create and push the tag:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

### Package visibility and Docker Hub

The image is published to the GitHub Container Registry. If `docker compose pull` asks for a login, open the package on GitHub (your profile › *Packages* › `contracts-manager` › *Package settings* › *Change package visibility*) and make it **Public**; the visibility of a package is independent of the one of the repository.

To publish to Docker Hub as well, create the secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` in the repository, uncomment the Docker Hub step in the workflow and add the Docker Hub image name under `images` in the metadata step.
