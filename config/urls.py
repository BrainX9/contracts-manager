"""
URL configuration für config Projekt.

Die `urlpatterns` Liste leitet URLs zu Ansichten weiter. Für weitere Informationen siehe
https://docs.djangoproject.com/en/5.0/topics/http/urls/
"""

from django.contrib import admin
from django.templatetags.static import static
from django.urls import include, path, re_path
from django.utils.functional import lazy
from django.views.generic import RedirectView

from .views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    # Mit und ohne Schrägstrich, damit Prüfwerkzeuge keine Weiterleitung bekommen
    re_path(r"^health/?$", health, name="health"),
    # Favicon für Seiten ohne eigenen Verweis (Django-Admin): Browser fragen
    # von sich aus /favicon.ico ab
    path("favicon.ico", RedirectView.as_view(url=lazy(static, str)("web/favicon.ico")), name="favicon"),
    # Single Sign-on (OpenID Connect), 404 solange OIDC_ENABLED aus ist
    path("oidc/", include("mozilla_django_oidc.urls")),
    # Oberfläche (Frontend)
    path("assistant/", include("assistant.urls")),  # KI-Assistent (Version 0.8a)
    path("", include("web.urls")),
]