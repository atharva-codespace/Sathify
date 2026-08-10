"""
Module 1.3 — Role-Based Access Control.

Every endpoint in every module attaches permissions from this file. Two rules
keep authorisation honest across the codebase:

1. **Role is not enough.** Most endpoints need role AND approval AND society
   scope. Checking only the role is the mistake this module exists to prevent,
   so the composed classes at the bottom are what most viewsets should use.
2. **Society isolation is enforced on the queryset, not just the permission.**
   A permission class answers "may this user call this endpoint?", never "which
   rows may they see?". Row filtering is ``SocietyScopedQuerysetMixin``'s job
   (``apps/core/mixins.py``). Both are required — a permission alone would let a
   guard at society A read society B's bookings through a legitimate endpoint.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Role


class _RolePermission(BasePermission):
    """Base for single-role checks. Subclasses set ``required_role``.

    Default-deny in the strict sense: access is granted only on a positive match
    against one of the four known roles. ``required_role`` is never left at its
    empty default by a real subclass, and the explicit guard below makes sure a
    subclass that forgot to set it denies everyone rather than matching every
    user whose role is somehow blank.
    """

    required_role: str = ""
    message = "Your account role does not have access to this resource."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            # Both halves must name a real role. Comparing two empty strings
            # would otherwise succeed, which is the one way a "deny by default"
            # check can accidentally say yes.
            and self.required_role in Role.values
            and user.role == self.required_role
        )


class IsResident(_RolePermission):
    required_role = Role.RESIDENT
    message = "Only residents can access this resource."


class IsWorker(_RolePermission):
    required_role = Role.WORKER
    message = "Only domestic workers can access this resource."


class IsGuard(_RolePermission):
    required_role = Role.GUARD
    message = "Only security guards can access this resource."


class IsSocietyAdmin(_RolePermission):
    required_role = Role.SOCIETY_ADMIN
    message = "Only society administrators can access this resource."


class IsApproved(BasePermission):
    """Requires an administrator-approved account.

    Registration alone grants nothing (SRS 3.1, 3.2): an unapproved worker must
    not appear in search or be admitted at the gate, and an unapproved resident
    must not be able to hire. Authentication and authorisation are separate
    gates here, deliberately — an unapproved user can still sign in and see
    their pending status, which is why this is not folded into login.
    """

    message = "Your account is awaiting administrator approval."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_approved)


class IsPhoneVerified(BasePermission):
    message = "Please verify your phone number to continue."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_phone_verified)


class IsSameSociety(BasePermission):
    """Object-level check that an object belongs to the caller's society.

    Backstop for detail routes that fetch by primary key. Superusers are
    platform staff and operate across societies by definition.
    """

    message = "This resource belongs to a different society."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True

        society_id = getattr(obj, "society_id", None)
        if society_id is None:
            # Reached via a related object, e.g. obj.worker.society_id.
            society = getattr(obj, "society", None)
            society_id = getattr(society, "id", None)

        return society_id is not None and society_id == user.society_id


class IsOwnerOrSocietyAdmin(BasePermission):
    """Allows the object's owner, or an administrator of the same society.

    Used for profile-shaped resources: a worker edits their own profile, and
    their society's administrator may also act on it, but nobody else can.
    """

    message = "You can only modify your own records."
    owner_field = "user"

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True

        owner = getattr(obj, self.owner_field, None)
        if owner is not None and owner == user:
            return True
        if owner is None and obj == user:  # the object IS the user record
            return True

        return bool(user.is_society_admin and getattr(obj, "society_id", None) == user.society_id)


class ReadOnly(BasePermission):
    """Permits safe methods only. Compose with ``|`` to widen read access."""

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


# ---------------------------------------------------------------------------
# Composed permissions — prefer these in viewsets
#
# DRF supports & and | on permission classes, so the common combinations are
# named once here rather than being re-assembled (and occasionally
# mis-assembled) at each call site.
# ---------------------------------------------------------------------------

IsApprovedResident = IsResident & IsApproved
IsApprovedWorker = IsWorker & IsApproved
IsApprovedGuard = IsGuard & IsApproved
IsApprovedSocietyAdmin = IsSocietyAdmin & IsApproved

#: Any signed-in, approved user, whatever their role.
IsApprovedUser = IsApproved

#: Staff who operate the gate: guards, plus administrators overseeing them.
IsGateStaff = (IsGuard | IsSocietyAdmin) & IsApproved

#: Either party to an engagement — used by ratings and attendance history.
IsEngagementParty = (IsResident | IsWorker) & IsApproved
