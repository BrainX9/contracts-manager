"""
Django settings für config Projekt.

Generiert durch 'django-admin startproject' using Django 5.0.1.

Für weitere Informationen über diese Datei siehe
https://docs.djangoproject.com/en/5.0/topics/settings/

Für die vollständige Liste der Einstellungen und deren Werte siehe
https://docs.djangoproject.com/en/5.0/ref/settings/
"""

from datetime import timedelta
from pathlib import Path

import environ
from django.utils.translation import gettext_lazy as _

# Django-environ initialisieren
env = environ.Env(
    # Standardwerte
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
)

# .env-Datei laden
BASE_DIR = Path(__file__).resolve().parent.parent
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

# Sicherheit
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
# Die Zustandsprüfung im Container ruft http://localhost:8000/health/ auf
for _local_host in ("localhost", "127.0.0.1"):
    if _local_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_local_host)
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# Betrieb hinter einem Reverse Proxy mit HTTPS (z. B. Synology).
# Der Proxy muss den Header "X-Forwarded-Proto: https" setzen.
# Die Weiterleitung von HTTP auf HTTPS übernimmt der Proxy.
USE_HTTPS = env.bool("USE_HTTPS", default=False)
if USE_HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Optional: HSTS (Browser nutzen danach nur noch HTTPS). Standard aus,
    # da der Reverse Proxy der Synology HSTS selbst setzen kann.
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)

# Das CSRF-Token kommt für htmx aus der Seite (hx-headers), JavaScript muss
# das Cookie nicht lesen können
CSRF_COOKIE_HTTPONLY = True

# Application definition
INSTALLED_APPS = [
    "config.apps.AdminConfig",  # Django-Admin, nur für Superuser
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "guardian",  # Berechtigungen pro Objekt
    "django_htmx",  # HTMX-Hilfen (request.htmx)
    "django_q",  # Hintergrund-Aufgaben, z. B. Erinnerungen (Service "worker")
    "axes",  # Schutz vor zu vielen Fehlversuchen bei der Anmeldung
    "contracts",  # Fachlogik: Verträge, Stammdaten, Berechtigungen
    "web",  # Oberfläche (Frontend)
    "assistant",  # KI-Assistent (Version 0.8a)
]

# Anmeldung und Berechtigungen:
# - ModelBackend: Login und globale Rechte (z. B. "hinzufügen")
# - OwnerBackend: Eigentümer hat alle Rechte an seinen Objekten
# - guardian: Rechte pro Objekt für Leser und Bearbeiter
AUTHENTICATION_BACKENDS = [
    # Muss zuerst kommen: blockiert gesperrte Anmeldungen vor allen anderen
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
    "contracts.backends.OwnerBackend",
    "guardian.backends.ObjectPermissionBackend",
]

# guardian legt sonst einen Datenbank-Benutzer für anonyme Zugriffe an.
# Die App ist nur mit Anmeldung nutzbar.
ANONYMOUS_USER_NAME = None

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Für statische Dateien
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Sprache des angemeldeten Benutzers aktivieren (nach der Anmeldung)
    "web.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Antwortet bei gesperrter Anmeldung mit der Sperrseite (am Ende)
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "assistant.context_processors.assistant",
            ],
        },
    },
]

# Entwicklung: Templates ohne Cache laden, damit Änderungen ohne Neustart
# von gunicorn sichtbar sind (runserver erledigt das sonst automatisch).
if DEBUG:
    TEMPLATES[0]["APP_DIRS"] = False
    TEMPLATES[0]["OPTIONS"]["loaders"] = [
        "django.template.loaders.filesystem.Loader",
        "django.template.loaders.app_directories.Loader",
    ]

WSGI_APPLICATION = "config.wsgi.application"

