from django.contrib.admin.apps import AdminConfig as BaseAdminConfig


class AdminConfig(BaseAdminConfig):
    """
    Django-Admin mit eigener AdminSite (nur Superuser), siehe config/admin.py.
    """
    default_site = "config.admin.AdminSite"
