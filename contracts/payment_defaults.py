"""
Standard-Zahlungsmethoden (Version 0.2.0).

- PayPal, American Express, Visa und Lastschrift werden einmalig angelegt,
  sobald es einen Superuser gibt. Es sind normale Datensätze: Eigentümer
  ist der erste Superuser, die Gruppe "Alle" darf sie lesen.
- Ob das schon geschehen ist, merkt sich
  ``web.SiteSettings.default_payment_methods_created``. Deshalb entstehen
  keine Duplikate, und ein gelöschter Standardwert kommt nicht wieder.
- Die Icons unter contracts/payment_icons/ sind selbst gezeichnete Kacheln
  (keine Logos der Anbieter) und werden nach MEDIA_ROOT kopiert.

Auslöser (verbunden in ContractsConfig.ready): nach den Migrationen und
beim Speichern eines Superusers.
"""

import logging
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files import File
from django.db import transaction

logger = logging.getLogger(__name__)

ICON_SOURCE_DIR = Path(__file__).resolve().parent / "payment_icons"

# Name und Quelldatei des Icons
DEFAULT_PAYMENT_METHODS = (
    ("PayPal", "paypal.png"),
    ("American Express", "american_express.png"),
    ("Visa", "visa.png"),
    ("Lastschrift", "direct_debit.png"),
)


def ensure_default_payment_methods():
    """
    Legt die Standardwerte an, falls das noch nicht geschehen ist und es
    einen Superuser gibt. Gibt die Anzahl der angelegten Werte zurück.
    """
    from web.models import SiteSettings

    from . import permissions as perms
    from .models import PaymentMethod

    owner = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
    if owner is None:
        return 0

    created = 0
    with transaction.atomic():
        SiteSettings.load()
        # Sperre, damit zwei gleichzeitige Starts (app und worker) nicht doppelt anlegen
        site_settings = SiteSettings.objects.select_for_update().get(pk=1)
        if site_settings.default_payment_methods_created:
            return 0
        group_all, _ = Group.objects.get_or_create(name=perms.GROUP_ALL)
        for name, icon_name in DEFAULT_PAYMENT_METHODS:
            # Gleichnamige Werte des Eigentümers bleiben, wie sie sind
            if PaymentMethod.objects.filter(owner=owner, name=name).exists():
                continue
            method = PaymentMethod(owner=owner, name=name)
            with open(ICON_SOURCE_DIR / icon_name, "rb") as source:
                # Die Quelldateien haben schon das Zielformat (PNG, 256 px)
                method.icon.save(icon_name, File(source), save=False)
            method.save()
            perms.grant_role(method, group_all, perms.READER)
            created += 1
        site_settings.default_payment_methods_created = True
        site_settings.save(update_fields=["default_payment_methods_created"])
    return created


def ensure_safely():
    """
    Wie ensure_default_payment_methods, aber ein Fehler (z. B. Ordner media
    nicht beschreibbar) bricht weder den Start noch das Speichern eines
    Benutzers ab. Beim nächsten Start wird es erneut versucht.
    """
    try:
        return ensure_default_payment_methods()
    except Exception:
        logger.exception("Die Standard-Zahlungsmethoden konnten nicht angelegt werden")
        return 0


# --- Signale (verbunden in ContractsConfig.ready) ------------------------------

def after_migrate(sender, **kwargs):
    # Update einer Installation, die schon einen Superuser hat
    ensure_safely()


def after_superuser_saved(sender, instance, raw=False, **kwargs):
    # Neuinstallation: ensure_admin oder createsuperuser nach den Migrationen.
    # Erst nach der Transaktion, damit der Benutzer sicher gespeichert ist.
    if raw or not instance.is_superuser:
        return
    transaction.on_commit(ensure_safely)
