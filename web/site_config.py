"""
E-Mail, Erinnerungsversand und Single Sign-on: Verwaltung vor .env
(Version 0.4.0).

Die Werte stehen in der .env (``EMAIL_*``, ``DEFAULT_FROM_EMAIL``,
``APP_BASE_URL``, ``REMINDER_SEND_TIME``, ``OIDC_*``) und zusätzlich unter
Verwaltung › E-Mail bzw. Single Sign-on (``web.SiteSettings``, nur
Superuser). Ein Wert aus der Verwaltung hat Vorrang, ein leeres Feld
bedeutet "Wert aus der .env". Dies ist die einzige Stelle, die beides
zusammenführt (wie ``assistant.config`` für den KI-Assistenten); Versand,
Anmeldung und Ansichten fragen ``mail()``, ``sso()`` und
``reminder_time()``.
"""

from dataclasses import dataclass

from django.conf import settings

# Pfade des Synology SSO Servers unterhalb der Adresse des Servers
SYNOLOGY_PATHS = {
    "authorization_endpoint": "/webman/sso/SSOOauth.cgi",
    "token_endpoint": "/webman/sso/SSOAccessToken.cgi",
    "user_endpoint": "/webman/sso/SSOUserInfo.cgi",
    "jwks_endpoint": "/webman/sso/openid-jwks.json",
}

SECURITY_STARTTLS = "starttls"
SECURITY_SSL = "ssl"
SECURITY_NONE = "none"


def switch(value, default):
    """"on"/"off" aus der Verwaltung, sonst der Wert aus der .env."""
    return {"on": True, "off": False}.get(value, default)


# --- E-Mail -------------------------------------------------------------------

@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    security: str
    user: str
    password: str
    sender: str
    # Öffentliche Adresse der App für Links in E-Mails, ohne Schrägstrich am Ende
    base_url: str
    timeout: int = 20

    @property
    def smtp(self):
        """Ohne Server werden E-Mails nur ins Log geschrieben."""
        return bool(self.host)

    @property
    def security_label(self):
        return {SECURITY_SSL: "SSL/TLS", SECURITY_STARTTLS: "STARTTLS"}.get(self.security, "–")


def mail_from_env():
    """Konfiguration nur aus der .env."""
    if settings.EMAIL_USE_SSL:
        security = SECURITY_SSL
    elif settings.EMAIL_USE_TLS:
        security = SECURITY_STARTTLS
    else:
        security = SECURITY_NONE
    return MailConfig(
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        security=security,
        user=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        sender=settings.DEFAULT_FROM_EMAIL,
        base_url=settings.APP_BASE_URL,
        timeout=settings.EMAIL_TIMEOUT,
    )


def own_user_address(site):
    """SMTP-Benutzer aus der Verwaltung, wenn er eine E-Mail-Adresse ist."""
    user = site.email_user.strip()
    return user if "@" in user else ""


def merge_mail(site, env=None):
    """
    Führt die Einstellungen aus der Verwaltung (``site``: SiteSettings oder
    ein Objekt mit denselben Attributen) mit der .env zusammen.
    """
    env = env or mail_from_env()
    return MailConfig(
        host=site.email_host.strip() or env.host,
        port=site.email_port or env.port,
        security=site.email_security or env.security,
        user=site.email_user.strip() or env.user,
        password=site.email_password or env.password,
        # Wie in der .env: ohne eigenen Absender die Adresse des Benutzers
        sender=site.email_sender.strip() or own_user_address(site) or env.sender,
        base_url=site.app_base_url.strip().rstrip("/") or env.base_url,
        timeout=env.timeout,
    )


def mail():
    """Gültige Konfiguration: Verwaltung vor .env."""
    from .models import SiteSettings

    return merge_mail(SiteSettings.load())


