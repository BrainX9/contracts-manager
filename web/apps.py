from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class WebConfig(AppConfig):
    """
    Oberfläche (Frontend) mit Django-Templates und HTMX.
    Die Fachlogik und alle Berechtigungen liegen in der App "contracts".
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "web"
    verbose_name = _("Web interface")
