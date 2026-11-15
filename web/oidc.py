"""
Anmeldung über OpenID Connect (Version 0.4 beta), z. B. mit dem
Synology SSO Server. Aufbauend auf mozilla-django-oidc.

Regeln:
- Zuordnung über den Benutzernamen aus dem Claim ``OIDC_USERNAME_CLAIM``
  (Synology: "username"), ohne Beachtung der Groß-/Kleinschreibung.
  Die E-Mail wird nicht zur Zuordnung verwendet.
- Unbekannte Benutzer werden nur angelegt, wenn ``OIDC_CREATE_USER``
  gesetzt ist. Sie kommen in die Gruppen "Benutzer" und "Alle", bekommen
  kein lokales Passwort und nie Superuser- oder Mitarbeiter-Status.
- Deaktivierte Benutzer können sich auch über SSO nicht anmelden.
- Ist SSO ausgeschaltet, liefern die Adressen 404 und das Backend meldet
  niemanden an.

Seit 0.4.0 kommen die Werte aus ``web.site_config.sso()`` (Verwaltung vor
.env) und gelten ohne Neustart. mozilla-django-oidc liest seine
Einstellungen über ``get_settings``; Backend und Ansichten überschreiben
das (``ConfiguredMixin``).
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.functional import cached_property
from django.http import Http404
from django.shortcuts import resolve_url
from django.utils.translation import gettext_lazy as _
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView, OIDCAuthenticationRequestView

from mozilla_django_oidc.utils import import_from_settings

from contracts import permissions as perms

from . import site_config

logger = logging.getLogger(__name__)


def sso_enabled():
    return site_config.sso().enabled


class ConfiguredMixin:
    """
    Einstellungen von mozilla-django-oidc aus ``web.site_config``. Die
    Konfiguration wird erst beim ersten Zugriff gelesen und gilt für dieses
    Objekt (eine Anfrage bzw. eine Anmeldung).
    """

    @cached_property
    def sso_config(self):
        return site_config.sso()

    def get_settings(self, attr, *args):
        values = self.sso_config.oidc_settings()
        if attr in values:
            return values[attr]
        return import_from_settings(attr, *args)


class SSOBackend(ConfiguredMixin, OIDCAuthenticationBackend):
    """
    Benutzer über den Benutzernamen aus dem SSO zuordnen bzw. anlegen.
    """

    def __init__(self, *args, **kwargs):
        # Nicht die Basisklasse aufrufen: Sie liest sofort alle Einstellungen.
        # Django erzeugt die Backends aber bei jeder Rechteprüfung, das wäre
        # jedes Mal eine Datenbankabfrage. Die Werte kommen bei Bedarf aus
        # den Eigenschaften unten.
        self.UserModel = get_user_model()

    OIDC_OP_TOKEN_ENDPOINT = property(lambda self: self.sso_config.token_endpoint)
    OIDC_OP_USER_ENDPOINT = property(lambda self: self.sso_config.user_endpoint)
    OIDC_OP_JWKS_ENDPOINT = property(lambda self: self.sso_config.jwks_endpoint)
    OIDC_RP_CLIENT_ID = property(lambda self: self.sso_config.client_id)
    OIDC_RP_CLIENT_SECRET = property(lambda self: self.sso_config.client_secret)
    OIDC_RP_SIGN_ALGO = property(lambda self: self.sso_config.sign_algo)
    # Schlüssel kommen immer vom JWKS-Endpunkt
    OIDC_RP_IDP_SIGN_KEY = None

    def authenticate(self, request, **kwargs):
        # Nur die Rückkehr vom SSO (code und state); sonst nicht zuständig
        if request is None or not request.GET.get("code") or not request.GET.get("state"):
            return None
        if not self.sso_config.enabled:
            return None
        return super().authenticate(request, **kwargs)

    def get_userinfo(self, access_token, id_token, payload):
        # Claims aus ID-Token und Userinfo zusammenführen; Userinfo hat Vorrang
        userinfo = super().get_userinfo(access_token, id_token, payload)
        return {**payload, **userinfo}

    def get_username(self, claims):
        value = claims.get(self.sso_config.username_claim)
        return value.strip() if isinstance(value, str) else ""

    def describe_user_by_claims(self, claims):
        return f"username {self.get_username(claims)!r}"

    def verify_claims(self, claims):
        return bool(self.get_username(claims))

    def filter_users_by_claims(self, claims):
        username = self.get_username(claims)
        if not username:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(username__iexact=username)

    def create_user(self, claims):
        user = self.UserModel(username=self.get_username(claims), email=claims.get("email") or "")
        # Anmeldung nur über SSO, kein lokales Passwort
        user.set_unusable_password()
        user.save()
        # Darf Objekte anlegen; in "Alle" kommt jeder neue Benutzer automatisch
        users_group, _created = Group.objects.get_or_create(name=perms.GROUP_USERS)
        user.groups.add(users_group)
        logger.info("Benutzer %s über SSO angelegt", user.username)
        return user

    def update_user(self, user, claims):
        # E-Mail aus dem SSO übernehmen, wenn lokal keine hinterlegt ist
        email = claims.get("email")
        if email and not user.email:
            user.email = email
            user.save(update_fields=["email"])
        return user


class SSORequestView(ConfiguredMixin, OIDCAuthenticationRequestView):
    """
    Startet die Anmeldung beim SSO (Weiterleitung zum Anbieter).
    """

    def dispatch(self, request, *args, **kwargs):
        if not self.sso_config.enabled:
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class SSOCallbackView(ConfiguredMixin, OIDCAuthenticationCallbackView):
    """
    Rückkehr vom SSO. Bei einem Fehler zurück zur Anmeldeseite mit Meldung.
    """

    def dispatch(self, request, *args, **kwargs):
        if not self.sso_config.enabled:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    @property
    def failure_url(self):
        return resolve_url(settings.LOGIN_URL)

    def login_failure(self):
        messages.error(
            self.request,
            _("Single sign-on failed. Your account may not be set up for this application yet."),
        )
        return super().login_failure()
