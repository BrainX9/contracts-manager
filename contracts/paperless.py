"""
Verknüpfung mit Paperless-ngx.

Bis Version 0.8.0 nur Links in die Weboberfläche (``PAPERLESS_URL`` in der
.env). Seit 0.9.0 steht alles unter Verwaltung › Paperless
(``web.SiteSettings``, nur Superuser) mit drei Betriebsarten:

- ``off``: keine Links, keine Anfragen;
- ``links``: Links in die Weboberfläche wie bisher;
- ``api``: Contracts Manager holt Paperless-Dokumente über die API und zeigt
  sie selbst an (Rechte von Contracts Manager), und Uploads lassen sich an
  Paperless übertragen (``web.paperless_sync``).

Die API wird nur mit der Standardbibliothek angesprochen (``urllib``), mit
dem Token eines eigenen Paperless-Benutzers. Fehlermeldungen enthalten nie
das Token.
"""

import json
import mimetypes
import re
import uuid
from dataclasses import dataclass, field
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote, urlencode

from django.utils.translation import gettext as _

TIMEOUT = 30


class PaperlessError(Exception):
    """Fehler bei einer Anfrage an Paperless (Text ohne Token)."""


@dataclass(frozen=True)
class PaperlessConfig:
    mode: str = "off"
    # Adresse ohne Schrägstrich am Ende
    base_url: str = ""
    token: str = ""
    tags: tuple = field(default_factory=tuple)
    user_tag: bool = False
    user_tag_prefix: str = ""

    @property
    def links(self):
        """Links in die Weboberfläche (in den Modi "Links" und "API")."""
        return self.mode in ("links", "api") and bool(self.base_url)

    @property
    def api(self):
        return self.mode == "api" and bool(self.base_url) and bool(self.token)

    def user_tag_for(self, user):
        """Tag für den Benutzer, z. B. CM-anna (oder None)."""
        if not self.user_tag or user is None:
            return None
        return f"{self.user_tag_prefix}{user.get_username()}"[:128]


def split_tags(text):
    """Kommagetrennte Tags ohne Leerzeichen am Rand und ohne Dubletten."""
    tags = []
    for name in (text or "").split(","):
        name = name.strip()[:128]
        if name and name.lower() not in (tag.lower() for tag in tags):
            tags.append(name)
    return tuple(tags)


def merge(site):
    """Konfiguration aus den Einstellungen (SiteSettings oder Ersatzobjekt)."""
    return PaperlessConfig(
        mode=site.paperless_mode or "off",
        base_url=(site.paperless_url or "").strip().rstrip("/"),
        token=(site.paperless_token or "").strip(),
        tags=split_tags(site.paperless_tags),
        user_tag=bool(site.paperless_user_tag),
        user_tag_prefix=site.paperless_user_tag_prefix or "",
    )


def config():
    """Gültige Konfiguration aus der Verwaltung."""
    from web.models import SiteSettings

    return merge(SiteSettings.load())


def base_url():
    """
    Basisadresse von Paperless ohne abschließenden Schrägstrich (oder None,
    auch wenn Paperless ausgeschaltet ist).
    """
    current = config()
    return current.base_url if current.links else None


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


# --- API ----------------------------------------------------------------------

