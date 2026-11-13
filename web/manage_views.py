"""
Bereich Administration im Frontend (Version 0.6 beta) unter /manage/.

- Benutzer und Gruppen: Administratoren (Gruppe "Administratoren") und
  Superuser, mit den Grenzen aus contracts.permissions.
- Eigentum übertragen und Erinnerungs-Übersicht: nur Superuser.
- System: Version, Worker, E-Mail, SSO; Test-E-Mail an sich selbst.
- Backup und Import (0.9 beta): nur Superuser, Logik in contracts.backup.
Ohne Recht gibt es 403. Das Django-Backend (/admin/) bleibt Superusern
vorbehalten.
"""

import logging
import tempfile
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.text import capfirst
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from contracts import backup
from contracts import permissions as perms
from contracts.mail import build_mail, user_language
from contracts.models import Contract, Reminder

from .manage_forms import BackupUploadForm, GroupForm, SiteSettingsForm, TransferOwnershipForm, UserForm
from .models import SiteSettings

logger = logging.getLogger(__name__)
User = get_user_model()


# --- Gemeinsame Bausteine -----------------------------------------------------

class ManageMixin(LoginRequiredMixin):
    """
    Prüft das globale Recht ``required`` = (Aktion, Modell), z. B.
    ("change", "user"). ``superuser_only`` für Bereiche nur für Superuser.
    """
    required = ("view", "user")
    superuser_only = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.superuser_only:
            allowed = request.user.is_active and request.user.is_superuser
        else:
            allowed = perms.can_manage(request.user, *self.required)
        if not allowed:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def send_account_mail(request, user):
    """
    Einladung (noch nie angemeldet oder kein lokales Passwort) bzw. Link zum
    Zurücksetzen des Passworts. Link 7 Tage gültig (PASSWORD_RESET_TIMEOUT).
    Gibt den Namen der Vorlage zurück.
    """
    name = "invitation" if user.last_login is None or not user.has_usable_password() else "password_reset"
    path = reverse("password_reset_confirm", kwargs={
        "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
        "token": default_token_generator.make_token(user),
    })
    context = {"user": user, "reset_url": request.build_absolute_uri(path), "invited_by": request.user}
    build_mail(name, context, [user.email], user_language(user)).send()
    return name


def user_badges(user):
    """
    Kennzeichen eines Benutzers für Listen und Detailseite.
    """
    group_names = {group.name for group in user.groups.all()}
    return {
        "superuser": user.is_superuser,
        "administrator": perms.GROUP_ADMINS in group_names,
        "inactive": not user.is_active,
        "never_logged_in": user.last_login is None,
        "sso_only": not user.has_usable_password(),
    }


# --- Benutzer -----------------------------------------------------------------

class UserListView(ManageMixin, TemplateView):
    template_name = "web/manage/users/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        q = self.request.GET.get("q", "").strip()
        users = User.objects.prefetch_related("groups").order_by("username")
        if q:
            users = users.filter(
                Q(username__icontains=q) | Q(first_name__icontains=q)
                | Q(last_name__icontains=q) | Q(email__icontains=q)
            )
        # Anzahl eigener Objekte je Benutzer über alle Modelle
        totals = {}
        for model in perms.owned_models():
            for row in model.objects.values("owner").annotate(total=Count("pk")):
                totals[row["owner"]] = totals.get(row["owner"], 0) + row["total"]
        context.update({
            "q": q,
            "rows": [
                {
                    "user": user,
                    "badges": user_badges(user),
                    "groups": [g.name for g in user.groups.all() if g.name != perms.GROUP_ALL],
                    "objects": totals.get(user.pk, 0),
                }
                for user in users
            ],
            "can_add": perms.can_manage(self.request.user, "add", "user"),
        })
        return context


class UserCreateView(ManageMixin, View):
    required = ("add", "user")
    template_name = "web/manage/users/form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": UserForm(actor=request.user)})

    def post(self, request):
        form = UserForm(request.POST, actor=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})
        user = form.save()
        messages.success(request, _("The user “%(name)s” has been created.") % {"name": user.get_username()})
        if form.cleaned_data.get("send_invitation"):
            notify_account_mail(request, user)
        return redirect("manage_user_detail", pk=user.pk)


