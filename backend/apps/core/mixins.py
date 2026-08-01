"""
Reusable viewset mixins.

``SocietyScopedQuerysetMixin`` is the single most security-relevant piece of
shared code in the project: it is what stops a guard or administrator at one
society from reading another society's records through an endpoint they are
otherwise legitimately authorised to call.
"""

import logging

logger = logging.getLogger(__name__)


class SocietyScopedQuerysetMixin:
    """Restricts a viewset's queryset to the caller's own society.

    Apply to every viewset whose model inherits ``SocietyScopedModel``. Set
    ``society_lookup`` when the FK is reached indirectly, e.g. ``"worker__society"``.

    Platform staff (``is_superuser``) bypass the filter — they operate across
    societies by definition.
    """

    society_lookup = "society"

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if not user or not user.is_authenticated:
            return queryset.none()

        if user.is_superuser:
            return queryset

        society_id = getattr(user, "society_id", None)
        if society_id is None:
            # Fail closed. A user with no society must not see society data:
            # returning everything here would be a cross-tenant data leak.
            logger.warning(
                "User %s has no society; returning empty queryset for %s",
                user.pk,
                self.__class__.__name__,
            )
            return queryset.none()

        return queryset.filter(**{self.society_lookup: society_id})