def _open(cfg, path, data=None, headers=None, method=None, timeout=TIMEOUT):
    """Anfrage an ``<Adresse>/api/<path>``; liefert die offene Antwort."""
    url = f"{cfg.base_url}/api/{path}"
    request = urlrequest.Request(url, data=data, method=method, headers={
        "Authorization": f"Token {cfg.token}",
        "Accept": "application/json",
        "User-Agent": "contracts-manager",
        **(headers or {}),
    })
    try:
        response = urlrequest.urlopen(request, timeout=timeout)
    except urlerror.HTTPError as error:
        raise PaperlessError(describe_http_error(error)) from None
    except (urlerror.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise PaperlessError(_("Paperless is not reachable: %(error)s") % {"error": reason}) from None
    final_url = getattr(response, "geturl", lambda: url)() or url
    if final_url.split("?")[0] != url.split("?")[0]:
        # Weiterleitung, meist auf die Anmeldeseite: falsche Adresse
        response.close()
        raise PaperlessError(_("Paperless redirected the request. Is the address correct?"))
    return response


def describe_http_error(error):
    if error.code in (401, 403):
        return _("Paperless refused the request (HTTP %(code)s): check the API token and the permissions "
                 "of the Paperless user.") % {"code": error.code}
    if error.code == 404:
        return _("Not found in Paperless (HTTP 404).")
    try:
        detail = error.read(500).decode("utf-8", "replace").strip()
    except OSError:
        detail = ""
    message = _("Paperless answered with HTTP %(code)s.") % {"code": error.code}
    return f"{message} {detail}".strip()


def _json(cfg, path, payload=None, method=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    with _open(cfg, path, data=data, headers=headers, method=method) as response:
        try:
            return json.loads(response.read() or b"null")
        except ValueError:
            raise PaperlessError(_("Paperless sent an unexpected answer. Is the address correct?")) from None


def check(cfg):
    """
    Test der eingegebenen Werte (Verwaltung › Paperless). Liefert eine Liste
    aus (Erfolg, Text); nichts wird angelegt.
    """
    if not cfg.base_url or not cfg.token:
        return [(False, _("Please enter the address of Paperless and an API token."))]
    results = []
    try:
        data = _json(cfg, "documents/?page_size=1&fields=id")
        if not isinstance(data, dict) or "count" not in data:
            raise PaperlessError(_("Paperless sent an unexpected answer. Is the address correct?"))
        count = data["count"]
        results.append((True, _("Connected. The Paperless user sees %(count)s documents.") % {"count": count}))
    except PaperlessError as error:
        return [(False, str(error))]
    try:
        names = list(cfg.tags)
        existing = [name for name in names if find_tag(cfg, name)]
        missing = [name for name in names if name not in existing]
        if missing:
            results.append((True, _("Tags to be created on the first transfer: %(tags)s") % {
                "tags": ", ".join(missing)}))
        elif names:
            results.append((True, _("All tags exist in Paperless.")))
        else:
            results.append((True, _("Tags can be read.")))
    except PaperlessError as error:
        results.append((False, _("Tags: %(error)s") % {"error": error}))
    return results


def find_tag(cfg, name):
    """ID des Tags mit diesem Namen (ohne Groß-/Kleinschreibung) oder None."""
    data = _json(cfg, f"tags/?{urlencode({'name__iexact': name, 'fields': 'id,name'})}")
    for tag in (data or {}).get("results", []):
        if tag.get("name", "").lower() == name.lower():
            return tag["id"]
    return None


def tag_ids(cfg, names):
    """IDs der Tags; fehlende werden angelegt (ohne automatische Zuordnung)."""
    ids = []
    for name in names:
        tag_id = find_tag(cfg, name)
        if tag_id is None:
            # matching_algorithm 0: Paperless ordnet den Tag nie selbst zu
            tag_id = _json(cfg, "tags/", {"name": name, "matching_algorithm": 0})["id"]
        ids.append(tag_id)
    return ids


def _multipart(fields, file_field, filename, content):
    """Formular als multipart/form-data (Felder als Liste von Paaren)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    safe_name = quote(filename)
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
         f'filename="{safe_name}"; filename*=UTF-8\'\'{safe_name}\r\n'
         f"Content-Type: {content_type}\r\n\r\n").encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload(cfg, filename, content, title, tags=(), correspondent=None):
    """
    Lädt eine Datei hoch (``post_document``). Paperless verarbeitet sie im
    Hintergrund und liefert nur die ID der Aufgabe.
    """
    fields = [("title", title[:128])]
    fields += [("tags", tag_id) for tag_id in tags]
    if correspondent:
        fields.append(("correspondent", int(correspondent)))
    body, content_type = _multipart(fields, "document", filename, content)
    with _open(cfg, "documents/post_document/", data=body, headers={"Content-Type": content_type},
               method="POST", timeout=120) as response:
        task_id = json.loads(response.read() or b'""')
    if not isinstance(task_id, str) or not task_id:
        raise PaperlessError(_("Paperless sent an unexpected answer. Is the address correct?"))
    return task_id


@dataclass(frozen=True)
class TaskState:
    done: bool
    document_id: int = None
    error: str = ""


DUPLICATE = re.compile(r"duplicate of .*\(#(\d+)\)", re.IGNORECASE | re.DOTALL)


def task_document_id(task):
    """Dokument-ID einer erledigten Aufgabe (Paperless 2.x und 3.x) oder None."""
    result_data = task.get("result_data")
    if isinstance(result_data, dict) and result_data.get("document_id"):
        return int(result_data["document_id"])
    related = task.get("related_document_ids")
    if isinstance(related, list) and related:
        return int(related[0])
    if task.get("related_document"):
        return int(task["related_document"])
    return None


def task_message(task):
    """Meldung einer Aufgabe als Text (Paperless 2.x: result, 3.x: result_data)."""
    parts = []
    for key in ("result", "result_data"):
        value = task.get(key)
        if isinstance(value, dict):
            value = " ".join(str(item) for item in value.values() if item)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def task_state(cfg, task_id):
    """
    Stand einer Aufgabe von ``upload``. Erkennt Paperless die Datei als
    Dublette, gilt das vorhandene Dokument.
    """
    data = _json(cfg, f"tasks/?{urlencode({'task_id': task_id})}")
    tasks = data if isinstance(data, list) else (data or {}).get("results", [])
    if not tasks:
        return TaskState(done=False)
    task = tasks[0]
    status = (task.get("status") or "").upper()
    if status not in ("SUCCESS", "FAILURE", "REVOKED"):
        return TaskState(done=False)
    document_id = task_document_id(task)
    if status == "SUCCESS" and document_id:
        return TaskState(done=True, document_id=document_id)
    message = task_message(task)
    duplicate = DUPLICATE.search(message)
    if duplicate:
        return TaskState(done=True, document_id=int(duplicate.group(1)))
    return TaskState(done=True, error=message or _("Paperless could not process the file."))


def open_document(cfg, document_id, original=False):
    """
    Datei eines Dokuments als offene Antwort (zum Weiterreichen). Ohne
    ``original`` die von Paperless erzeugte PDF-Fassung, falls es sie gibt.
    """
    query = "?original=true" if original else ""
    return _open(cfg, f"documents/{int(document_id)}/download/{query}", timeout=60)