def notify_account_mail(request, user):
    """
    Schickt Einladung bzw. Reset-Link und meldet das Ergebnis.
    """
    try:
        name = send_account_mail(request, user)
    except Exception:
        logger.exception("E-Mail an %s konnte nicht verschickt werden", user)
        messages.error(request, _("The email could not be sent. Please check the email settings under System."))
        return
    if name == "invitation":
        messages.success(request, _("An invitation has been sent to %(email)s.") % {"email": user.email})
    else:
        messages.success(request, _("A link to reset the password has been sent to %(email)s.") % {"email": user.email})


class UserObjectMixin(ManageMixin):
    def get_user(self, pk):
        return get_object_or_404(User, pk=pk)


class UserDetailView(UserObjectMixin, View):
    template_name = "web/manage/users/detail.html"

    def get(self, request, pk):
        target = self.get_user(pk)
        actor = request.user
        counts = perms.owned_object_counts(target)
        return render(request, self.template_name, {
            "target": target,
            "badges": user_badges(target),
            "groups": [g.name for g in target.groups.order_by("name")],
            "counts": counts,
            "owns_objects": any(counts.values()),
            "can_edit": perms.can_edit_user(actor, target),
            "can_change_status": perms.can_change_status(actor, target),
            "can_delete": perms.can_delete_user(actor, target),
            "can_transfer": actor.is_superuser and any(counts.values()),
        })


class UserUpdateView(UserObjectMixin, View):
    required = ("change", "user")
    template_name = "web/manage/users/form.html"

    def get_target(self, request, pk):
        target = self.get_user(pk)
        if not perms.can_edit_user(request.user, target):
            raise PermissionDenied
        return target

    def get(self, request, pk):
        target = self.get_target(request, pk)
        return render(request, self.template_name, {"form": UserForm(instance=target, actor=request.user), "target": target})

    def post(self, request, pk):
        target = self.get_target(request, pk)
        form = UserForm(request.POST, instance=target, actor=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "target": target})
        form.save()
        messages.success(request, _("The user has been saved."))
        return redirect("manage_user_detail", pk=target.pk)


class UserMailView(UserObjectMixin, View):
    """
    Einladung bzw. Link zum Zurücksetzen des Passworts erneut schicken.
    """
    required = ("change", "user")

    def post(self, request, pk):
        target = self.get_user(pk)
        if not perms.can_edit_user(request.user, target):
            raise PermissionDenied
        if not target.email:
            messages.error(request, _("The user has no email address."))
        elif not target.is_active:
            messages.error(request, _("The user is deactivated."))
        else:
            notify_account_mail(request, target)
        return redirect("manage_user_detail", pk=target.pk)


class UserActiveView(UserObjectMixin, View):
    """
    Benutzer deaktivieren oder wieder aktivieren (nicht das eigene Konto).
    """
    required = ("change", "user")

    def post(self, request, pk):
        target = self.get_user(pk)
        if not perms.can_change_status(request.user, target):
            raise PermissionDenied
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        if target.is_active:
            messages.success(request, _("The user has been activated."))
        else:
            messages.success(request, _("The user has been deactivated and can no longer sign in."))
        return redirect("manage_user_detail", pk=target.pk)


class UserDeleteView(UserObjectMixin, View):
    required = ("delete", "user")
    template_name = "web/manage/users/confirm_delete.html"

    def get_target(self, request, pk):
        target = self.get_user(pk)
        if not perms.can_delete_user(request.user, target):
            raise PermissionDenied
        return target

    def get(self, request, pk):
        return render(request, self.template_name, {"target": self.get_target(request, pk)})

    def post(self, request, pk):
        target = self.get_target(request, pk)
        name = target.get_username()
        target.delete()
        messages.success(request, _("The user “%(name)s” has been deleted.") % {"name": name})
        return redirect("manage_user_list")


