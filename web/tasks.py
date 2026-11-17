"""
Aufgaben für den Hintergrund-Worker (django-q2). Der tägliche Zeitplan wird
in WebConfig.ready angelegt.
"""

from . import update_check


def check_for_updates():
    """
    Täglich: neueste Version bei GitHub erfragen (Version 0.5.0). Ergebnis
    für das Protokoll des Workers: die gefundene Version oder leer.
    """
    return update_check.check() or ""
