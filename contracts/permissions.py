"""
Berechtigungen pro Objekt (Version 0.3a beta).

Rollen und ihre Rechte:
- Eigentümer: Feld "owner" am Objekt, alle Rechte (contracts.backends.OwnerBackend)
- Bearbeiter: guardian-Rechte "view_<modell>" und "change_<modell>"
- Leser:      guardian-Recht "view_<modell>"

Leser und Bearbeiter können Benutzer oder Gruppen sein. Rollen werden
immer als vollständiges Bündel gesetzt (grant_role, set_roles), damit
keine unvollständigen Kombinationen entstehen.

Kosteneinträge haben keine eigenen Rechte, sie folgen ihrem Vertrag.

Alle Prüfungen laufen über user.has_perm(perm, obj) bzw. diese Funktionen,
damit Admin, Frontend und spätere Schnittstellen dieselbe Logik nutzen.
"""

from django.contrib.auth.models import Group
from django.db.models import Q
from guardian.shortcuts import (
    assign_perm,
    get_groups_with_perms,
    get_objects_for_user,
    get_users_with_perms,
    remove_perm,
)

# Namen der Standardgruppen
GROUP_USERS = "Benutzer"  # darf Objekte anlegen
GROUP_ALL = "Alle"        # enthält alle Benutzer, für Freigaben an alle
GROUP_ADMINS = "Administratoren"  # pflegt Benutzer und Gruppen im Frontend (0.6)
GROUP_AI = "KI"  # darf den KI-Assistenten nutzen (0.8a)
# Standardgruppen lassen sich nicht umbenennen oder löschen
PROTECTED_GROUPS = (GROUP_USERS, GROUP_ALL, GROUP_ADMINS, GROUP_AI)

READER = "reader"
EDITOR = "editor"

# Rechte je Rolle (Aktionen der Standardrechte von Django)
ROLE_ACTIONS = {
    READER: ("view",),
    EDITOR: ("view", "change"),
}
SHARED_ACTIONS = ("view", "change")


def perm_name(model, action):
    """
    Vollständiger Rechtename, z. B. "contracts.view_contract".
    """
    opts = model._meta
    return f"{opts.app_label}.{action}_{opts.model_name}"


# --- Prüfungen ---------------------------------------------------------------

# Modelle ohne eigene Rechte, die ihrem Vertrag folgen (Feld "contract")
CONTRACT_CHILD_MODELS = {"costentry", "document"}


def _follows_contract(obj):
    """
    Kosteneintrag oder Dokument (Version 0.7 beta): Rechte folgen dem Vertrag.
    """
    return obj._meta.model_name in CONTRACT_CHILD_MODELS


def can_view(user, obj):
    if _follows_contract(obj):
        # Kosteneinträge lesen, wer den Vertrag lesen darf
        obj = obj.contract
    return user.has_perm(perm_name(type(obj), "view"), obj)


def can_change(user, obj):
    if _follows_contract(obj):
        # Kosteneinträge ändern, wer den Vertrag bearbeiten darf
        obj = obj.contract
    return user.has_perm(perm_name(type(obj), "change"), obj)


def can_delete(user, obj):
    if _follows_contract(obj):
        # Kosteneintrag löschen ist Teil der Bearbeitung des Vertrags
        return can_change(user, obj.contract)
    # "delete" vergibt nur das OwnerBackend (bzw. Superuser)
    return user.has_perm(perm_name(type(obj), "delete"), obj)


def can_share(user, obj):
    """
    Eigentümer wechseln und Freigaben ändern: nur Eigentümer und Superuser.
    """
    if not user.is_active:
        return False
    return user.is_superuser or (obj.pk is not None and obj.owner_id == user.pk)


# --- Abfragen ----------------------------------------------------------------

def _filter_for(user, queryset, action):
    if not user.is_active:
        return queryset.none()
    if user.is_superuser:
        return queryset
    if queryset.model._meta.model_name in CONTRACT_CHILD_MODELS:
        # Kosteneinträge und Dokumente folgen ihrem Vertrag
        from .models import Contract
        contracts = _filter_for(user, Contract.objects.all(), action)
        return queryset.filter(contract__in=contracts)
    shared = get_objects_for_user(
        user,
        perm_name(queryset.model, action),
        klass=queryset.model,
        # Globale Rechte zählen hier nicht, nur Rechte am Objekt
        accept_global_perms=False,
    ).values("pk")
    return queryset.filter(Q(owner=user) | Q(pk__in=shared))


def visible_to(user, queryset):
    """
    Objekte, die der Benutzer lesen darf (Eigentümer, Leser, Bearbeiter).
    """
    return _filter_for(user, queryset, "view")


