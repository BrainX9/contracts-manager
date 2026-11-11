"""
Dokumente zu Verträgen (Version 0.7 beta).

- Zwei Quellen: hochgeladene Dateien und Verweise auf Paperless-ngx.
- Hochgeladene Dateien liegen unter
  ``documents/<Jahr>/<Vertragsreferenz>/<Dateiname>``, z. B.
  ``documents/2026/20260001/rechnung.pdf``. Das Jahr stammt aus der
  Referenz (Jahr der Anlage des Vertrags), die sich nie ändert.
- Der ursprüngliche Dateiname bleibt erhalten. Gibt es ihn im Ordner
  schon, wird ein Zähler angehängt: ``rechnung_2.pdf``, ``rechnung_3.pdf``.
- Erlaubt sind PDF, Bilder, Office-Dokumente, Text und E-Mails bis 20 MB.
- Ausgeliefert werden Dateien nur mit Leserecht am Vertrag
  (web.document_views.document_file), nicht als öffentliche Mediendatei.
"""

import os
import posixpath

from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.text import get_valid_filename
from django.utils.translation import gettext_lazy as _

DOCUMENT_DIR = "documents"
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
# Länge des Dateifelds (Pfad inklusive Ordner)
MAX_PATH_LENGTH = 255
# Länge des Dateinamens ohne Endung, damit der Pfad sicher passt
MAX_STEM_LENGTH = 120

# Erlaubte Endungen und der Inhaltstyp, mit dem sie ausgeliefert werden.
# Der vom Browser gemeldete Typ wird nicht verwendet.
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "txt": "text/plain; charset=utf-8",
    "eml": "message/rfc822",
}
ALLOWED_EXTENSIONS = tuple(CONTENT_TYPES)
# Diese Typen öffnet der Browser direkt (neuer Tab), alle anderen werden
# heruntergeladen.
INLINE_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "webp"}


def extension(name):
    return os.path.splitext(name)[1].lstrip(".").lower()


def content_type(name):
    return CONTENT_TYPES.get(extension(name), "application/octet-stream")


def is_inline(name):
    return extension(name) in INLINE_EXTENSIONS


def contract_folder(contract):
    """
    Ordner eines Vertrags, z. B. ``documents/2026/20260001``.
    """
    reference = contract.reference or ""
    year = reference[:4] if len(reference) >= 4 else str(timezone.localdate().year)
    return posixpath.join(DOCUMENT_DIR, year, reference or str(contract.pk))


def clean_filename(filename):
    """
    Dateiname ohne Pfad und ohne Sonderzeichen, Endung klein geschrieben,
    Name gekürzt. Leerzeichen werden zu Unterstrichen.
    """
    base = os.path.basename(filename.replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    try:
        stem = get_valid_filename(stem)
    except Exception:  # SuspiciousFileOperation bei leeren Namen
        stem = ""
    stem = stem[:MAX_STEM_LENGTH] or "document"
    return f"{stem}{ext.lower()}"


def available_name(storage, folder, filename):
    """
    Erster freier Name im Ordner: ``name.pdf``, sonst ``name_2.pdf`` usw.
    """
    stem, ext = os.path.splitext(filename)
    candidate = posixpath.join(folder, filename)
    counter = 2
    while storage.exists(candidate):
        candidate = posixpath.join(folder, f"{stem}_{counter}{ext}")
        counter += 1
    return candidate


def document_upload_to(instance, filename):
    """
    Speicherort einer hochgeladenen Datei (siehe Modulbeschreibung).
    """
    field = instance._meta.get_field("file")
    return available_name(field.storage, contract_folder(instance.contract), clean_filename(filename))


def validate_document_file(value):
    """
    Prüft Endung und Größe einer neu hochgeladenen Datei. Bereits
    gespeicherte Dateien werden nicht erneut geprüft.
    """
    if not value or getattr(value, "_committed", True):
        return
    if extension(value.name) not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            _("This file type is not allowed. Allowed: %(types)s."),
            code="document_type",
            params={"types": ", ".join(ext.upper() for ext in ALLOWED_EXTENSIONS if ext != "jpeg")},
        )
    if value.size > MAX_UPLOAD_SIZE:
        raise ValidationError(
            _("The file may be at most %(size)s."),
            code="document_too_large",
            params={"size": filesizeformat(MAX_UPLOAD_SIZE)},
        )


def delete_file_on_commit(field_file):
    """
    Löscht eine Datei erst nach erfolgreicher Transaktion und danach leere
    Ordner bis ``documents/``.
    """
    if not field_file:
        return
    storage, name = field_file.storage, field_file.name

    def delete():
        storage.delete(name)
        remove_empty_folders(storage, posixpath.dirname(name))

    transaction.on_commit(delete)


# Ordner, unterhalb derer leere Unterordner entfernt werden (auch Anhänge
# des KI-Assistenten, Version 0.8b)
CLEANUP_ROOTS = (DOCUMENT_DIR, "assistant")


def remove_empty_folders(storage, folder):
    """
    Entfernt leere Ordner (Vertrag, Jahr bzw. Unterhaltung), nie die
    obersten Ordner selbst.
    """
    root = folder.split("/", 1)[0] if folder else ""
    while folder and root in CLEANUP_ROOTS and folder != root and folder.startswith(f"{root}/"):
        try:
            os.rmdir(storage.path(folder))
        except (OSError, NotImplementedError):
            return
        folder = posixpath.dirname(folder)


# --- Signale (verbunden in ContractsConfig.ready) ------------------------------

def delete_file_with_document(sender, instance, **kwargs):
    """
    Datei löschen, wenn das Dokument gelöscht wird, auch zusammen mit dem
    Vertrag.
    """
    delete_file_on_commit(instance.file)
