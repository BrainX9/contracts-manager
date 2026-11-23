"""
E-Mail, Erinnerungsversand und Single Sign-on (Version 0.4.0, seit 0.9.0 nur
noch aus der Verwaltung).

Die Werte stehen unter Verwaltung › E-Mail bzw. Single Sign-on
(``web.SiteSettings``, nur Superuser); ein leeres Feld bedeutet
"Standardwert" (``mail_defaults``, ``sso_defaults``). Die früheren
Variablen der .env (``EMAIL_*``, ``OIDC_*`` …) übernimmt ``web.env_import``
einmalig, danach werden sie nicht mehr gelesen. Versand, Anmeldung und
Ansichten fragen ``mail()``, ``sso()`` und ``reminder_time()``.
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

# Standardwerte für leere Felder
REMINDER_DEFAULT = "07:00"


def switch(value, default):
    """"on"/"off" aus der Verwaltung, sonst der Standardwert."""
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


def mail_defaults():
    """Standardwerte für leere Felder (seit 0.9.0 nicht mehr aus der .env)."""
    return MailConfig(
        host="",
        port=587,
        security=SECURITY_STARTTLS,
        user="",
        password="",
        sender="contracts@localhost",
        base_url="",
        timeout=settings.EMAIL_TIMEOUT,
    )


def own_user_address(site):
    """SMTP-Benutzer aus der Verwaltung, wenn er eine E-Mail-Adresse ist."""
    user = site.email_user.strip()
    return user if "@" in user else ""


def merge_mail(site, env=None):
    """
    Einstellungen aus der Verwaltung (``site``: SiteSettings oder ein Objekt
    mit denselben Attributen), leere Felder mit den Standardwerten.
    """
    env = env or mail_defaults()
    return MailConfig(
        host=site.email_host.strip() or env.host,
        port=site.email_port or env.port,
        security=site.email_security or env.security,
        user=site.email_user.strip() or env.user,
        password=site.email_password or env.password,
        # Ohne eigenen Absender die Adresse des SMTP-Benutzers
        sender=site.email_sender.strip() or own_user_address(site) or env.sender,
        base_url=site.app_base_url.strip().rstrip("/") or env.base_url,
        timeout=env.timeout,
    )


def mail():
    """Gültige Konfiguration aus der Verwaltung."""
    from .models import SiteSettings

    return merge_mail(SiteSettings.load())


def reminder_time():
    """Uhrzeit des täglichen Erinnerungsversands ("HH:MM")."""
    from .models import SiteSettings

    # Ohne load(): läuft auch nach jeder Migration und soll dort keinen
    # Datensatz anlegen
    own = SiteSettings.objects.filter(pk=1).values_list("reminder_send_time", flat=True).first()
    return own or REMINDER_DEFAULT


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


def sso_defaults():
    """Standardwerte für leere Felder (seit 0.9.0 nicht mehr aus der .env)."""
    return SSOConfig(
        enabled=False,
        base_url="",
        client_id="",
        client_secret="",
        create_users=False,
        username_claim="username",
        scopes="openid email",
        sign_algo="RS256",
        authorization_endpoint="",
        token_endpoint="",
        user_endpoint="",
        jwks_endpoint="",
    )


def merge_sso(site, env=None):
    """
    Wie ``merge_mail``. Endpunkte: ein eigener Wert aus der Verwaltung, sonst
    der Pfad des Synology SSO Servers unter der Adresse aus der Verwaltung.
    """
    env = env or sso_defaults()
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
    """Gültige Konfiguration aus der Verwaltung."""
    from .models import SiteSettings

    return merge_sso(SiteSettings.load())
