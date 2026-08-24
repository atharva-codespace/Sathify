"""
The one documented way to read across societies.

-------------------------------------------------------------------------------
WHY THIS FILE EXISTS AT ALL
-------------------------------------------------------------------------------
``SocietyScopedModel`` and ``SocietyScopedQuerysetMixin`` are built on a single
promise: no request can see another society's rows. The Superadmin console
breaks that promise on purpose — a platform operator reconciling payments has to
see all of them at once.

The dangerous way to build that is the obvious one: let ``is_superuser`` skip the
filter wherever the filter happens to live. ``SocietyScopedQuerysetMixin`` already
does this, and for the society-facing API it is fine, because the only superusers
were platform staff who rarely called it. A whole console built on that habit is
a different proposition: the bypass would end up spread across every console
viewset, with no way to answer "which of our operators read this resident's
record last month, and why?" short of reading the code and guessing.

So the bypass gets exactly one entry point, here, and it writes down what it did.

-------------------------------------------------------------------------------
LOGGING IS NOT BEST-EFFORT
-------------------------------------------------------------------------------
:class:`PlatformScopedQuerysetMixin` records the read *before* returning rows,
and a failure to record is a failure to read. That ordering is deliberate. A log
written afterwards is a log that is missing precisely the reads that crashed
halfway, which are the ones an investigation cares about most.

The counterpart is ``PlatformAccessLog`` being readable by the society it names
(PRD §9.4d). A capability nobody can audit is one a managing committee has to
take on trust; this codebase does not ask them to.
"""

from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

#: Models whose rows are about an identifiable person rather than about money or
#: configuration. Reads of these are logged individually; everything else is
#: aggregate enough not to be worth the row.
PII_MODEL_LABELS = frozenset(
    {
        "accounts.User",
        "societies.Resident",
        "workers.WorkerProfile",
        "attendance.AttendanceEvent",
        "attendance.WorkSession",
    }
)


def client_ip(request) -> str | None:
    """The caller's address, preferring the proxy header the deploy sets.

    Returns None rather than a guess when neither is present — an audit row
    saying "we don't know" is more useful than one asserting 127.0.0.1.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # Left-most entry is the original client; the rest are proxies.
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def record_platform_access(
    *,
    user,
    model_label: str,
    society=None,
    action: str = "read",
    reason: str = "",
    row_count: int = 0,
    ip_address: str | None = None,
):
    """Write one access row. Returns it, or None when the model is not PII.

    Called by the mixin below rather than by viewsets directly, so that adding a
    console endpoint cannot accidentally opt out of the audit trail by forgetting
    a line.
    """
    if model_label not in PII_MODEL_LABELS:
        return None

    from apps.accounts.models import PlatformAccessLog

    return PlatformAccessLog.objects.create(
        superadmin=user if getattr(user, "pk", None) else None,
        society=society,
        model_label=model_label,
        action=action,
        reason=reason[:300],
        row_count=row_count,
        ip_address=ip_address,
    )


class PlatformScopedQuerysetMixin:
    """Cross-society reads for console viewsets, with an audit row.

    Deliberately *not* a subclass of ``SocietyScopedQuerysetMixin``: the two have
    opposite jobs, and inheriting one from the other would make it easy to reach
    for the wrong one by autocomplete. A console viewset uses this; every other
    viewset in the codebase uses the scoped one.

    ``?society=<id>`` narrows the read to a single society, which is both the
    common case for support work and the version that produces a usefully
    specific audit row.
    """

    #: Set on the viewset when the model is reached through a relation, e.g.
    #: ``"worker__society"``. Matches the scoped mixin's spelling on purpose.
    society_lookup = "society"

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if not (user and user.is_authenticated and getattr(user, "is_superadmin", False)):
            # Fail closed, and loudly. Reaching here means a console viewset was
            # exposed without IsPlatformOperator in front of it — a routing bug,
            # not a user error, and one that would otherwise leak every society.
            logger.error(
                "%s used PlatformScopedQuerysetMixin without a superadmin caller "
                "(user=%s). Returning nothing.",
                self.__class__.__name__,
                getattr(user, "pk", None),
            )
            return queryset.none()

        society = self.request.query_params.get("society") if hasattr(self.request, "query_params") else None
        if society:
            queryset = queryset.filter(**{self.society_lookup: society})

        self._log_platform_read(queryset, society_id=society)
        return queryset

    # -- internals -----------------------------------------------------------

    def _log_platform_read(self, queryset, *, society_id=None) -> None:
        """Record the read. Runs inside the request's transaction on purpose.

        If writing the audit row fails, the read fails with it. See the module
        docstring: a log that silently drops its hardest cases is worse than no
        log, because it invites confidence it has not earned.
        """
        model = queryset.model
        label = f"{model._meta.app_label}.{model.__name__}"
        if label not in PII_MODEL_LABELS:
            return

        society = None
        if society_id:
            from apps.societies.models import Society

            society = Society.objects.filter(pk=society_id).first()

        with transaction.atomic():
            record_platform_access(
                user=self.request.user,
                model_label=label,
                society=society,
                action="read",
                reason=self.request.headers.get("X-Access-Reason", ""),
                row_count=queryset.count(),
                ip_address=client_ip(self.request),
            )


__all__ = [
    "PII_MODEL_LABELS",
    "PlatformScopedQuerysetMixin",
    "client_ip",
    "record_platform_access",
]
