from django.contrib import admin
from django.shortcuts import redirect


class AdminSite(admin.AdminSite):
    """
    Verwaltung nur für Superuser. Alle anderen Benutzer pflegen ihre Daten
    in der Oberfläche; angemeldete Benutzer ohne Zugang werden dorthin
    weitergeleitet statt erneut nach einem Login gefragt.
    """

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def login(self, request, extra_context=None):
        if request.method == "GET" and request.user.is_authenticated and not self.has_permission(request):
            return redirect("dashboard")
        return super().login(request, extra_context)