def editable_by(user, queryset):
    """
    Objekte, die der Benutzer bearbeiten darf (Eigentümer, Bearbeiter).
    """
    return _filter_for(user, queryset, "change")


def visible_or_current(user, queryset, current_pk=None):
    """
    Auswahl für Formulare: lesbare Objekte plus der aktuell gewählte Wert.
    So kann ein Bearbeiter speichern, auch wenn er z. B. den Verbraucher
    eines freigegebenen Vertrags selbst nicht sehen darf.
    """
    visible = visible_to(user, queryset)
    if current_pk is None:
        return visible
    return queryset.filter(Q(pk__in=visible.values("pk")) | Q(pk=current_pk))


def users_who_can_view(obj):
    """
    Aktive Benutzer, die das Objekt lesen dürfen: Eigentümer, Leser und
    Bearbeiter (direkt oder über Gruppen) sowie Superuser. Z. B. als
    mögliche Empfänger einer Erinnerung.
    """
    from django.contrib.auth import get_user_model

    shared = get_users_with_perms(
        obj, with_superusers=True, with_group_users=True,
        only_with_perms_in=[perm_name(type(obj), "view").split(".")[1]],
    )
    return (
        get_user_model().objects
        .filter(Q(pk=obj.owner_id) | Q(pk__in=[user.pk for user in shared]) | Q(is_superuser=True))
        .filter(is_active=True)
        .order_by("username")
    )


# --- Rollen setzen und lesen -------------------------------------------------

def revoke_roles(obj, target):
    """
    Entzieht einem Benutzer oder einer Gruppe alle Rollen am Objekt.
    """
    for action in SHARED_ACTIONS:
        remove_perm(perm_name(type(obj), action), target, obj)


def grant_role(obj, target, role):
    """
    Gibt einem Benutzer oder einer Gruppe genau eine Rolle am Objekt.
    """
    if role not in ROLE_ACTIONS:
        raise ValueError(f"Unbekannte Rolle: {role}")
    revoke_roles(obj, target)
    for action in ROLE_ACTIONS[role]:
        assign_perm(perm_name(type(obj), action), target, obj)


def _role_from_codenames(codenames, model):
    model_name = model._meta.model_name
    if f"change_{model_name}" in codenames:
        return EDITOR
    if f"view_{model_name}" in codenames:
        return READER
    return None


def get_roles(obj):
    """
    Liefert die Freigaben eines Objekts:
    {"reader": {"users": set, "groups": set}, "editor": {...}}
    """
    roles = {role: {"users": set(), "groups": set()} for role in ROLE_ACTIONS}
    model = type(obj)
    users = get_users_with_perms(
        obj, attach_perms=True, with_superusers=False, with_group_users=False
    )
    for user, codenames in users.items():
        role = _role_from_codenames(codenames, model)
        if role:
            roles[role]["users"].add(user)
    for group, codenames in get_groups_with_perms(obj, attach_perms=True).items():
        role = _role_from_codenames(codenames, model)
        if role:
            roles[role]["groups"].add(group)
    return roles


def set_roles(obj, reader_users=(), reader_groups=(), editor_users=(), editor_groups=()):
    """
    Setzt die Freigaben eines Objekts vollständig auf die angegebenen Werte.
    Wer in beiden Rollen steht, wird Bearbeiter. Der Eigentümer braucht
    keine Freigabe und wird übersprungen.
    """
    wanted = {}
    for target in reader_users:
        wanted[target] = READER
    for target in reader_groups:
        wanted[target] = READER
    for target in editor_users:
        wanted[target] = EDITOR
    for target in editor_groups:
        wanted[target] = EDITOR
    wanted = {
        target: role for target, role in wanted.items()
        if not (getattr(target, "pk", None) == obj.owner_id and not isinstance(target, Group))
    }

    current = {}
    for role, entries in get_roles(obj).items():
        for target in entries["users"] | entries["groups"]:
            current[target] = role

    for target in current.keys() - wanted.keys():
        revoke_roles(obj, target)
    for target, role in wanted.items():
        if current.get(target) != role:
            grant_role(obj, target, role)


def transfer_ownership(obj, new_owner):
    """
    Übergibt ein Objekt an einen neuen Eigentümer. Der bisherige Eigentümer
    wird automatisch Bearbeiter, damit er nicht versehentlich den Zugriff
    verliert; die Rolle lässt sich danach wieder entfernen.
    Gibt den bisherigen Eigentümer zurück (oder None, wenn sich nichts ändert).
    """
    previous = obj.owner
    if previous.pk == new_owner.pk:
        return None
    obj.owner = new_owner
    obj.save()
    # Falls der neue Eigentümer vorher eine Rolle hatte, ist sie überflüssig
    revoke_roles(obj, new_owner)
    grant_role(obj, previous, EDITOR)
    return previous


