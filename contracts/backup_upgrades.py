"""
Import älterer Backups (Version 0.2.0).

Je unterstützter Version steht hier der erwartete Migrationsstand der
eigenen Apps und ein Schritt, der die Datensätze aus data.json auf den
Stand der nächsten Version bringt. Vor dem Laden durchläuft ein Backup
alle Schritte ab seiner Version (Kette 0.1.0 -> 0.2.0 -> ...).

Eine neue Version ergänzt:
1. einen Eintrag in ``VERSIONS`` (am Ende, mit dem Migrationsstand),
2. bei der bisher letzten Version den Schritt zur neuen (``upgrade``);
   ändert sich am Datenbestand nichts, genügt ``no_changes``.
"""

from dataclasses import dataclass
from typing import Callable, Optional

# Apps, deren Migrationsstand zum Backup passen muss
OWN_APPS = ("contracts", "web", "assistant")

# Name der Standardgruppe, die Objekte anlegen darf (contracts.permissions.GROUP_USERS)
USERS_GROUP = "Benutzer"


@dataclass(frozen=True)
class BackupVersion:
    version: str
    # Letzte Migration je eigener App in dieser Version
    migrations: dict
    # Bringt die Datensätze auf den Stand der nächsten Version; None bei der aktuellen
    upgrade: Optional[Callable] = None


def no_changes(objects):
    return objects


def upgrade_0_1_0(objects):
    """
    0.1.0 -> 0.2.0: Verträge bekommen das Feld ``payment_method`` (leer),
    Zahlungsmethoden gibt es im Backup nicht. Das Kennzeichen für die
    Standard-Zahlungsmethoden ist nicht gesetzt, sie werden nach dem Import
    angelegt (contracts.payment_defaults). Die Gruppe "Benutzer" darf
    Zahlungsmethoden anlegen (sonst erst nach dem nächsten Start).
    """
    add_payment_method = ["add_paymentmethod", "contracts", "paymentmethod"]
    for row in objects:
        fields = row.get("fields")
        if not isinstance(fields, dict):
            continue
        if row.get("model") == "contracts.contract":
            fields.setdefault("payment_method", None)
        elif row.get("model") == "web.sitesettings":
            fields["default_payment_methods_created"] = False
        elif row.get("model") == "auth.group" and fields.get("name") == USERS_GROUP:
            permissions = fields.setdefault("permissions", [])
            if add_payment_method not in permissions:
                permissions.append(add_payment_method)
    return objects


# In aufsteigender Reihenfolge, die letzte ist die aktuelle Version
VERSIONS = (
    BackupVersion(
        "0.1.0",
        {
            "contracts": ["0014_contract_optional_consumer_start_date"],
            "web": ["0002_site_settings"],
            "assistant": ["0003_repair_columns_from_0_8a"],
        },
        upgrade_0_1_0,
    ),
    BackupVersion(
        "0.2.0",
        {
            "contracts": ["0015_payment_method"],
            "web": ["0003_default_payment_methods_created"],
            "assistant": ["0003_repair_columns_from_0_8a"],
        },
    ),
)

CURRENT = VERSIONS[-1]


def find(version):
    """Eintrag einer unterstützten Version oder None."""
    return next((entry for entry in VERSIONS if entry.version == version), None)


def parse(version):
    """
    "0.2.0" -> (0, 2, 0). Alles andere (z. B. "0.9.1 beta") ergibt None.
    """
    parts = str(version or "").split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def is_newer(version):
    """Stammt das Backup aus einer neueren Version als der installierten?"""
    parsed = parse(version)
    return parsed is not None and parsed > parse(CURRENT.version)


def upgrade(objects, version):
    """
    Bringt die Datensätze eines Backups der Version ``version`` auf den
    aktuellen Stand.
    """
    start = next(index for index, entry in enumerate(VERSIONS) if entry.version == version)
    for entry in VERSIONS[start:]:
        if entry.upgrade is not None:
            objects = entry.upgrade(objects)
    return objects