class UserTransferView(UserObjectMixin, View):
    """
    Alle Objekte eines Benutzers an einen anderen übertragen (nur Superuser).
    """
    superuser_only = True
    template_name = "web/manage/users/transfer.html"

    def get(self, request, pk):
        target = self.get_user(pk)
        return render(request, self.template_name, {
            "target": target, "form": TransferOwnershipForm(from_user=target),
            "counts": perms.owned_object_counts(target),
        })

    def post(self, request, pk):
        target = self.get_user(pk)
        form = TransferOwnershipForm(request.POST, from_user=target)
        if not form.is_valid():
            return render(request, self.template_name, {
                "target": target, "form": form, "counts": perms.owned_object_counts(target),
            })
        new_owner = form.cleaned_data["new_owner"]
        counts = perms.transfer_all_ownership(target, new_owner, keep_access=form.cleaned_data["keep_access"])
        messages.success(request, _("%(count)s objects have been transferred to %(name)s.") % {
            "count": sum(counts.values()), "name": new_owner.get_username(),
        })
        return redirect("manage_user_detail", pk=target.pk)


# --- Gruppen ------------------------------------------------------------------

def group_share_counts():
    """
    Anzahl der Freigaben (Objekte) je Gruppe über alle Modelle.
    """
    from contracts.models import (
        CategoryGroupObjectPermission, ConsumerGroupObjectPermission,
        ContractGroupObjectPermission, ProviderGroupObjectPermission,
    )

    totals = {}
    for model in (
        ContractGroupObjectPermission, ProviderGroupObjectPermission,
        CategoryGroupObjectPermission, ConsumerGroupObjectPermission,
    ):
        rows = model.objects.values("group").annotate(objects=Count("content_object", distinct=True))
        for row in rows:
            totals[row["group"]] = totals.get(row["group"], 0) + row["objects"]
    return totals


class GroupListView(ManageMixin, TemplateView):
    required = ("view", "group")
    template_name = "web/manage/groups/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        actor = self.request.user
        shares = group_share_counts()
        can_change = perms.can_manage(actor, "change", "group")
        groups = Group.objects.annotate(members=Count("user", filter=Q(user__is_active=True))).order_by("name")
        context.update({
            "rows": [
                {
                    "group": group, "members": group.members, "shares": shares.get(group.pk, 0),
                    "protected": group.name in perms.PROTECTED_GROUPS,
                    "editable": can_change and group_is_editable(actor, group),
                }
                for group in groups
            ],
            "can_add": perms.can_manage(self.request.user, "add", "group"),
            "can_change": perms.can_manage(self.request.user, "change", "group"),
        })
        return context


def group_is_editable(actor, group):
    """
    "Alle" pflegt sich selbst, "Administratoren" nur durch Superuser.
    """
    if group.name == perms.GROUP_ALL:
        return False
    return group.name != perms.GROUP_ADMINS or actor.is_superuser


def check_group_editable(actor, group):
    if not group_is_editable(actor, group):
        raise PermissionDenied


class GroupFormView(ManageMixin, View):
    """
    Gruppe anlegen (ohne pk) oder bearbeiten (mit pk).
    """
    template_name = "web/manage/groups/form.html"

    def dispatch(self, request, *args, **kwargs):
        self.required = ("change", "group") if "pk" in kwargs else ("add", "group")
        return super().dispatch(request, *args, **kwargs)

    def get_group(self, request, pk):
        if pk is None:
            return Group()
        group = get_object_or_404(Group, pk=pk)
        check_group_editable(request.user, group)
        return group

    def context(self, form, group):
        return {
            "form": form, "group": group,
            "can_delete": group.pk and group.name not in perms.PROTECTED_GROUPS
            and perms.can_manage(self.request.user, "delete", "group"),
        }

    def get(self, request, pk=None):
        group = self.get_group(request, pk)
        return render(request, self.template_name, self.context(GroupForm(instance=group, actor=request.user), group))

    def post(self, request, pk=None):
        group = self.get_group(request, pk)
        form = GroupForm(request.POST, instance=group, actor=request.user)
        if not form.is_valid():
            return render(request, self.template_name, self.context(form, group))
        form.save()
        messages.success(request, _("The group “%(name)s” has been saved.") % {"name": group.name})
        return redirect("manage_group_list")


class GroupDeleteView(ManageMixin, View):
    required = ("delete", "group")
    template_name = "web/manage/groups/confirm_delete.html"

    def get_group(self, pk):
        group = get_object_or_404(Group, pk=pk)
        if group.name in perms.PROTECTED_GROUPS:
            raise PermissionDenied
        return group

    def get(self, request, pk):
        group = self.get_group(pk)
        return render(request, self.template_name, {"group": group, "shares": group_share_counts().get(group.pk, 0)})

    def post(self, request, pk):
        group = self.get_group(pk)
        name = group.name
        group.delete()
        messages.success(request, _("The group “%(name)s” has been deleted.") % {"name": name})
        return redirect("manage_group_list")


