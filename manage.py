#!/usr/bin/env python
import os
import sys


def drop_root():
    """
    Im Container nie als root arbeiten (Version 0.9.0): Seit der Container als
    root startet, laufen auch "docker compose exec app python manage.py ..."
    als root. Dann mit setpriv zum App-Benutzer wechseln, damit z. B. ein
    Import keine Dateien anlegt, die die App nicht mehr ändern kann.
    """
    if not os.environ.get("CONTRACTS_MANAGER_IMAGE") or not hasattr(os, "getuid") or os.getuid() != 0:
        return
    uid = os.environ.get("APP_UID", "1000")
    gid = os.environ.get("APP_GID", "1000")
    if not (uid.isdigit() and gid.isdigit()) or uid == "0":
        sys.exit("APP_UID und APP_GID müssen Zahlen sein, APP_UID nicht 0.")
    os.environ["HOME"] = "/tmp"
    os.execv("/usr/bin/setpriv", [
        "setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--inh-caps=-all",
        sys.executable, *sys.argv,
    ])


if __name__ == '__main__':
    drop_root()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)