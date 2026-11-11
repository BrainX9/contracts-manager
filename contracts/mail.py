"""
E-Mail-Versand mit Vorlagen (Version 0.5 beta).

Jede E-Mail besteht aus drei Vorlagen unter ``templates/emails/``:
``<name>_subject.txt`` (Betreff), ``<name>.txt`` (Text) und
``<name>.html`` (HTML). Sie werden in der Sprache des Empfängers
erzeugt (``web.UserPreferences.language``, sonst Standardsprache).
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import translation


def user_language(user):
    """
    Sprache eines Benutzers aus seinen Einstellungen, sonst Standardsprache.
    """
    # Fehlende Einstellungen lösen RelatedObjectDoesNotExist (AttributeError) aus
    preferences = getattr(user, "preferences", None)
    return preferences.language if preferences else settings.LANGUAGE_CODE


def absolute_url(path):
    """
    Vollständige Adresse für Links in E-Mails (APP_BASE_URL + Pfad).
    """
    return f"{settings.APP_BASE_URL}{path}" if settings.APP_BASE_URL else path


def build_mail(name, context, to, language=None):
    """
    Erzeugt eine E-Mail aus den Vorlagen ``emails/<name>*`` in der
    angegebenen Sprache. Gibt eine EmailMultiAlternatives zurück.
    """
    language = language or settings.LANGUAGE_CODE
    context = {"base_url": settings.APP_BASE_URL, **context}
    with translation.override(language):
        subject = render_to_string(f"emails/{name}_subject.txt", context)
        # Betreff muss einzeilig sein
        subject = settings.EMAIL_SUBJECT_PREFIX + " ".join(subject.split())
        text = render_to_string(f"emails/{name}.txt", context)
        html = render_to_string(f"emails/{name}.html", context)
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, to)
    message.attach_alternative(html, "text/html")
    return message


def send_mail_to_user(name, context, user):
    """
    Sendet eine E-Mail an einen Benutzer in dessen Sprache.
    Gibt False zurück, wenn der Benutzer keine E-Mail-Adresse hat.
    """
    if not user.email:
        return False
    build_mail(name, {"recipient": user, **context}, [user.email], user_language(user)).send()
    return True
