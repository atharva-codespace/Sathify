"""
Shared Django admin helpers.

-------------------------------------------------------------------------------
WHY THIS EXISTS
-------------------------------------------------------------------------------
``SocietyScopedQuerysetMixin`` (apps/core/mixins.py) protects the *API*. It does
nothing for the Django admin, which has its own queryset path — so a staff user
browsing ``/admin/`` sees every society's rows regardless of their own.

That is not a live hole today: ``User.objects.create_user`` sets
``is_staff=False``, so only superusers — platform operators, who work across
societies by definition — can reach the admin at all. It becomes one the moment
a society administrator is given staff access, which is exactly what Module
11.1 asks for when it specifies the worker and resident directory be "built
directly on Django Admin customizations".

:class:`SocietyScopedAdminMixin` is what makes that grant safe. Apply it to any
ModelAdmin a non-superuser might reach.
"""

from django.contrib import admin  # noqa: F401 — re-exported for convenience


class SocietyScopedAdminMixin:
    """Restricts an admin screen to the staff member's own society.

    Set :attr:`society_lookup` when the society is reached indirectly, e.g.
    ``"user__society"`` for a model that hangs off the user rather than
    carrying its own FK.

    Superusers see everything. Staff with no society see nothing — failing
    closed, the same rule the API mixin follows, because the alternative in a
    multi-tenant system is a cross-society leak.
    """

    society_lookup = "society"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)

        if request.user.is_superuser:
            return queryset

        society_id = getattr(request.user, "society_id", None)
        if society_id is None:
            return queryset.none()

        return queryset.filter(**{self.society_lookup: society_id})

    def has_delete_permission(self, request, obj=None):
        """Nobody but a superuser deletes through these screens.

        SRS 5.5 requires a retained audit trail across bookings, attendance,
        payments, gate entries and complaints. A delete button on a society
        administrator's screen is the single easiest way to lose one, and the
        cases where a record genuinely must go are rare enough to be worth a
        platform operator's involvement.
        """
        return bool(request.user.is_superuser)


__all__ = ["SocietyScopedAdminMixin"]
