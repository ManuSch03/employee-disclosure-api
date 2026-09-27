from rest_framework.permissions import BasePermission
from .resolution import can_view, can_edit


def requester_for(request):
    """Resolves the authenticated user to a Requester record.

    The critical security property: requester identity comes from the
    authenticated session or token, never from a value the caller
    supplies. An authenticated user with no linked Requester has no
    identity in this system and therefore no access.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    return getattr(user, "requester", None)


class HasDisclosurePermission(BasePermission):
    """DRF permission class wrapping the resolution engine, for any view
    whose objects are PersonData rows. Reads check view rights; writes
    check edit rights."""

    message = "Access denied."

    def has_permission(self, request, view):
        return requester_for(request) is not None

    def has_object_permission(self, request, view, obj):
        requester = requester_for(request)
        if requester is None:
            return False
        fn = can_view if request.method in ("GET", "HEAD", "OPTIONS") else can_edit
        return fn(obj.person, requester, obj.field_category, obj.field_name).granted


class IsPolicyAdmin(BasePermission):
    """Only staff may create or change relationships and organisation
    rules. Without this, any signed-in requester could grant itself
    access by writing a rule or a relationship -- which would bypass the
    whole disclosure model."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


class IsStaffOrReadOnly(BasePermission):
    """Anyone signed in may read; only staff may write."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return user.is_staff
