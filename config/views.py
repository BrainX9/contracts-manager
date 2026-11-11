"""
Projektweite Ansichten, die zu keiner App gehören.
"""

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
def health(request):
    """
    Zustandsprüfung für Docker und Monitoring.
    Antwortet mit 200, wenn die Datenbank erreichbar ist, sonst mit 503.
    Ohne Anmeldung aufrufbar und gibt keine Daten preis.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})
