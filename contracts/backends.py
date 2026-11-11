"""
Authentifizierungs-Backend für die Rolle "Eigentümer".

Der Eigentümer eines Objekts (Feld "owner") hat alle Rechte an diesem
Objekt. Damit funktioniert user.has_perm("contracts.change_contract", obj)
einheitlich für Eigentümer (dieses Backend) sowie Leser und Bearbeiter
(guardian.backends.ObjectPermissionBackend).
"""


class OwnerBackend:
    """
    Gibt dem Eigentümer alle Rechte am eigenen Objekt.
    Meldet niemanden an und vergibt keine globalen Rechte.
    """

    def authenticate(self, request, **credentials):
        return None

    def has_perm(self, user_obj, perm, obj=None):
        if obj is None or not getattr(user_obj, "is_active", False):
            return False
        owner_id = getattr(obj, "owner_id", None)
        if owner_id is None or owner_id != user_obj.pk:
            return False
        # Nur Rechte für genau dieses Modell, z. B. "contracts.view_contract"
        app_label, _, codename = perm.partition(".")
        opts = obj._meta
        return app_label == opts.app_label and codename.endswith(f"_{opts.model_name}")
