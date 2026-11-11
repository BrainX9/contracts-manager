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
- Ist SSO ausgeschaltet (``OIDC_ENABLED``), liefern die Adressen 404.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import Group
from django.http import Http404
from django.shortcuts import resolve_url
from django.utils.translation import gettext_lazy as _
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView, OIDCAuthenticationRequestView

from contracts import permissions as perms

logger = logging.getLogger(__name__)


def sso_enabled():
    return getattr(settings, "OIDC_ENABLED", False)


class SSOBackend(OIDCAuthenticationBackend):
    """
    Benutzer über den Benutzernamen aus dem SSO zuordnen bzw. anlegen.
    """

    def get_userinfo(self, access_token, id_token, payload):
        # Claims aus ID-Token und Userinfo zusammenführen; Userinfo hat Vorrang
        userinfo = super().get_userinfo(access_token, id_token, payload)
        return {**payload, **userinfo}

    def get_username(self, claims):
        value = claims.get(settings.OIDC_USERNAME_CLAIM)
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


class SSORequestView(OIDCAuthenticationRequestView):
    """
    Startet die Anmeldung beim SSO (Weiterleitung zum Anbieter).
    """

    def dispatch(self, request, *args, **kwargs):
        if not sso_enabled():
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class SSOCallbackView(OIDCAuthenticationCallbackView):
    """
    Rückkehr vom SSO. Bei einem Fehler zurück zur Anmeldeseite mit Meldung.
    """

    def dispatch(self, request, *args, **kwargs):
        if not sso_enabled():
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
