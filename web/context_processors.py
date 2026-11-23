from django.conf import settings

from . import update_check, whats_new


def version_notice(request):
    """
    Version und Update-Hinweis für die Seitenleiste, nur für Superuser
    (Version 0.5.0). Eine einfache Abfrage, kein Zugriff auf das Netz.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_superuser):
        return {}
    latest = update_check.available_update()
    return {
        "app_version": settings.APP_VERSION,
        "update_available": latest,
        "update_release_url": update_check.release_url(latest) if latest else "",
    }


def whats_new_notice(request):
    """
    Neuigkeiten seit dem letzten Update für das Fenster nach dem Anmelden
    (Version 0.10.0). Den Stand liest UserLanguageMiddleware mit.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {}
    entries = whats_new.pending(user, getattr(request, "seen_version", settings.APP_VERSION))
    return {"whats_new_pending": entries} if entries else {}
