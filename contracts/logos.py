"""
Logos der Vertragspartner (Version 0.4 beta).

- Erlaubt sind PNG, JPEG und WebP bis 2 MB. SVG ist bewusst nicht
  erlaubt, da es Skripte enthalten kann.
- Beim Speichern wird das Bild auf höchstens 256 px verkleinert und als
  PNG neu geschrieben. Dabei fallen Metadaten (z. B. GPS-Daten) weg.
- Dateinamen sind zufällig, damit sich Logos nicht erraten lassen.
- Ausgeliefert werden Logos nur mit Leserecht am Vertragspartner
  (web.views.provider_logo), nicht als öffentliche Mediendatei.
"""

import uuid
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError

LOGO_DIR = "logos"
MAX_UPLOAD_SIZE = 2 * 1024 * 1024
MAX_DIMENSION = 256
# Schutz vor sehr großen Bildern ("Dekompressionsbombe")
MAX_PIXELS = 25_000_000
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


def logo_upload_to(instance, filename):
    """
    Zufälliger Dateiname unter logos/, der ursprüngliche Name wird nicht
    verwendet.
    """
    return f"{LOGO_DIR}/{uuid.uuid4().hex}.png"


def validate_logo(value):
    """
    Prüft Größe, Format und Abmessungen einer neu hochgeladenen Datei.
    Bereits gespeicherte Logos werden nicht erneut geprüft.
    """
    if not value or getattr(value, "_committed", True):
        return
    if value.size > MAX_UPLOAD_SIZE:
        raise ValidationError(
            _("The logo may be at most %(size)s."),
            code="logo_too_large",
            params={"size": filesizeformat(MAX_UPLOAD_SIZE)},
        )
    try:
        value.seek(0)
        with Image.open(value) as image:
            image_format = image.format
            width, height = image.size
    except (UnidentifiedImageError, OSError):
        raise ValidationError(_("The file is not a valid image."), code="logo_invalid")
    finally:
        value.seek(0)
    if image_format not in ALLOWED_FORMATS:
        raise ValidationError(
            _("Only PNG, JPEG and WebP images are allowed."), code="logo_format"
        )
    if width * height > MAX_PIXELS:
        raise ValidationError(_("The image is too large."), code="logo_pixels")


def process_logo(file):
    """
    Verkleinert das Bild auf höchstens MAX_DIMENSION Pixel und schreibt es
    ohne Metadaten als PNG neu. Gibt eine ContentFile zurück.
    """
    file.seek(0)
    with Image.open(file) as image:
        # Drehung aus den EXIF-Daten übernehmen, bevor sie wegfallen
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
    return ContentFile(output.getvalue())


def delete_file_on_commit(field_file):
    """
    Löscht eine Datei erst nach erfolgreicher Transaktion, damit bei einem
    Fehler das Logo nicht verloren geht.
    """
    if not field_file:
        return
    storage, name = field_file.storage, field_file.name
    transaction.on_commit(lambda: storage.delete(name))


# --- Signale (verbunden in ContractsConfig.ready) ------------------------------

def remember_old_logo(sender, instance, raw=False, **kwargs):
    """
    Merkt sich vor dem Speichern das bisherige Logo, um es nach dem
    Ersetzen oder Entfernen zu löschen.
    """
    instance._old_logo = None
    if raw or instance.pk is None:
        return
    old = sender.objects.filter(pk=instance.pk).values_list("logo", flat=True).first()
    if old and old != instance.logo.name:
        field = sender._meta.get_field("logo")
        instance._old_logo = field.attr_class(instance, field, old)


def delete_replaced_logo(sender, instance, raw=False, **kwargs):
    old = getattr(instance, "_old_logo", None)
    if old:
        delete_file_on_commit(old)
        instance._old_logo = None


def delete_logo_with_provider(sender, instance, **kwargs):
    delete_file_on_commit(instance.logo)
