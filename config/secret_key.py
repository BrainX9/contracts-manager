"""
SECRET_KEY ohne .env (Version 0.9.0).

Ist ``SECRET_KEY`` nicht gesetzt, erzeugt die App beim ersten Start einen
Schlüssel und legt ihn im Ordner ``CONFIG_DIR`` ab (im Container ``/config``,
auf dem Host der Ordner ``config`` neben der Compose-Datei). App und Worker
lesen ihn von dort. Die Datei ist nur für den App-Benutzer lesbar (600) und
nicht Teil eines Backups. Geht sie verloren, entsteht ein neuer Schlüssel:
Alle sind abgemeldet, offene Einladungs- und Reset-Links verfallen; Daten
gehen nicht verloren.
"""

import os
import secrets
import time
from pathlib import Path

FILE_NAME = "secret_key"


class SecretKeyError(Exception):
    pass


def read_key(path):
    # Ein zweiter Prozess schreibt gerade: kurz warten, bis Inhalt da ist
    for _attempt in range(20):
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
        time.sleep(0.1)
    raise SecretKeyError(f"{path} ist leer. Datei löschen, dann wird beim nächsten Start ein neuer Schlüssel erzeugt.")


def load_or_create(directory):
    """Schlüssel aus ``directory/secret_key``; fehlt die Datei, wird sie angelegt."""
    directory = Path(directory)
    path = directory / FILE_NAME
    try:
        return read_key(path)
    except FileNotFoundError:
        pass
    except PermissionError:
        raise SecretKeyError(
            f"{path} ist nicht lesbar. Der Ordner muss dem Benutzer der App gehören (APP_UID/APP_GID)."
        ) from None
    if not directory.is_dir():
        raise SecretKeyError(
            f"SECRET_KEY ist nicht gesetzt und der Ordner {directory} fehlt. Entweder SECRET_KEY setzen oder "
            "den Ordner config einbinden (siehe docker-compose.yml)."
        )
    key = secrets.token_urlsafe(50)
    temporary = directory / f".{FILE_NAME}.{os.getpid()}"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(key)
        try:
            # Atomar und nur, wenn es die Datei noch nicht gibt (App und Worker)
            os.link(temporary, path)
        except FileExistsError:
            return read_key(path)
        finally:
            temporary.unlink(missing_ok=True)
    except PermissionError:
        raise SecretKeyError(
            f"In {directory} kann kein Schlüssel angelegt werden. Der Ordner muss dem Benutzer der App gehören "
            "(APP_UID/APP_GID) – oder SECRET_KEY setzen."
        ) from None
    return key