# Database
DATABASES = {
    "default": env.db(),  # Verwendet DATABASE_URL Umgebungsvariable
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# Sprache: Texte im Code auf Englisch, Übersetzungen in locale/.
# Standard ist Deutsch, jeder Benutzer kann die Sprache in seinen
# Einstellungen ändern (web.middleware.UserLanguageMiddleware).
LANGUAGE_CODE = "de"
LANGUAGES = [
    ("de", _("German")),
    ("en", _("English")),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

# Anmeldung im Frontend
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Hochgeladene Dateien (später z. B. Vertragsdokumente).
# Im Container liegt das Verzeichnis auf einem Volume außerhalb des Images.
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Paperless-ngx (optional): Basisadresse für Links zu Dokumenten und
# Korrespondenten, z. B. https://paperless.example.de. Leer = keine Links.
PAPERLESS_URL = env("PAPERLESS_URL", default="")

# --- KI-Assistent (Version 0.8a) ---------------------------------------------
# Ausgeschaltet, solange AI_ENABLED nicht gesetzt ist. AI_PROVIDER:
# - "openai": OpenAI und alle OpenAI-kompatiblen Anbieter (IONOS AI Model
#   Hub, Mistral, lokales Ollama) über AI_BASE_URL
# - "anthropic": Claude
# Nutzen dürfen den Assistenten Mitglieder der Gruppe "KI" und Superuser.
AI_ENABLED = env.bool("AI_ENABLED", default=False)
AI_PROVIDER = env("AI_PROVIDER", default="openai")
AI_BASE_URL = env("AI_BASE_URL", default="")
AI_API_KEY = env("AI_API_KEY", default="")
AI_MODEL = env("AI_MODEL", default="")
# Claude: Denktiefe ("low", "medium", "high"; leer = Standard des Modells)
# und Höchstlänge einer Antwort einschließlich Denkschritten
AI_EFFORT = env("AI_EFFORT", default="")
AI_MAX_TOKENS = env.int("AI_MAX_TOKENS", default=16000)
# Höchstzahl der Werkzeugaufrufe je Antwort (Schutz vor Endlosschleifen)
AI_MAX_TOOL_ROUNDS = env.int("AI_MAX_TOOL_ROUNDS", default=8)
# Wartezeit je Anfrage an den Anbieter in Sekunden
AI_TIMEOUT = env.int("AI_TIMEOUT", default=120)

# --- Single Sign-on über OpenID Connect (Version 0.4 beta) -------------------
# Z. B. mit dem Synology SSO Server. Ausgeschaltet, solange OIDC_ENABLED
# nicht gesetzt ist; die lokale Anmeldung bleibt immer möglich.
# Umsetzung: web.oidc (mozilla-django-oidc).
OIDC_ENABLED = env.bool("OIDC_ENABLED", default=False)
# Adresse des Anbieters, z. B. https://nas.example.de. Daraus ergeben sich die
# Endpunkte des Synology SSO Servers; einzeln überschreibbar.
OIDC_OP_BASE_URL = env("OIDC_OP_BASE_URL", default="").rstrip("/")
OIDC_OP_AUTHORIZATION_ENDPOINT = env(
    "OIDC_OP_AUTHORIZATION_ENDPOINT", default=f"{OIDC_OP_BASE_URL}/webman/sso/SSOOauth.cgi"
)
OIDC_OP_TOKEN_ENDPOINT = env(
    "OIDC_OP_TOKEN_ENDPOINT", default=f"{OIDC_OP_BASE_URL}/webman/sso/SSOAccessToken.cgi"
)
OIDC_OP_USER_ENDPOINT = env(
    "OIDC_OP_USER_ENDPOINT", default=f"{OIDC_OP_BASE_URL}/webman/sso/SSOUserInfo.cgi"
)
OIDC_OP_JWKS_ENDPOINT = env(
    "OIDC_OP_JWKS_ENDPOINT", default=f"{OIDC_OP_BASE_URL}/webman/sso/openid-jwks.json"
)
OIDC_RP_CLIENT_ID = env("OIDC_RP_CLIENT_ID", default="")
OIDC_RP_CLIENT_SECRET = env("OIDC_RP_CLIENT_SECRET", default="")
OIDC_RP_SIGN_ALGO = env("OIDC_RP_SIGN_ALGO", default="RS256")
OIDC_RP_SCOPES = env("OIDC_RP_SCOPES", default="openid email")
# Claim mit dem Benutzernamen (Synology: "username", sonst oft "preferred_username")
OIDC_USERNAME_CLAIM = env("OIDC_USERNAME_CLAIM", default="username")
# Unbekannte Benutzer beim ersten Login anlegen (Standard: nein)
OIDC_CREATE_USER = env.bool("OIDC_CREATE_USERS", default=False)
# Beschriftung der Schaltfläche auf der Anmeldeseite
OIDC_TIMEOUT = 10
OIDC_AUTHENTICATE_CLASS = "web.oidc.SSORequestView"
OIDC_CALLBACK_CLASS = "web.oidc.SSOCallbackView"
if OIDC_ENABLED:
    # Nach Axes und ModelBackend
    AUTHENTICATION_BACKENDS.insert(2, "web.oidc.SSOBackend")

# --- E-Mail (Version 0.5 beta) -----------------------------------------------
# Versand über SMTP, z. B. IONOS (smtp.ionos.de, Port 587, STARTTLS).
# Ohne EMAIL_HOST werden E-Mails nur ins Log geschrieben (Entwicklung).
EMAIL_HOST = env("EMAIL_HOST", default="")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = 20
# Absender, bei IONOS meist dieselbe Adresse wie EMAIL_HOST_USER
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER or "contracts@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_SUBJECT_PREFIX = "[Contracts Manager] "
# Öffentliche Adresse der App für Links in E-Mails, die ohne Anfrage
# entstehen (Erinnerungen im Hintergrund), z. B. https://vertraege.example.de
APP_BASE_URL = env("APP_BASE_URL", default="").rstrip("/")
# Gültigkeit der Links für Einladung und Passwort-Reset (Sekunden): 7 Tage
PASSWORD_RESET_TIMEOUT = 7 * 24 * 3600

# --- Hintergrund-Aufgaben mit django-q2 (Version 0.5 beta) --------------------
# Die Datenbank dient als Warteschlange (kein Redis). Der Worker läuft als
# eigener Service: python manage.py qcluster (docker-compose.yml, "worker").
Q_CLUSTER = {
    "name": "contracts",
    "orm": "default",
    # Zwei Prozesse, damit der KI-Assistent und der Erinnerungsversand
    # sich nicht gegenseitig aufhalten
    "workers": 2,
    "timeout": 300,
    # Muss größer als timeout sein
    "retry": 600,
    # Verpasste Läufe (z. B. Worker aus) nicht nachholen, nur einmal ausführen
    "catch_up": False,
    "save_limit": 100,
    "label": "Hintergrund-Aufgaben",
    # Die Standardwerte (poll 0,2 s, guard_cycle 0,5 s) sind für viele
    # Aufgaben gedacht und fragen die Datenbank ständig ab (ca. 15
    # Transaktionen pro Sekunde, spürbare CPU-Last im Leerlauf). Hier
    # gibt es nur den täglichen Erinnerungsversand und Antworten des
    # KI-Assistenten (0.8a); dafür reicht ein Abstand von 2 Sekunden
    # (etwa eine Abfrage pro Sekunde im Leerlauf).
    "poll": 2,
    # Überwachung der Worker-Prozesse alle 5 Sekunden; der Zeitplan wird
    # weiterhin etwa alle 30 Sekunden geprüft
    "guard_cycle": 5,
}
# Uhrzeit (Europe/Berlin) für den täglichen Versand der Erinnerungen
REMINDER_SEND_TIME = env("REMINDER_SEND_TIME", default="07:00")

# Angezeigte Version (Administration › System)
APP_VERSION = "0.2.0"

# --- Fehlversuche bei der Anmeldung (django-axes) ----------------------------
# Nach 5 falschen Passwörtern für denselben Benutzernamen von derselben
# IP-Adresse ist die Anmeldung 15 Minuten gesperrt. Eine erfolgreiche
# Anmeldung setzt den Zähler zurück. Superuser sehen die Versuche in der
# Django-Verwaltung und können Sperren dort aufheben.
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("AXES_COOLOFF_MINUTES", default=15))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "web/lockout.html"
# Hinter dem Reverse Proxy steht die echte IP-Adresse in X-Forwarded-For
if USE_HTTPS:
    AXES_IPWARE_PROXY_COUNT = 1
    AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]

# Protokoll: Fehler (z. B. Server Error 500) mit Traceback in die
# Container-Ausgabe (docker compose logs), auch bei DEBUG=False.
# Abgewiesene Aufrufe (403, 404) stehen bereits im Zugriffsprotokoll von
# gunicorn und werden hier nicht wiederholt.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="WARNING"), "propagate": False},
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
