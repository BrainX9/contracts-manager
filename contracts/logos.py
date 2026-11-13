"""
Logos der Vertragspartner (Version 0.4 beta) und Icons der
Zahlungsmethoden (Version 0.2.0).

- Erlaubt sind PNG, JPEG und WebP bis 2 MB. SVG ist bewusst nicht
  erlaubt, da es Skripte enthalten kann.
- Beim Speichern wird das Bild auf höchstens 256 px verkleinert und als
  PNG neu geschrieben. Dabei fallen Metadaten (z. B. GPS-Daten) weg.
- Dateinamen sind zufällig, damit sich Logos nicht erraten lassen.
- Ausgeliefert werden Logos nur mit Leserecht am Vertragspartner
  (web.views.provider_logo), nicht als öffentliche Mediendatei.
- Für Icons der Zahlungsmethoden gelten dieselben Regeln, sie liegen
  unter payment_icons/.
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
PAYMENT_ICON_DIR = "payment_icons"
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


def payment_icon_upload_to(instance, filename):
    """
    Zufälliger Dateiname unter payment_icons/.
    """
    return f"{PAYMENT_ICON_DIR}/{uuid.uuid4().hex}.png"


def validate_logo(value):
    """
    Prüfung für Provider.logo. Der Name bleibt, weil die Migration 0009
    darauf verweist.
    """
    validate_image(value)


def validate_image(value):
    """
    Prüft Größe, Format und Abmessungen einer neu hochgeladenen Datei.
    Bereits gespeicherte Bilder werden nicht erneut geprüft.
    """
    if not value or getattr(value, "_committed", True):
        return
    if value.size > MAX_UPLOAD_SIZE:
        raise ValidationError(
            _("The image may be at most %(size)s."),
            code="image_too_large",
            params={"size": filesizeformat(MAX_UPLOAD_SIZE)},
        )
    try:
        value.seek(0)
        with Image.open(value) as image:
            image_format = image.format
            width, height = image.size
    except (UnidentifiedImageError, OSError):
        raise ValidationError(_("The file is not a valid image."), code="image_invalid")
    finally:
        value.seek(0)
    if image_format not in ALLOWED_FORMATS:
        raise ValidationError(
            _("Only PNG, JPEG and WebP images are allowed."), code="image_format"
        )
    if width * height > MAX_PIXELS:
        raise ValidationError(_("The image is too large."), code="image_pixels")


def process_image(file):
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
    Fehler das Bild nicht verloren geht.
    """
    if not field_file:
        return
    storage, name = field_file.storage, field_file.name
    transaction.on_commit(lambda: storage.delete(name))


# --- Signale (verbunden in ContractsConfig.ready) ------------------------------

def connect_file_cleanup(model, field_name):
    """
    Löscht die Datei eines Bildfelds, wenn sie ersetzt oder entfernt wird
    oder das Objekt gelöscht wird. Gilt für Provider.logo und
    PaymentMethod.icon.
    """
    from django.db.models.signals import post_delete, post_save, pre_save

    attribute = f"_old_{field_name}"

    def remember_old_file(sender, instance, raw=False, **kwargs):
        # Merkt sich vor dem Speichern die bisherige Datei
        setattr(instance, attribute, None)
        if raw or instance.pk is None:
            return
        old = sender.objects.filter(pk=instance.pk).values_list(field_name, flat=True).first()
        if old and old != getattr(instance, field_name).name:
            field = sender._meta.get_field(field_name)
            setattr(instance, attribute, field.attr_class(instance, field, old))

    def delete_replaced_file(sender, instance, raw=False, **kwargs):
        old = getattr(instance, attribute, None)
        if old:
            delete_file_on_commit(old)
            setattr(instance, attribute, None)

    def delete_file_with_object(sender, instance, **kwargs):
        delete_file_on_commit(getattr(instance, field_name))

    uid = f"contracts_{model._meta.model_name}_{field_name}"
    # weak=False: die Funktionen leben nur in diesem Aufruf
    pre_save.connect(remember_old_file, sender=model, weak=False, dispatch_uid=f"{uid}_remember")
    post_save.connect(delete_replaced_file, sender=model, weak=False, dispatch_uid=f"{uid}_replaced")
    post_delete.connect(delete_file_with_object, sender=model, weak=False, dispatch_uid=f"{uid}_deleted")
