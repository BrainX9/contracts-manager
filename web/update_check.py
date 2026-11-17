"""
Hinweis auf neue Versionen (Version 0.5.0, nur für Superuser sichtbar).

Der Worker fragt einmal täglich bei GitHub nach dem neuesten Release
(``web.tasks.check_for_updates``) und speichert das Ergebnis in
``web.SiteSettings``. Seiten fragen nie selbst nach, sie laufen also auch
ohne Internet. Gesendet wird nur eine anonyme Anfrage; GitHub sieht die
IP-Adresse des Servers, aber keine Daten aus der App. Abschaltbar unter
Verwaltung › System.
"""

import json
import logging
import re
import urllib.request

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

REPOSITORY = "BrainX9/contracts-manager"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
TIMEOUT = 10
# Die Antwort ist klein; mehr wird nicht gelesen
MAX_BYTES = 1_000_000

VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(text):
    """
    "v0.5.0" oder "0.5.0" als (0, 5, 0). Alles andere (Beta-Versionen,
    Zusätze) ergibt None und wird nicht verglichen.
    """
    match = VERSION_PATTERN.match((text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def release_url(version):
    return f"{RELEASES_URL}/tag/v{version}"


def fetch_latest(timeout=TIMEOUT):
    """
    Neueste Version laut GitHub (z. B. "0.5.0"). Wirft eine Ausnahme bei
    Netzwerkfehlern und unerwarteten Antworten.
    """
    request = urllib.request.Request(
        LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            # Bewusst allgemein: keine Angaben zur Installation
            "User-Agent": "contracts-manager-update-check",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (feste https-Adresse)
        data = json.loads(response.read(MAX_BYTES))
    tag = data.get("tag_name", "") if isinstance(data, dict) else ""
    parsed = parse_version(tag)
    if parsed is None:
        raise ValueError(f"Unerwarteter Versionsname: {tag!r}")
    return ".".join(str(part) for part in parsed)


def check(timeout=TIMEOUT):
    """
    Fragt GitHub und speichert das Ergebnis. Ausgeschaltet oder bei einem
    Fehler bleibt der letzte bekannte Stand erhalten. Ergebnis: die neueste
    Version oder None.
    """
    from .models import SiteSettings

    site = SiteSettings.load()
    if not site.update_check:
        return None
    try:
        latest = fetch_latest(timeout)
    except Exception as error:  # Netzwerk, JSON, Format: nie den Worker stören
        logger.warning("Update-Prüfung fehlgeschlagen: %s", error)
        return None
    site.latest_version = latest
    site.update_checked_at = timezone.now()
    site.save(update_fields=["latest_version", "update_checked_at"])
    return latest


def newer_than_installed(latest):
    latest_parsed, installed = parse_version(latest), parse_version(settings.APP_VERSION)
    return bool(latest_parsed and installed and latest_parsed > installed)


def available_update():
    """
    Die neuere Version, die der Worker zuletzt gefunden hat, sonst None.
    Legt keinen Datensatz an und fragt nicht im Netz.
    """
    from .models import SiteSettings

    row = SiteSettings.objects.filter(pk=1).values("update_check", "latest_version").first()
    if not row or not row["update_check"]:
        return None
    return row["latest_version"] if newer_than_installed(row["latest_version"]) else None
