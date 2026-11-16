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


def mail_config():
    """Gültige E-Mail-Einstellungen: Verwaltung vor .env (seit 0.4.0)."""
    from web import site_config

    return site_config.mail()


def absolute_url(path):
    """
    Vollständige Adresse für Links in E-Mails (Adresse der App + Pfad).
    """
    base_url = mail_config().base_url
    return f"{base_url}{path}" if base_url else path


def build_mail(name, context, to, language=None, config=None, connection=None):
    """
    Erzeugt eine E-Mail aus den Vorlagen ``emails/<name>*`` in der
    angegebenen Sprache. Gibt eine EmailMultiAlternatives zurück.
    ``config`` und ``connection`` nur für den Test ungespeicherter
    Einstellungen (Verwaltung › E-Mail).
    """
    language = language or settings.LANGUAGE_CODE
    config = config or mail_config()
    context = {"base_url": config.base_url, **context}
    with translation.override(language):
        subject = render_to_string(f"emails/{name}_subject.txt", context)
        # Betreff muss einzeilig sein
        subject = settings.EMAIL_SUBJECT_PREFIX + " ".join(subject.split())
        text = render_to_string(f"emails/{name}.txt", context)
        html = render_to_string(f"emails/{name}.html", context)
    message = EmailMultiAlternatives(subject, text, config.sender, to, connection=connection)
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
