# Configuration

[← Back to README](../README.md)

All settings come from environment variables in `.env` (see [`.env.example`](../.env.example)). After changing `.env`, restart with `sudo docker compose up -d`.

- [First administrator](#first-administrator)
- [Users and sharing](#users-and-sharing)
- [Administration in the web interface](#administration-in-the-web-interface)
- [Email and reminders](#email-and-reminders)
- [Single sign-on with the Synology SSO Server](#single-sign-on-with-the-synology-sso-server)
- [Documents](#documents)
- [Paperless-ngx links](#paperless-ngx-links)
- [AI assistant](#ai-assistant)
- [Language](#language)
- [Security](#security)

## First administrator

The app has no preset account: until a user exists, nobody can sign in. There are two ways to create the first administrator (a superuser).

**From `.env` (on the first start).** Set

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<a strong password>
ADMIN_EMAIL=you@example.com
```

and start the containers. If **no superuser exists yet**, the app creates one with these values (`ADMIN_EMAIL` is optional). Then:

- Later changes to these variables have no effect, and nothing is overwritten. They are ignored as soon as a superuser exists, so existing installations are not affected.
- The password must pass the usual password rules (at least 8 characters, not too common, not only digits). Otherwise no user is created and the log of the `app` container says why (`sudo docker compose logs app`); the password is never logged.
- After the first sign-in change the password in the app (user menu › Settings) and remove the line `ADMIN_PASSWORD` from `.env`. The password is stored in plain text in `.env` (keep the file readable for you only: `chmod 600 .env`).

**By hand**, at any time:

```bash
sudo docker compose exec app python manage.py createsuperuser
```

Further users and groups are managed in the web interface, see below.

## Users and sharing

Every object (contract, contract partner, category, consumer, payment method) has an **owner** and can be shared with further users or groups:

| Role | May |
|---|---|
| Owner | view, edit, delete, change sharing and owner |
| Editor | view and edit |
| Reader | view |

Cost entries and reminders follow their contract. Superusers may do everything.

Default groups, created automatically:

| Group | Meaning |
|---|---|
| **Benutzer** | may create own contracts and master data; without it, a user only sees what was shared with them |
| **Alle** | contains every user automatically; sharing with “Alle” makes an object visible to everyone |
| **Administratoren** | may manage users and groups in the web interface |

**Sharing an object:** on the page of a contract, contract partner, category, consumer or payment method, open **Sharing** and choose exactly one role per user and group: no access, reader or editor. Readers of master data can select it in their own contracts. Sharing a main category does not share its subcategories. Only the owner and superusers see and change sharing. When ownership is handed over, the previous owner automatically becomes an editor (the role can be removed afterwards).

Tip: share categories everybody should use with the group **“Alle”** as readers.

## Administration in the web interface

In the sidebar under **Administration** (`/manage/`), visible to superusers and members of **Administratoren**:

| Area | Content | Who |
|---|---|---|
| Users | search, create with an email invitation, edit, send invitation or password reset link, deactivate, delete | Administrators, superusers |
| Users › Transfer | transfer all objects of a user to another user | superusers only |
| Groups | create, rename, edit members, delete | Administrators, superusers |
| Reminders | reminders of all contracts | superusers only |
| System | version, worker status, email settings, test email, single sign-on | Administrators, superusers |

**Safeguards for administrators**, so they cannot grant themselves more rights: they cannot edit superusers; only superusers grant the group “Administratoren” and the superuser flag; nobody changes their own groups or active status; default groups cannot be renamed or deleted. Note that whoever manages users and groups can still grant indirect access to shared objects via group membership – give the role to trusted people only.

**New users** are created without a local password. With **Send invitation by email** (default) they receive a link to choose their password; links for invitations and password reset are valid for 7 days. Without an invitation, a user can only sign in via single sign-on. Every user is automatically in “Alle”.

**Removing users:** deactivate at any time (data and sharing are kept, sign-in is blocked). A user who owns objects can only be deleted after a superuser has transferred the objects.

The Django admin (`/admin/`) remains available to superusers only.

## Email and reminders

Contracts Manager sends emails via SMTP: reminders, invitations and password reset links. Without SMTP settings, emails are only written to the log.

### SMTP (example: IONOS)

```dotenv
EMAIL_HOST=smtp.ionos.de
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=contracts@your-domain.com
EMAIL_HOST_PASSWORD=<mailbox password>
DEFAULT_FROM_EMAIL=Contracts Manager <contracts@your-domain.com>
APP_BASE_URL=https://contracts.example.com
```

With IONOS the sender (`DEFAULT_FROM_EMAIL`) must belong to the mailbox. Alternatively use port 465 with `EMAIL_USE_TLS=False` and `EMAIL_USE_SSL=True`. `APP_BASE_URL` is needed for links in reminders, which are created in the background without a browser request.

Send a test email from **Administration › System**, or on the command line:

```bash
sudo docker compose exec app python manage.py sendtestemail you@example.com
```

Every user maintains their own email address under **Settings** (unique per account).

### Password reset

On the login page via **Forgot password?**. The link is valid for 7 days and works once. The page does not reveal whether an address exists. Users without a local password (single sign-on only) receive no email.

### Reminders

On a contract page under **Reminder › Set up** (edit rights on the contract required; readers only see it):

| Field | Meaning |
|---|---|
| Reminder active | on/off switch |
| Reminder date | key date, e.g. the last day of the notice period |
| Remind in advance | 0–60 days; from this many days before the date an email is sent **every day**, up to and including the date |
| Reason | cancellation, contract review or other |
| Recipients | users who may view the contract (owner preselected) |
| Note | free text included in the email |

After the reminder date the reminder switches itself off and is shown as done; set a new date to reuse it. Recipients without an email address or without (continued) read access are skipped. Emails use the recipient's language.

The **worker** service (django-q2) sends reminders daily at `REMINDER_SEND_TIME` (default `07:00`). If the worker is down at that time, the run happens once after it starts; missed days are not caught up. **Administration › System** shows the last and next run. To trigger a run manually:

```bash
sudo docker compose exec app python manage.py send_reminders
```

## Single sign-on with the Synology SSO Server

Users can optionally sign in with their Synology account (OpenID Connect via the **SSO Server** package). Local sign-in with a password always remains available, e.g. for the superuser.

### 1. Set up the SSO Server (DSM)

1. Open the **SSO Server** package and enable **OIDC** under the services. The server URL must be reachable via HTTPS, e.g. `https://nas.example.com`.
2. Add an application (type OIDC):
   - Name: e.g. `Contracts Manager`
   - Redirect URI: `https://contracts.example.com/oidc/callback/` (exactly like this, with a trailing slash)
3. Note the **application ID** (client ID) and **application secret** (client secret).

### 2. Add to `.env`

```dotenv
OIDC_ENABLED=True
OIDC_OP_BASE_URL=https://nas.example.com
OIDC_RP_CLIENT_ID=<application ID>
OIDC_RP_CLIENT_SECRET=<application secret>
# Optional: create unknown users on first sign-in (default: False)
# OIDC_CREATE_USERS=False
```

After `sudo docker compose up -d`, the login page shows the button **SSO Login**. Administrators can change its label under **Administration › System** (e.g. “Log in with Synology”); leave the field empty to go back to “SSO Login”.

The SSO Server endpoints (`/webman/sso/…`) are derived from `OIDC_OP_BASE_URL`. The token, user info and key endpoints are called by the container itself, so it must be able to reach that address. If it cannot, override them individually with an internal address (`OIDC_OP_TOKEN_ENDPOINT`, `OIDC_OP_USER_ENDPOINT`, `OIDC_OP_JWKS_ENDPOINT`). `OIDC_OP_AUTHORIZATION_ENDPOINT` must be reachable from the browser.

### Rules

- Users are matched by **user name** (claim `username` of the SSO Server), case-insensitive: Synology user `anna` signs in as Contracts Manager user `anna`.
- With `OIDC_CREATE_USERS=False` (default) only users created beforehand may sign in; others see an error on the login page.
- With `OIDC_CREATE_USERS=True` unknown users are created on first sign-in: groups “Benutzer” and “Alle”, no local password, never superuser or staff.
- Deactivated users cannot sign in via SSO either.
- **Log out** ends the Contracts Manager session only, not the SSO Server session.

## Payment methods

A contract can have a **payment method** (optional), for example PayPal or direct debit. Payment methods are master data like contract partners, categories and consumers: maintain them under **Master data › Payment methods**. Do not confuse them with the *payment interval* of a cost entry (monthly, yearly, …).

- **Create and edit:** users of the group “Benutzer” may create payment methods; the creator becomes the owner. The name is unique per owner.
- **Sharing** works as for all master data: readers may select the payment method in their own contracts, editors may also change it. The contract page links the payment method only for users who may view it; others see just the name.
- **Icon** (optional): PNG, JPEG or WebP up to 2 MB, no SVG. It is reduced to 256 pixels and stored without metadata. Icons are only delivered to users who may view the payment method.
- **Delete:** only possible while no contract uses the payment method.
- **Contract list:** the filter “Payment method” appears as soon as a visible contract has one.

**Defaults.** *PayPal*, *American Express*, *Visa* and *Lastschrift* (direct debit) are created once, as soon as there is a superuser: on the first start of a new installation, after the update from 0.1.0, and after importing a backup that has no payment methods yet. They are ordinary records: the first superuser owns them and the group “Alle” may read them, so every user can select them. Superusers can rename them, replace the icon or delete them like any other value; a deleted default is not created again. The icons are simple tiles drawn for this project, not the logos of the companies.

## Documents

Every contract has a **Documents** card listing both kinds of documents together. An icon shows where each one is:

| Icon | Source | Content |
|---|---|---|
| Hard drive (blue) | **Upload** | A file stored in Contracts Manager |
| Archive box (green) | **Paperless** | A link to a document in Paperless-ngx (name and document ID) |

- **Allowed files:** PDF, JPG, PNG, WebP, DOCX, XLSX, ODT, ODS, TXT and EML, up to **20 MB** each. PDFs and images open in a new tab, other files are downloaded.
- **Storage:** `data/media/documents/<year>/<reference>/<file name>`, e.g. `documents/2026/20260001/invoice.pdf`. The year is the year the contract was created (first four digits of its reference). The original file name is kept; if it already exists in the folder, a counter is added (`invoice_2.pdf`, `invoice_3.pdf`, …).
- **Access** follows the contract: readers may open and download documents, editors may add, rename and delete them. Files are only delivered after this check, never as public files.
- Deleting a document of type Upload also deletes its file. Deleting a Paperless link leaves the document in Paperless untouched.
- **Cost entries** can be linked to one document of the same contract (e.g. the invoice or the notice of a price change).

If uploads of larger files fail with error **413**, the reverse proxy limits the request size; allow at least 21 MB there.

## Paperless-ngx links

Contract partners and contract documents can point to [Paperless-ngx](https://docs.paperless-ngx.com/):

- **Contract partner:** correspondent ID
- **Document of a contract:** document ID, e.g. the policy, the invoice or the price adjustment letter (see [Documents](#documents))

The IDs appear in the Paperless address bar, e.g. `…/documents/123/details` for document 123. For correspondents, filter the document list by the correspondent and read the ID from the address.

To turn the IDs into links, set the Paperless address (without a trailing slash) and restart:

```dotenv
PAPERLESS_URL=https://paperless.example.com
```

The links open Paperless in a new tab, where you sign in as usual. Contracts Manager does not access Paperless; it only creates links. Without `PAPERLESS_URL` the IDs are stored but not linked.

## AI assistant

Contracts Manager has an optional chat assistant that answers questions about your contracts (“How much do I pay for insurance per month?”). It is switched off until you configure a provider.

```dotenv
AI_ENABLED=True
# "anthropic": Claude
# "openai": OpenAI and OpenAI-compatible providers (IONOS AI Model Hub, Mistral, Ollama, …)
AI_PROVIDER=anthropic
AI_API_KEY=<your API key>
AI_MODEL=claude-sonnet-5-5
# Optional for Claude: thinking depth low | medium | high (empty = model default)
AI_EFFORT=medium
# Only for OpenAI-compatible providers other than OpenAI:
# AI_BASE_URL=https://<provider>/v1
```

| Variable | Default | Purpose |
|---|---|---|
| `AI_EFFORT` | model default | Claude only: how much the model thinks before answering. `low` or `medium` is usually enough for questions and costs less. |
| `AI_MAX_TOKENS` | `16000` | Maximum length of one answer, including the model's thinking |
| `AI_MAX_TOOL_ROUNDS` | `8` | Maximum number of tool rounds per answer |
| `AI_TIMEOUT` | `120` | Seconds to wait for the provider |

**Claude specifics:** current Claude models always think before answering. Contracts Manager stores these thinking blocks with the conversation and sends them back unchanged, keeps the system prompt of a conversation fixed, and never changes the tool list within a conversation – Claude rejects changed histories otherwise. When calling Anthropic directly (no `AI_BASE_URL`), a request the model declines for safety reasons is automatically retried on Anthropic's recommended fallback model where possible; otherwise the chat shows that the request was declined.

Restart with `sudo docker compose up -d`. Then add the users who may use the assistant to the group **“KI”** (Administration › Groups); superusers can always use it. The assistant appears in the sidebar, and **Administration › System** shows its status.

**What it can do:** search contracts, read details, cost history, documents and reminders, calculate totals, and look up contract partners, categories and consumers – always only what the user is allowed to see.

**New contracts from documents (0.8b):** attach a file in the chat (paper clip; PDF, JPG, PNG, WebP, TXT or EML, up to 20 MB). The assistant

1. reads the text **locally** – PDF text layer, otherwise text recognition with Tesseract (German and English) for scans and photos; for emails the subject, sender, text and attached PDFs;
2. stores the relevant facts it found (partner, numbers, dates, amount, term …) with the file in your database;
3. checks whether a matching contract or contract partner already exists and tells you;
4. proposes what to do: attach the document to the existing contract, or create the missing partner, category or consumer and then the new contract with its first cost entry and the document.

Every proposal appears as a card listing **all values** that would be stored, with **Approve** and **Reject**. Nothing is saved before you approve; the values are checked again at that moment, with your permissions, and you become the owner of new objects. The assistant has no tools to delete anything.

Only if no text can be recognised locally is the file itself sent to the model (Claude only, PDF and images up to 10 MB). The file stays in the conversation until you delete it; once attached to a contract, a copy is stored as a regular contract document. The assistant cannot search the internet; it tells you when data such as the partner's address is missing.

**Privacy:** the question and the contract data needed for the answer are sent to the configured provider. Conversations are stored in your own database; each user sees only their own and can delete them. If you want to keep data in Germany or on your own hardware, use a provider hosted there (e.g. IONOS) or a local model (Ollama).

Answers are generated by the background worker, so the `worker` container must be running.

## Language

The interface is available in German (default) and English. Each user chooses the language under **Settings**; it applies on all devices, in the Django admin and in emails. The login page uses German.

## Security

- **Failed logins:** after 5 wrong passwords for the same user name from the same IP address, logging in is locked for 15 minutes ([django-axes](https://github.com/jazzband/django-axes)). Configurable with `AXES_FAILURE_LIMIT` and `AXES_COOLOFF_MINUTES`; locks can be lifted in the Django admin (see [operations](operations.md#locked-login)). Behind the reverse proxy (`USE_HTTPS=True`) the client address is taken from `X-Forwarded-For`.
- **HTTPS:** with `USE_HTTPS=True`, session and CSRF cookies are only sent via HTTPS. HSTS can be enabled with `SECURE_HSTS_SECONDS` or in the Synology reverse proxy.
- **Containers** run as a non-root user (`APP_UID`/`APP_GID`, default `1000`); the code in the image is read-only.
- **Uploads:** logos are checked (PNG, JPEG, WebP, max. 2 MB), re-encoded without metadata and only delivered to users who may view the contract partner.
- **Secrets** only in `.env`, never in the repository.