# --- Erinnerungen (nur Superuser) ---------------------------------------------

class ReminderOverviewView(ManageMixin, TemplateView):
    superuser_only = True
    template_name = "web/manage/reminders.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show_all = self.request.GET.get("show") == "all"
        reminders = (
            Reminder.objects.select_related("contract__provider")
            .prefetch_related("recipients")
            .order_by("-enabled", "remind_on")
        )
        if not show_all:
            reminders = reminders.filter(enabled=True)
        context.update({"reminders": reminders, "show_all": show_all})
        return context


# --- System -------------------------------------------------------------------

class SystemView(ManageMixin, View):
    """
    Version, Worker, E-Mail-Einstellungen (ohne Passwort), SSO. Test-E-Mail
    an die eigene Adresse. Seit 0.7 beta: Beschriftung der SSO-Schaltfläche
    (web.SiteSettings).
    """
    template_name = "web/manage/system.html"

    def worker_status(self):
        from django_q.models import Failure, Schedule, Success

        from contracts.apps import REMINDER_SCHEDULE_NAME

        schedule = Schedule.objects.filter(name=REMINDER_SCHEDULE_NAME).first()
        last_success = Success.objects.filter(func="contracts.tasks.send_due_reminders").order_by("-stopped").first()
        week_ago = timezone.now() - timedelta(days=7)
        return {
            "next_run": schedule.next_run if schedule else None,
            "last_success": last_success,
            # Seit dem Fix nur Zähler (dict); ältere Läufe haben einen Text
            "last_result": last_success.result if last_success and isinstance(last_success.result, dict) else None,
            # Mehr als gut einen Tag ohne Lauf: Worker vermutlich aus
            "stale": last_success is None or last_success.stopped < timezone.now() - timedelta(hours=26),
            "recent_failures": Failure.objects.filter(stopped__gte=week_ago).count(),
        }

    def context(self, settings_form=None):
        return {
            "settings_form": settings_form or SiteSettingsForm(instance=SiteSettings.load()),
            "version": settings.APP_VERSION,
            "worker": self.worker_status(),
            "email": {
                "smtp": bool(settings.EMAIL_HOST),
                "host": settings.EMAIL_HOST,
                "port": settings.EMAIL_PORT,
                "security": "SSL" if settings.EMAIL_USE_SSL else ("STARTTLS" if settings.EMAIL_USE_TLS else "–"),
                "user": settings.EMAIL_HOST_USER,
                "sender": settings.DEFAULT_FROM_EMAIL,
            },
            "app_base_url": settings.APP_BASE_URL,
            "sso": {
                "enabled": settings.OIDC_ENABLED,
                "base_url": settings.OIDC_OP_BASE_URL,
                "create_users": settings.OIDC_CREATE_USER,
            },
            "ai": {
                "enabled": settings.AI_ENABLED,
                "provider": settings.AI_PROVIDER,
                "model": settings.AI_MODEL,
                "base_url": settings.AI_BASE_URL,
                "users": User.objects.filter(is_active=True, groups__name=perms.GROUP_AI).count(),
            },
            "reminder_time": settings.REMINDER_SEND_TIME,
            "counts": {
                "users": User.objects.filter(is_active=True).count(),
                "contracts": Contract.objects.count(),
                "reminders": Reminder.objects.filter(enabled=True).count(),
            },
        }

    def get(self, request):
        return render(request, self.template_name, self.context())

    def post(self, request):
        if request.POST.get("action") == "settings":
            form = SiteSettingsForm(request.POST, instance=SiteSettings.load())
            if not form.is_valid():
                return render(request, self.template_name, self.context(settings_form=form))
            form.save()
            messages.success(request, _("The settings have been saved."))
            return redirect("manage_system")
        # Test-E-Mail an die eigene Adresse
        if not request.user.email:
            messages.error(request, _("Please enter your email address under Settings first."))
        else:
            try:
                build_mail("test", {"user": request.user}, [request.user.email], user_language(request.user)).send()
            except Exception as error:
                logger.exception("Test-E-Mail fehlgeschlagen")
                messages.error(request, _("The test email could not be sent: %(error)s") % {"error": error})
            else:
                messages.success(request, _("A test email has been sent to %(email)s.") % {"email": request.user.email})
        return redirect("manage_system")


