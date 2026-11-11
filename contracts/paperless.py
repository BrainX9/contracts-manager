"""
Verknüpfung mit Paperless-ngx.

Erzeugt Links in die Weboberfläche von Paperless, wenn in den Settings
PAPERLESS_URL gesetzt ist (z. B. https://paperless.example.de).
Ohne PAPERLESS_URL geben alle Funktionen None zurück.
"""

from urllib.parse import urlencode

from django.conf import settings


def base_url():
    """
    Basisadresse von Paperless ohne abschließenden Schrägstrich (oder None).
    """
    url = (getattr(settings, "PAPERLESS_URL", "") or "").strip().rstrip("/")
    return url or None


def document_url(document_id):
    """
    Link zur Detailansicht eines Dokuments.
    """
    base = base_url()
    if not base or not document_id:
        return None
    return f"{base}/documents/{int(document_id)}/details"


def correspondent_url(correspondent_id):
    """
    Link zur Dokumentenliste, gefiltert auf einen Korrespondenten.
    """
    base = base_url()
    if not base or not correspondent_id:
        return None
    query = urlencode({"correspondent__id__in": int(correspondent_id)})
    return f"{base}/documents?{query}"
