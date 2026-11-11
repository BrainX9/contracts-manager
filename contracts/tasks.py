"""
Aufgaben für den Hintergrund-Worker (django-q2, Version 0.5 beta).
Der tägliche Zeitplan wird in ContractsConfig.ready angelegt.
"""

from . import reminders


def send_due_reminders():
    """
    Täglicher Versand der Erinnerungen. Das Ergebnis (nur Zähler) zeigt
    Verwaltung › System übersetzt an.
    """
    return reminders.send_due_reminders().as_dict()
