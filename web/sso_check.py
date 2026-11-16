"""
Test der SSO-Einstellungen (Version 0.4.0), aufgerufen unter Verwaltung ›
Single Sign-on.

Geprüft werden die eingegebenen, nicht gespeicherten Werte: Vollständigkeit
und die Erreichbarkeit der Endpunkte, die die App selbst aufruft (Schlüssel,
Token, Benutzerdaten). Ob Client-ID und Client-Secret stimmen, zeigt erst
eine echte Anmeldung; das lässt sich ohne Benutzer nicht prüfen.
"""

from dataclasses import dataclass

import requests
from django.utils.translation import gettext as _

OK = "ok"
FAILED = "failed"
# Sekunden je Anfrage; drei Anfragen bleiben unter der Zeitgrenze des Webservers
TIMEOUT = 6


@dataclass(frozen=True)
class CheckResult:
    label: str
    status: str
    detail: str


def error_text(error):
    text = str(error) or error.__class__.__name__
    return text if len(text) <= 300 else text[:300] + " …"


def check_complete(config):
    missing = [
        label for label, value in (
            (_("client ID"), config.client_id),
            (_("client secret"), config.client_secret),
            (_("authorization endpoint"), config.authorization_endpoint.startswith("http")),
        ) if not value
    ]
    if missing:
        return CheckResult(_("Settings complete"), FAILED, _("Missing: %(missing)s.") % {"missing": ", ".join(missing)})
    return CheckResult(_("Settings complete"), OK, _("Client ID, client secret and endpoints are set."))


def check_jwks(config, timeout=TIMEOUT):
    label = _("Signing keys (JWKS)")
    if not config.jwks_endpoint.startswith("http"):
        return CheckResult(label, FAILED, _("No address set."))
    try:
        response = requests.get(config.jwks_endpoint, timeout=timeout)
        response.raise_for_status()
        keys = response.json().get("keys")
    except (requests.RequestException, ValueError, AttributeError) as error:
        return CheckResult(label, FAILED, f"{config.jwks_endpoint}: {error_text(error)}")
    if not keys:
        return CheckResult(label, FAILED, _("%(url)s answers, but contains no keys.") % {"url": config.jwks_endpoint})
    return CheckResult(label, OK, _("%(count)s key(s) loaded from %(url)s.") % {
        "count": len(keys), "url": config.jwks_endpoint,
    })


def check_reachable(label, url, method, timeout=TIMEOUT):
    """
    Der Endpunkt antwortet. Ohne gültige Anmeldung ist eine Ablehnung (4xx)
    die erwartete Antwort; ein Fehler ist nur "nicht erreichbar", "nicht
    gefunden" oder ein Serverfehler.
    """
    if not url.startswith("http"):
        return CheckResult(label, FAILED, _("No address set."))
    try:
        response = requests.request(method, url, timeout=timeout, allow_redirects=False)
    except requests.RequestException as error:
        return CheckResult(label, FAILED, f"{url}: {error_text(error)}")
    if response.status_code == 404 or response.status_code >= 500:
        return CheckResult(label, FAILED, _("%(url)s answers with HTTP %(status)s.") % {
            "url": url, "status": response.status_code,
        })
    return CheckResult(label, OK, _("%(url)s is reachable (HTTP %(status)s).") % {
        "url": url, "status": response.status_code,
    })


def run(config, timeout=TIMEOUT):
    """Alle Prüfungen; ohne vollständige Angaben entfallen die Anfragen."""
    results = [check_complete(config)]
    results.append(check_jwks(config, timeout))
    results.append(check_reachable(_("Token endpoint"), config.token_endpoint, "POST", timeout))
    results.append(check_reachable(_("User info endpoint"), config.user_endpoint, "GET", timeout))
    return results
