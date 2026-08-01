"""
Module 4 — Discovery & Hiring (Recurring).

Entity shape::

    Resident ──< HireRequest >── WorkerProfile
                     │
                     └──1:1── Engagement   (created on acceptance)

``HireRequest`` and ``Engagement`` are deliberately separate models rather than
one row carrying a longer status enum, because they have different lifetimes and
different owners:

* A **hire request** belongs to a negotiation. It has a deadline, it goes stale,
  and once answered it is history — an audit record of who asked whom and how
  fast they replied. Module 4.3's response-rate signal is computed from exactly
  this table.
* An **engagement** is the standing relationship. It outlives the request and is
  the anchor that Modules 6, 7, 8 and 9 (scheduling, attendance, payments,
  ratings) all hang their records off. Collapsing the two would mean every one of
  those modules carried a foreign key to a row that might turn out to be a
  declined proposal.

-------------------------------------------------------------------------------
WEEKDAY CONVENTION — READ BEFORE CHANGING
-------------------------------------------------------------------------------
``days_of_week`` stores integers using Python's ``date.weekday()`` convention:
Monday is 0 and Sunday is 6. This is *not* the same as Django's ``__week_day``
lookup (Sunday is 1) or Dart's ``DateTime.weekday`` (Monday is 1). Anything that
compares a stored day against a real date must go through :func:`weekday_of`
rather than open-coding the arithmetic, and the Flutter client subtracts one from
``DateTime.weekday`` before sending.

-------------------------------------------------------------------------------
EXPIRY IS LAZY
-------------------------------------------------------------------------------
A pending request past ``expires_at`` is conceptually expired, but nothing runs
on a timer to say so — Sathify has no scheduled worker on the free tier (see
docs/free-tier-constraints.md). Rows are therefore swept on read, by
``HireRequest.objects.expire_lapsed()``, which every view that reads or acts on
requests calls first. Consequently **never filter on ``status=PENDING`` alone**;
use the ``pending()`` / ``lapsed()`` queryset methods, which are honest about the
deadline regardless of whether a sweep has happened yet.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel

#: Monday-first labels, indexed by the values stored in ``days_of_week``.
DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

#: How long a worker has to answer a hire request before it lapses. Overridable
#: via settings so a society operating at a different tempo can be tuned without
#: a migration.
DEFAULT_RESPONSE_WINDOW_HOURS = 48


def weekday_of(value: dt.date) -> int:
    """The stored-convention weekday for a date (Monday=0 … Sunday=6).

    The single place the convention is applied, so that changing it is a
    one-line edit rather than a search across four modules.
    """
    return value.weekday()


def validate_days_of_week(value) -> None:
    """Validate the ``days_of_week`` payload: a non-empty set of 0–6.

    Enforced at the model layer rather than only in the serializer because
    scheduling (Module 6) reads these rows directly, and a stray ``7`` would
    silently never match a real weekday instead of failing loudly.
    """
    if not isinstance(value, list):
        raise ValidationError(_("Days of week must be a list of integers."))
    if not value:
        raise ValidationError(_("Select at least one day of the week."))
    if len(set(value)) != len(value):
        raise ValidationError(_("Days of week must not contain duplicates."))
    for day in value:
        # bool is a subclass of int; True would otherwise sail through as day 1.
        if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
            raise ValidationError(
                _("Each day must be an integer from 0 (Monday) to 6 (Sunday).")
            )


def default_response_deadline():
    """Deadline for a newly created request. A callable, so migrations stay stable."""
    hours = getattr(settings, "HIRE_REQUEST_RESPONSE_HOURS", DEFAULT_RESPONSE_WINDOW_HOURS)
    return timezone.now() + dt.timedelta(hours=hours)


class RecurringTerms(models.Model):
    """The terms of a recurring arrangement: which days, what time, what pay.

    Shared by ``HireRequest`` (as proposed) and ``Engagement`` (as agreed). Held
    in one abstract base so the two cannot drift apart — an engagement is
    supposed to be a faithful record of the request that produced it, and that
    guarantee is worth more than the small indirection.
    """

    days_of_week = models.JSONField(
        default=list,
        validators=[validate_days_of_week],
        help_text=_("Monday=0 … Sunday=6, e.g. [0,1,2,3,4] for weekdays."),
    )
    start_time = models.TimeField(help_text=_("Expected arrival time."))
    expected_duration_minutes = models.PositiveSmallIntegerField(
        default=60,
        help_text=_("Used by Module 7 to judge whether a visit was short-served."),
    )
    monthly_rate = models.PositiveIntegerField(help_text=_("Agreed monthly pay in INR."))

    class Meta:
        abstract = True

    @property
    def day_labels(self) -> list[str]:
        """Human-readable days, for the app and Django Admin."""
        return [DAY_LABELS[d] for d in sorted(self.days_of_week) if 0 <= d <= 6]

    @property
    def end_time(self) -> dt.time:
        """Expected departure, derived from start plus duration.

        Wraps past midnight rather than clamping, so a late-evening slot is
        represented honestly instead of collapsing onto 23:59.
        """
        base = dt.datetime.combine(dt.date.min, self.start_time)
        return (base + dt.timedelta(minutes=self.expected_duration_minutes)).time()

    def occurs_on(self, day: dt.date) -> bool:
        """Whether these terms call for a visit on ``day``."""
        return weekday_of(day) in set(self.days_of_week)


def _log_unmet_demand(lapsed_requests) -> None:
    """Record expired hire requests as unmet demand (Module 11.4).

    Lazily imported and non-raising. This runs inside a sweep that every list
    read triggers, and an analytics failure must never turn reading your own
    hire requests into an error.
    """
    try:
        from apps.administration.models import DemandKind
        from apps.administration.services import record_unmet_demand
    except ImportError:  # pragma: no cover — Module 11 always ships with this
        return

    for request in lapsed_requests:
        record_unmet_demand(
            society=request.society,
            kind=DemandKind.HIRE_LAPSED,
            service_label=request.service_type.name,
            requested_by=request.resident.user,
            # A recurring proposal has no single date. The deadline it ran past
            # is the closest thing to "when this was needed by", and it is what
            # makes the log sortable alongside the one-day bookings.
            requested_date=timezone.localdate(request.expires_at),
            requested_time=request.start_time,
            detail="The worker did not respond before the deadline.",
        )


class HireRequestStatus(models.TextChoices):
    PENDING = "pending", _("Awaiting the worker's response")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")
    WITHDRAWN = "withdrawn", _("Withdrawn by the resident")
    EXPIRED = "expired", _("Lapsed without a response")


class HireRequestQuerySet(models.QuerySet):
    """Deadline-aware queries. Prefer these over raw ``status=`` filters."""

    def pending(self):
        """Still open: unanswered *and* inside the response window."""
        return self.filter(
            status=HireRequestStatus.PENDING, expires_at__gt=timezone.now()
        )

    def lapsed(self):
        """Unanswered and past the deadline — expired in fact, if not yet in the row."""
        return self.filter(
            status=HireRequestStatus.PENDING, expires_at__lte=timezone.now()
        )

    def answered(self):
        """Requests the worker actually responded to, either way.

        Withdrawn requests are excluded on purpose: the resident took them off
        the table, so a non-response is not the worker's fault and must not
        count against their response rate (Module 4.3).
        """
        return self.filter(
            status__in=[HireRequestStatus.ACCEPTED, HireRequestStatus.DECLINED]
        )

    def expire_lapsed(self) -> int:
        """Flip lapsed rows to EXPIRED. Returns how many were swept.

        Idempotent and safe to call on every read — see the module docstring on
        lazy expiry.

        The rows are read before they are updated so each one can be logged as
        unmet demand (Module 11.4). A request nobody answered is demand the
        society failed to serve, and it is the only record of it: once the
        status flips there is nothing left to distinguish it from a request that
        was simply declined.
        """
        lapsed = list(
            self.lapsed().select_related("society", "resident__user", "service_type")
        )
        if not lapsed:
            return 0

        swept = self.filter(pk__in=[request.pk for request in lapsed]).update(
            status=HireRequestStatus.EXPIRED, updated_at=timezone.now()
        )
        _log_unmet_demand(lapsed)
        return swept


class HireRequest(SocietyScopedModel, RecurringTerms, TimeStampedModel):
    """Module 4.4 — a resident's proposal to a worker, with a deadline.

    Society-scoped even though both parties already carry a society, because
    that FK is what ``SocietyScopedQuerysetMixin`` filters on; reaching the
    society through ``resident.flat.tower.society`` on every list query would be
    three joins for a check that must run on literally every request.
    """

    resident = models.ForeignKey(
        "societies.Resident", on_delete=models.CASCADE, related_name="hire_requests"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="hire_requests"
    )
    service_type = models.ForeignKey(
        "workers.ServiceType", on_delete=models.PROTECT, related_name="hire_requests"
    )

    message = models.TextField(
        blank=True, max_length=500, help_text=_("Optional note from the resident.")
    )

    status = models.CharField(
        max_length=20,
        choices=HireRequestStatus.choices,
        default=HireRequestStatus.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField(default=default_response_deadline, db_index=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_note = models.TextField(
        blank=True,
        max_length=500,
        help_text=_("The worker's reason, chiefly when declining."),
    )

    objects = HireRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One live proposal per resident/worker/service at a time. Without
            # this, tapping "Hire" twice on a slow connection sends two requests
            # and the worker sees a duplicate they must answer separately.
            models.UniqueConstraint(
                fields=["resident", "worker", "service_type"],
                condition=models.Q(status=HireRequestStatus.PENDING),
                name="one_pending_hire_request_per_pair",
            ),
        ]
        indexes = [
            # The worker's inbox: "my open requests, newest first".
            models.Index(fields=["worker", "status", "-created_at"]),
            # The sweep in expire_lapsed().
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.resident} → {self.worker} ({self.status})"

    @property
    def is_actionable(self) -> bool:
        """Whether the worker may still accept or decline this."""
        return self.status == HireRequestStatus.PENDING and self.expires_at > timezone.now()

    @property
    def has_lapsed(self) -> bool:
        """Unanswered past the deadline, whether or not the row has been swept."""
        return self.status == HireRequestStatus.PENDING and self.expires_at <= timezone.now()

    @property
    def effective_status(self) -> str:
        """Status as the user should see it, accounting for un-swept expiry."""
        return HireRequestStatus.EXPIRED if self.has_lapsed else self.status

    @property
    def response_hours(self) -> float | None:
        """How long the worker took to answer, in hours. ``None`` if unanswered."""
        if self.responded_at is None:
            return None
        return (self.responded_at - self.created_at).total_seconds() / 3600


class EngagementStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    PAUSED = "paused", _("Paused")
    TERMINATED = "terminated", _("Terminated")


class EngagementEndReason(models.TextChoices):
    RESIDENT_ENDED = "resident_ended", _("Ended by the resident")
    WORKER_ENDED = "worker_ended", _("Ended by the worker")
    RESIDENT_MOVED_OUT = "resident_moved_out", _("Resident moved out")
    WORKER_LEFT_SOCIETY = "worker_left_society", _("Worker stopped working here")
    ADMIN_ENDED = "admin_ended", _("Ended by an administrator")


class EngagementQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=EngagementStatus.ACTIVE)

    def live(self):
        """Active or paused — i.e. not finished.

        Paused engagements still count as a live relationship: the worker is
        expected back, and Module 8 must not close out their payment history.
        """
        return self.filter(status__in=[EngagementStatus.ACTIVE, EngagementStatus.PAUSED])


class Engagement(SocietyScopedModel, RecurringTerms, TimeStampedModel):
    """Module 4.4 / 4.5 — a standing relationship between a resident and a worker.

    Created only by accepting a hire request, which is what makes the agreed
    terms provably two-sided. ``hire_request`` is nullable rather than required
    so that Module 11 can later record an arrangement made offline and entered by
    an administrator, without that path having to fabricate a request the worker
    never actually saw.
    """

    resident = models.ForeignKey(
        "societies.Resident", on_delete=models.PROTECT, related_name="engagements"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.PROTECT, related_name="engagements"
    )
    service_type = models.ForeignKey(
        "workers.ServiceType", on_delete=models.PROTECT, related_name="engagements"
    )
    hire_request = models.OneToOneField(
        HireRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="engagement",
    )

    status = models.CharField(
        max_length=20,
        choices=EngagementStatus.choices,
        default=EngagementStatus.ACTIVE,
        db_index=True,
    )
    started_on = models.DateField(default=dt.date.today)

    # --- 4.5 lifecycle trail ------------------------------------------------
    paused_at = models.DateTimeField(null=True, blank=True)
    pause_reason = models.CharField(max_length=200, blank=True)
    resumed_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(
        max_length=30, choices=EngagementEndReason.choices, blank=True
    )
    end_note = models.TextField(blank=True, max_length=500)
    ended_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ended_engagements",
    )

    objects = EngagementQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A household cannot hold two live arrangements with the same worker
            # for the same service. Paused counts as live — resuming must not be
            # able to collide with an engagement created while it was paused.
            models.UniqueConstraint(
                fields=["resident", "worker", "service_type"],
                condition=models.Q(
                    status__in=[EngagementStatus.ACTIVE, EngagementStatus.PAUSED]
                ),
                name="one_live_engagement_per_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["worker", "status"]),
            models.Index(fields=["resident", "status"]),
        ]

    def __str__(self):
        return f"{self.resident} ⇄ {self.worker} ({self.status})"

    @property
    def is_live(self) -> bool:
        return self.status in {EngagementStatus.ACTIVE, EngagementStatus.PAUSED}

    def occurs_on(self, day: dt.date) -> bool:
        """Whether a visit is expected on ``day``. Paused engagements never are."""
        return self.status == EngagementStatus.ACTIVE and super().occurs_on(day)

    # --- 4.5 transitions ----------------------------------------------------
    # Each is idempotent and returns whether it actually changed anything: the
    # mobile client retries on a flaky connection, and a duplicate "pause" must
    # not overwrite the original timestamp.

    def pause(self, reason: str = "") -> bool:
        """Suspend visits without ending the relationship."""
        if self.status != EngagementStatus.ACTIVE:
            return False
        self.status = EngagementStatus.PAUSED
        self.paused_at = timezone.now()
        self.pause_reason = reason
        self.save(update_fields=["status", "paused_at", "pause_reason", "updated_at"])
        return True

    def resume(self) -> bool:
        if self.status != EngagementStatus.PAUSED:
            return False
        self.status = EngagementStatus.ACTIVE
        self.resumed_at = timezone.now()
        self.pause_reason = ""
        self.save(update_fields=["status", "resumed_at", "pause_reason", "updated_at"])
        return True

    def terminate(self, *, reason: str, note: str = "", by=None) -> bool:
        """End the engagement for good. Terminal — a later resume is refused."""
        if self.status == EngagementStatus.TERMINATED:
            return False
        self.status = EngagementStatus.TERMINATED
        self.ended_at = timezone.now()
        self.end_reason = reason
        self.end_note = note
        self.ended_by = by
        self.save(
            update_fields=[
                "status",
                "ended_at",
                "end_reason",
                "end_note",
                "ended_by",
                "updated_at",
            ]
        )
        return True
