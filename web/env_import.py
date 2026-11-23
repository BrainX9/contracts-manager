"""
Einstellungen aus der .env einmalig in die Verwaltung übernehmen
(Version 0.9.0).

KI-Assistent, E-Mail, Single Sign-on und Paperless werden seit 0.9.0 nur noch
unter Verwaltung gepflegt. Stehen die alten Variablen noch in der .env bzw.
der Compose-Datei, übernimmt ``run`` sie einmalig in ``web.SiteSettings``:
nur Variablen, die wirklich gesetzt sind, und nur in Felder, die in der
Verwaltung noch leer sind. Welche Gruppen übernommen sind, steht in
``SiteSettings.env_imported`` (z. B. "ai,email,paperless,sso"); danach
werden die Variablen nie wieder gelesen, das Protokoll weist bei jedem Start
darauf hin.

``run`` läuft nach jeder Migration, also bei jedem Start des Containers.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)


def on_off(value):
    return "on" if value else "off"


def security(values):
    """EMAIL_USE_SSL/EMAIL_USE_TLS als Verschlüsselung der Verwaltung."""
    if values.get("EMAIL_USE_SSL"):
        return "ssl"
    if "EMAIL_USE_TLS" in values:
        return "starttls" if values["EMAIL_USE_TLS"] else "none"
    return "none" if "EMAIL_USE_SSL" in values else ""


def strip_url(value):
    return str(value or "").strip().rstrip("/")


# Gruppe -> [(Name in settings, Name der Variablen, Feld in SiteSettings, Umwandlung)]
# Die Umwandlung bekommt den Wert aus settings (schon mit Typ).
FIELDS = {
    "ai": [
        ("AI_ENABLED", "AI_ENABLED", "ai_enabled", on_off),
        ("AI_PROVIDER", "AI_PROVIDER", "ai_provider", str),
        ("AI_MODEL", "AI_MODEL", "ai_model", str),
        ("AI_BASE_URL", "AI_BASE_URL", "ai_base_url", strip_url),
        ("AI_API_KEY", "AI_API_KEY", "ai_api_key", str),
    ],
    "email": [
        ("EMAIL_HOST", "EMAIL_HOST", "email_host", str),
        ("EMAIL_PORT", "EMAIL_PORT", "email_port", int),
        ("EMAIL_HOST_USER", "EMAIL_HOST_USER", "email_user", str),
        ("EMAIL_HOST_PASSWORD", "EMAIL_HOST_PASSWORD", "email_password", str),
        ("DEFAULT_FROM_EMAIL", "DEFAULT_FROM_EMAIL", "email_sender", str),
        ("APP_BASE_URL", "APP_BASE_URL", "app_base_url", strip_url),
        ("REMINDER_SEND_TIME", "REMINDER_SEND_TIME", "reminder_send_time", str),
    ],
    "sso": [
        ("OIDC_ENABLED", "OIDC_ENABLED", "sso_enabled", on_off),
        ("OIDC_CREATE_USER", "OIDC_CREATE_USERS", "sso_create_users", on_off),
        ("OIDC_OP_BASE_URL", "OIDC_OP_BASE_URL", "sso_base_url", strip_url),
        ("OIDC_RP_CLIENT_ID", "OIDC_RP_CLIENT_ID", "sso_client_id", str),
        ("OIDC_RP_CLIENT_SECRET", "OIDC_RP_CLIENT_SECRET", "sso_client_secret", str),
        ("OIDC_USERNAME_CLAIM", "OIDC_USERNAME_CLAIM", "sso_username_claim", str),
        ("OIDC_RP_SCOPES", "OIDC_RP_SCOPES", "sso_scopes", str),
        ("OIDC_RP_SIGN_ALGO", "OIDC_RP_SIGN_ALGO", "sso_sign_algo", str),
        ("OIDC_OP_AUTHORIZATION_ENDPOINT", "OIDC_OP_AUTHORIZATION_ENDPOINT", "sso_authorization_endpoint", str),
        ("OIDC_OP_TOKEN_ENDPOINT", "OIDC_OP_TOKEN_ENDPOINT", "sso_token_endpoint", str),
        ("OIDC_OP_USER_ENDPOINT", "OIDC_OP_USER_ENDPOINT", "sso_user_endpoint", str),
        ("OIDC_OP_JWKS_ENDPOINT", "OIDC_OP_JWKS_ENDPOINT", "sso_jwks_endpoint", str),
    ],
    "paperless": [
        ("PAPERLESS_URL", "PAPERLESS_URL", "paperless_url", strip_url),
    ],
}

# Alle Variablen, die seit 0.9.0 nicht mehr ausgewertet werden
VARIABLES = sorted(
    {variable for fields in FIELDS.values() for _setting, variable, _field, _convert in fields}
    | {"EMAIL_USE_TLS", "EMAIL_USE_SSL"}
)


def site_values(values):
    """
    Felder der Verwaltung zu Werten im Stil von settings (Name -> Wert, nur
    gesetzte Namen). Leere Werte bleiben weg, außer bei Schaltern.
    """
    result = {}
    for fields in FIELDS.values():
        for setting, _variable, field, convert in fields:
            if setting not in values:
                continue
            value = convert(values[setting])
            if value != "" and value is not None:
                result[field] = value
    mail_security = security(values)
    if mail_security:
        result["email_security"] = mail_security
    if result.get("ai_provider") and result["ai_provider"] not in {"openai", "anthropic", "ollama", "mistral"}:
        del result["ai_provider"]
    if result.get("paperless_url"):
        result["paperless_mode"] = "links"
    return result


def environment_values():
    """Werte der Variablen, die tatsächlich gesetzt sind (nicht leer)."""
    present = {name for name in VARIABLES if os.environ.get(name, "").strip()}
    values = {}
    for fields in FIELDS.values():
        for setting, variable, _field, _convert in fields:
            if variable in present:
                values[setting] = getattr(settings, setting, "")
    for name in ("EMAIL_USE_TLS", "EMAIL_USE_SSL"):
        if name in present:
            values[name] = getattr(settings, name)
    return values


def group_of(field):
    if field == "email_security":
        return "email"
    if field == "paperless_mode":
        return "paperless"
    for group, fields in FIELDS.items():
        if any(field == name for _setting, _variable, name, _convert in fields):
            return group
    return None


def imported_groups(site):
    return {name for name in site.env_imported.split(",") if name}


def apply(site, values, groups):
    """Werte der Gruppen ``groups`` in leere Felder schreiben; liefert die Felder."""
    changed = []
    for field, value in site_values(values).items():
        if group_of(field) not in groups:
            continue
        current = getattr(site, field)
        empty = current in ("", None) or (field == "paperless_mode" and current == site.PAPERLESS_OFF)
        if empty:
            setattr(site, field, value)
            changed.append(field)
    return changed


def run(sender=None, **kwargs):
    """Noch nicht übernommene Gruppen übernehmen; wiederholbar."""
    from web.models import SiteSettings

    values = environment_values()
    site = SiteSettings.objects.filter(pk=1).first()
    if site is None:
        # Neue Installation ohne alte Variablen: nichts anlegen
        if not values:
            return
        site = SiteSettings.load()
    done = imported_groups(site)
    pending = set(FIELDS) - done
    if values:
        ignored = sorted(name for name in VARIABLES if os.environ.get(name, "").strip())
        if pending:
            changed = apply(site, values, pending)
            if changed:
                logger.warning("Einstellungen aus der .env in die Verwaltung übernommen: %s", ", ".join(changed))
        if done:
            logger.warning(
                "Diese Variablen werden seit Version 0.9.0 nicht mehr ausgewertet und können aus der .env "
                "bzw. der Compose-Datei entfernt werden (Einstellungen unter Verwaltung): %s", ", ".join(ignored),
            )
    if not pending:
        return
    site.env_imported = ",".join(sorted(done | pending))
    site.save()
    if "email" in pending and site.reminder_send_time:
        from contracts.apps import ensure_reminder_schedule

        ensure_reminder_schedule()
