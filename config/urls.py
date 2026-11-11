"""
URL configuration für config Projekt.

Die `urlpatterns` Liste leitet URLs zu Ansichten weiter. Für weitere Informationen siehe
https://docs.djangoproject.com/en/5.0/topics/http/urls/
"""

from django.contrib import admin
from django.urls import include, path, re_path

from .views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    # Mit und ohne Schrägstrich, damit Prüfwerkzeuge keine Weiterleitung bekommen
    re_path(r"^health/?$", health, name="health"),
    # Single Sign-on (OpenID Connect), 404 solange OIDC_ENABLED aus ist
    path("oidc/", include("mozilla_django_oidc.urls")),
    # Oberfläche (Frontend)
    path("assistant/", include("assistant.urls")),  # KI-Assistent (Version 0.8a)
    path("", include("web.urls")),
]