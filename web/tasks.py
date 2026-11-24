"""
Aufgaben für den Hintergrund-Worker (django-q2). Die Zeitpläne werden in
WebConfig.ready angelegt.
"""

from . import system_mail, update_check


def check_for_updates():
    """
    Täglich: neueste Version bei GitHub erfragen (Version 0.5.0) und bei
    einer neuen Version einmal die Superuser der Gruppe "Systemmail"
    benachrichtigen (0.11.0). Ergebnis für das Protokoll des Workers: die
    gefundene Version oder leer.
    """
    latest = update_check.check()
    if latest:
        system_mail.notify_update(latest)
    return latest or ""


def announce_release():
    """
    Kurz nach dem Start einer neuen Version: Neuigkeiten per E-Mail an die
    Gruppe "Systemmail" (Version 0.11.0). Ergebnis: Anzahl der E-Mails.
    """
    return system_mail.announce_release()