# --- Backup und Import (nur Superuser, 0.9 beta) ------------------------------

# Modelle, deren Anzahl die Bestätigungsseite nennt
BACKUP_SUMMARY_MODELS = (
    "auth.user", "auth.group", "contracts.contract", "contracts.costentry", "contracts.document",
    "contracts.provider", "contracts.category", "contracts.consumer", "contracts.reminder",
    "assistant.conversation",
)


def backup_summary(info):
    """Inhalt eines Backups für die Anzeige: Zeitpunkt und Anzahl je Modell."""
    try:
        created_at = datetime.fromisoformat(info.created_at)
    except ValueError:
        created_at = None
    models = {model._meta.label_lower: model for model in backup.backup_models()}
    return {
        "created_at": created_at,
        "rows": [
            (capfirst(models[label]._meta.verbose_name_plural), info.counts.get(label, 0))
            for label in BACKUP_SUMMARY_MODELS
        ],
    }


class BackupView(ManageMixin, View):
    """
    Seite "Backup": Backup herunterladen und Backup hochladen. Ein Upload wird
    nur geprüft und abgelegt; ersetzt wird erst nach der Bestätigung
    (BackupImportView).
    """
    superuser_only = True
    template_name = "web/manage/backup.html"

    def get(self, request):
        return render(request, self.template_name, {"form": BackupUploadForm()})

    def post(self, request):
        form = BackupUploadForm(request.POST, request.FILES)
        if form.is_valid():
            token = backup.store_upload(form.cleaned_data["file"])
            try:
                info = backup.inspect_backup(backup.upload_path(token))
            except backup.BackupError as error:
                backup.discard_upload(token)
                form.add_error("file", str(error))
            else:
                return render(request, "web/manage/backup_confirm.html", {
                    "token": token,
                    "info": info,
                    "summary": backup_summary(info),
                    "no_users": not info.counts.get("auth.user"),
                    "smtp": bool(settings.EMAIL_HOST),
                    "installed_version": settings.APP_VERSION,
                })
        return render(request, self.template_name, {"form": form})


class BackupDownloadView(ManageMixin, View):
    """Erstellt ein Backup und liefert es als Download (ohne Passwörter)."""
    superuser_only = True
    http_method_names = ["post"]

    def post(self, request):
        # Zwischendatei statt Arbeitsspeicher: Dokumente können groß sein
        buffer = tempfile.TemporaryFile()
        info = backup.write_backup(buffer)
        buffer.seek(0)
        if info.missing_files:
            logger.warning("Backup ohne fehlende Dateien: %s", ", ".join(info.missing_files))
        logger.info("Backup erstellt von %s", request.user.get_username())
        return FileResponse(
            buffer, as_attachment=True, filename=backup.default_filename(), content_type="application/zip",
        )


class BackupImportView(ManageMixin, View):
    """
    Importiert das zuvor hochgeladene Backup (``token``) oder verwirft es
    (``cancel``). Der Import ersetzt den gesamten Bestand und beendet alle
    Sitzungen, auch die eigene.
    """
    superuser_only = True
    http_method_names = ["post"]

    def post(self, request):
        token = request.POST.get("token", "")
        path = backup.upload_path(token)
        if path is None:
            messages.error(request, _("The uploaded backup is no longer available. Please upload it again."))
            return redirect("manage_backup")
        if "cancel" in request.POST:
            backup.discard_upload(token)
            messages.info(request, _("The import was cancelled. Nothing has been changed."))
            return redirect("manage_backup")
        actor = request.user.get_username()
        try:
            backup.import_backup(path)
        except backup.BackupError as error:
            messages.error(request, str(error))
            return redirect("manage_backup")
        finally:
            backup.discard_upload(token)
        logger.info("Backup importiert von %s", actor)
        # Die eigene Sitzung gibt es nicht mehr
        logout(request)
        messages.success(request, _(
            "The backup has been imported. No user has a password now: "
            "choose a new one via “Forgot password?”."
        ))
        return redirect("login")