def reminder_time():
    """Uhrzeit des täglichen Erinnerungsversands ("HH:MM")."""
    from .models import SiteSettings

    # Ohne load(): läuft auch nach jeder Migration und soll dort keinen
    # Datensatz anlegen
    own = SiteSettings.objects.filter(pk=1).values_list("reminder_send_time", flat=True).first()
    return own or settings.REMINDER_SEND_TIME


# --- Single Sign-on -----------------------------------------------------------

@dataclass(frozen=True)
class SSOConfig:
    enabled: bool
    base_url: str
    client_id: str
    client_secret: str
    create_users: bool
    username_claim: str
    scopes: str
    sign_algo: str
    authorization_endpoint: str
    token_endpoint: str
    user_endpoint: str
    jwks_endpoint: str

    def oidc_settings(self):
        """Namen der Einstellungen von mozilla-django-oidc mit den gültigen Werten."""
        return {
            "OIDC_OP_AUTHORIZATION_ENDPOINT": self.authorization_endpoint,
            "OIDC_OP_TOKEN_ENDPOINT": self.token_endpoint,
            "OIDC_OP_USER_ENDPOINT": self.user_endpoint,
            "OIDC_OP_JWKS_ENDPOINT": self.jwks_endpoint,
            "OIDC_RP_CLIENT_ID": self.client_id,
            "OIDC_RP_CLIENT_SECRET": self.client_secret,
            "OIDC_RP_SIGN_ALGO": self.sign_algo,
            "OIDC_RP_SCOPES": self.scopes,
            "OIDC_CREATE_USER": self.create_users,
        }


def sso_from_env():
    """Konfiguration nur aus der .env."""
    return SSOConfig(
        enabled=settings.OIDC_ENABLED,
        base_url=settings.OIDC_OP_BASE_URL,
        client_id=settings.OIDC_RP_CLIENT_ID,
        client_secret=settings.OIDC_RP_CLIENT_SECRET,
        create_users=settings.OIDC_CREATE_USER,
        username_claim=settings.OIDC_USERNAME_CLAIM,
        scopes=settings.OIDC_RP_SCOPES,
        sign_algo=settings.OIDC_RP_SIGN_ALGO,
        authorization_endpoint=settings.OIDC_OP_AUTHORIZATION_ENDPOINT,
        token_endpoint=settings.OIDC_OP_TOKEN_ENDPOINT,
        user_endpoint=settings.OIDC_OP_USER_ENDPOINT,
        jwks_endpoint=settings.OIDC_OP_JWKS_ENDPOINT,
    )


def merge_sso(site, env=None):
    """
    Wie ``merge_mail``. Endpunkte: ein eigener Wert aus der Verwaltung, sonst
    der Pfad des Synology SSO Servers unter der Adresse aus der Verwaltung,
    sonst der Wert aus der .env.
    """
    env = env or sso_from_env()
    base_url = site.sso_base_url.strip().rstrip("/")

    def endpoint(name):
        own = getattr(site, f"sso_{name}").strip()
        if own:
            return own
        if base_url:
            return base_url + SYNOLOGY_PATHS[name]
        return getattr(env, name)

    return SSOConfig(
        enabled=switch(site.sso_enabled, env.enabled),
        base_url=base_url or env.base_url,
        client_id=site.sso_client_id.strip() or env.client_id,
        client_secret=site.sso_client_secret or env.client_secret,
        create_users=switch(site.sso_create_users, env.create_users),
        username_claim=site.sso_username_claim.strip() or env.username_claim,
        scopes=" ".join(site.sso_scopes.split()) or env.scopes,
        sign_algo=site.sso_sign_algo.strip() or env.sign_algo,
        authorization_endpoint=endpoint("authorization_endpoint"),
        token_endpoint=endpoint("token_endpoint"),
        user_endpoint=endpoint("user_endpoint"),
        jwks_endpoint=endpoint("jwks_endpoint"),
    )


def sso():
    """Gültige Konfiguration: Verwaltung vor .env."""
    from .models import SiteSettings

    return merge_sso(SiteSettings.load())
