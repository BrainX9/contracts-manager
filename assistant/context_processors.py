from contracts import permissions as perms


def assistant(request):
    """
    Ob der Link zum KI-Assistenten angezeigt wird (eingeschaltet und Recht).
    """
    user = getattr(request, "user", None)
    return {"assistant_available": bool(user) and perms.can_use_assistant(user)}
