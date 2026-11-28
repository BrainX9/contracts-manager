"""
Hinweis auf Neuerungen nach einem Update (Version 0.10.0).

Jeder Benutzer sieht nach einem Update einmal ein Fenster mit den Einträgen
der Versionen, die neuer sind als ``UserPreferences.seen_version`` (höchstens
``MAX_PENDING``, neueste zuerst). Schließen oder „Verstanden“ setzt
``seen_version`` auf ``APP_VERSION``. Neue Benutzer bekommen die aktuelle
Version gleich beim Anlegen; für sie ist nichts neu.

Die Texte stehen hier statt bei GitHub, damit sie ohne Internet und
übersetzt erscheinen. Eine Version ohne Eintrag (z. B. nur Fehler behoben)
zeigt kein Fenster. Punkte mit ``superuser_only`` sehen nur Superuser.
"""

from dataclasses import dataclass, field

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .update_check import parse_version, release_url

# Höchstzahl der Versionen im Fenster nach dem Anmelden
MAX_PENDING = 3


@dataclass(frozen=True)
class Item:
    text: str
    superuser_only: bool = False


@dataclass(frozen=True)
class Entry:
    version: str
    title: str
    items: tuple = field(default_factory=tuple)

    @property
    def release_url(self):
        return release_url(self.version)

    def visible_items(self, user):
        return [item for item in self.items if not item.superuser_only or user.is_superuser]


ENTRIES = (
    Entry("0.9.0", _("Paperless inside Contracts Manager"), (
        Item(_("If Paperless is connected via its API, Paperless documents open directly in Contracts "
               "Manager. Everyone who may read a contract can open its documents, even without an account "
               "in Paperless.")),
        Item(_("Uploaded documents can be transferred to Paperless with one button.")),
        Item(_("New page Administration › Paperless. Email, single sign-on and the AI assistant are now "
               "set up only in Administration."), superuser_only=True),
    )),
    Entry("0.9.2", _("The assistant spots duplicates"), (
        Item(_("The AI assistant points out contract partners, categories and consumers that are probably "
               "the same – for example several entries for one insurance company – and explains how to merge "
               "them.")),
        Item(_("Before it proposes a new contract partner, it looks for similar existing entries and "
               "proposes those instead. This applies to new conversations.")),
    )),
    Entry("0.10.0", _("What's new after an update"), (
        Item(_("After each update, this window shows once what has changed. You can read it again at any "
               "time under “What's new” in the user menu.")),
    )),
    Entry("0.11.0", _("System emails"), (
        Item(_("Members of the new group “Systemmail” receive this summary of what's new by email after each "
               "update. Administrators decide who is in the group.")),
        Item(_("Superusers in the group also receive an email when a new version is available. Superusers and "
               "administrators were added to the group with this update."), superuser_only=True),
    )),
    Entry("0.12.0", _("Several values per filter"), (
        Item(_("In the contract list, the filters for main category, category, consumer, payment method and "
               "contract partner now take several values – for example “Me” and “Household” together. "
               "Contracts matching any of the ticked values are shown.")),
    )),
)


def version_key(version):
    """Version als Zahlen zum Vergleichen; leer oder unbekannt = älter als alles."""
    return parse_version(version) or (0, 0, 0)


def entries_for(user, seen="", limit=None):
    """
    Einträge bis zur installierten Version, die neuer als ``seen`` sind,
    neueste zuerst; nur mit Punkten, die der Benutzer sehen darf.
    """
    current = version_key(settings.APP_VERSION)
    after = version_key(seen)
    result = []
    for entry in sorted(ENTRIES, key=lambda entry: version_key(entry.version), reverse=True):
        key = version_key(entry.version)
        if key > current or key <= after:
            continue
        items = entry.visible_items(user)
        if items:
            result.append((entry, items))
    return result[:limit] if limit else result


def pending(user, seen):
    """Einträge für das Fenster nach dem Anmelden (leer, wenn nichts neu ist)."""
    if not (user and user.is_authenticated) or seen == settings.APP_VERSION:
        return []
    return entries_for(user, seen, limit=MAX_PENDING)


def mark_seen(user):
    from .models import UserPreferences

    UserPreferences.objects.update_or_create(user=user, defaults={"seen_version": settings.APP_VERSION})


def set_for_new_user(sender, instance, created, raw=False, **kwargs):
    """Neue Benutzer: aktuelle Version als gesehen (nicht beim Import eines Backups)."""
    if not created or raw:
        return
    from .models import UserPreferences

    UserPreferences.objects.get_or_create(user=instance, defaults={"seen_version": settings.APP_VERSION})
