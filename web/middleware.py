from django.conf import settings
from django.utils import translation


class UserLanguageMiddleware:
    """
    Aktiviert die Sprache aus den Einstellungen des angemeldeten Benutzers.

    Ohne Anmeldung (z. B. Login-Seite) gilt LANGUAGE_CODE (Deutsch). Die
    Sprache des Browsers wird bewusst nicht ausgewertet, damit die
    Oberfläche standardmäßig deutsch ist. Gilt auch für das Admin.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.available = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):
        language = settings.LANGUAGE_CODE
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            from .models import UserPreferences

            stored = (
                UserPreferences.objects.filter(user=user)
                .values_list("language", flat=True)
                .first()
            )
            if stored in self.available:
                language = stored

        translation.activate(language)
        request.LANGUAGE_CODE = language
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", language)
        translation.deactivate()
        return response