# --- Verwaltung von Benutzern und Gruppen (Version 0.6 beta) -----------------
# Administratoren (Gruppe "Administratoren") haben die globalen Django-Rechte
# auth.*_user und auth.*_group. Superuser dürfen alles. Damit sich
# Administratoren keine weiteren Rechte verschaffen, gelten Grenzen:
# keine Superuser bearbeiten, die Gruppe "Administratoren" nur durch
# Superuser vergeben, eigene Gruppen und eigenen Status nicht ändern.

def can_use_assistant(user):
    """
    KI-Assistent (Version 0.8a): eingeschaltet (AI_ENABLED) und Mitglied der
    Gruppe "KI" (Recht assistant.use_assistant) oder Superuser.
    """
    from django.conf import settings

    return bool(
        getattr(settings, "AI_ENABLED", False)
        and user.is_authenticated and user.is_active
        and user.has_perm("assistant.use_assistant")
    )


def can_manage(user, action, model_name):
    """
    Globales Recht für die Verwaltung, z. B. can_manage(user, "change", "user").
    """
    return user.is_active and (user.is_superuser or user.has_perm(f"auth.{action}_{model_name}"))


def can_edit_user(actor, target):
    """
    Darf ``actor`` den Benutzer ``target`` bearbeiten? Superuser bearbeiten
    nur Superuser selbst.
    """
    if not can_manage(actor, "change", "user"):
        return False
    return actor.is_superuser or not target.is_superuser


def can_change_status(actor, target):
    """
    Aktiv/inaktiv und Gruppen: nie am eigenen Konto.
    """
    return can_edit_user(actor, target) and actor.pk != target.pk


def can_delete_user(actor, target):
    """
    Löschen: nicht sich selbst, keine Superuser (außer durch Superuser) und
    nur ohne eigene Objekte (sonst PROTECT, vorher Eigentum übertragen).
    """
    return (
        can_manage(actor, "delete", "user")
        and actor.pk != target.pk
        and (actor.is_superuser or not target.is_superuser)
        and not any(owned_object_counts(target).values())
    )


def assignable_groups(actor):
    """
    Gruppen, die ``actor`` vergeben darf: ohne "Alle" (automatisch) und
    "Administratoren" nur für Superuser.
    """
    groups = Group.objects.exclude(name=GROUP_ALL).order_by("name")
    if not actor.is_superuser:
        groups = groups.exclude(name=GROUP_ADMINS)
    return groups


def owned_models():
    from .models import Category, Consumer, Contract, Provider

    return (Contract, Provider, Category, Consumer)


def owned_object_counts(user):
    """
    Anzahl der Objekte, die einem Benutzer gehören, je Modell.
    """
    return {model._meta.model_name: model.objects.filter(owner=user).count() for model in owned_models()}


def ownership_conflicts(from_user, to_user):
    """
    Objekte, die beim Übertragen gegen die Eindeutigkeit pro Eigentümer
    verstoßen würden (gleicher Name beim neuen Eigentümer).
    """
    from .models import Category, Consumer, Provider

    conflicts = []
    for model in (Provider, Consumer):
        names = model.objects.filter(owner=to_user).values_list("name", flat=True)
        for obj in model.objects.filter(owner=from_user, name__in=list(names)):
            conflicts.append(obj)
    target = set(Category.objects.filter(owner=to_user).values_list("parent_id", "name"))
    for obj in Category.objects.filter(owner=from_user):
        if (obj.parent_id, obj.name) in target:
            conflicts.append(obj)
    return conflicts


def transfer_all_ownership(from_user, to_user, keep_access=False):
    """
    Überträgt alle Objekte eines Benutzers an einen anderen (nur Superuser,
    siehe web.manage_views). Mit ``keep_access`` wird der bisherige
    Eigentümer Bearbeiter. Gibt die Anzahl je Modell zurück.
    Vorher ownership_conflicts prüfen.
    """
    from django.db import transaction

    counts = {}
    with transaction.atomic():
        for model in owned_models():
            objects = list(model.objects.filter(owner=from_user))
            for obj in objects:
                obj.owner = to_user
                obj.save()
                revoke_roles(obj, to_user)
                if keep_access:
                    grant_role(obj, from_user, EDITOR)
            counts[model._meta.model_name] = len(objects)
    return counts
