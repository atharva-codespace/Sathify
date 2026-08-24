"""
Module 11 — Admin, Reporting & Complaints.

The society administrator's operational layer: a complaint workflow with a
deadline attached to it, and a log of demand the platform could not serve.

-------------------------------------------------------------------------------
A COMPLAINT IS NEVER DELETED, ONLY CLOSED
-------------------------------------------------------------------------------
SRS 5.5 requires an audit trail of complaint actions retained for three years.
So there is no delete path: a complaint that turns out to be mistaken is
withdrawn or rejected, with a reason, and every transition is recorded as a
:class:`ComplaintUpdate`. That matters most in exactly the case where deletion
would be most tempting — a complaint against a worker that an administrator
would rather did not exist.

-------------------------------------------------------------------------------
THE DEADLINE IS SET ONCE, AT THE MOMENT OF RAISING
-------------------------------------------------------------------------------
``sla_due_at`` is computed by :mod:`apps.administration.sla` when the complaint
is created and never recomputed. Escalation raises the priority — which
reorders the queue, the whole point of escalating — but the deadline stays where
it was. A deadline that moves when something becomes more urgent is not a
deadline, and it would make the breach statistics in 11.4 meaningless.

-------------------------------------------------------------------------------
UNMET DEMAND IS A LOG, NOT A QUEUE
-------------------------------------------------------------------------------
:class:`UnmetDemand` records requests the platform could not fill. Nobody works
it; it exists so that 11.4 can answer "what were people asking for that we did
not have?" — which is the one question a society committee can actually act on
by recruiting.
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel
from apps.societies.models import Resident
from apps.workers.models import WorkerProfile

from . import sla


def complaint_photo_path(instance, filename):
    """Namespaced by society so storage stays browsable and scoped."""
    return f"complaints/society_{instance.society_id}/{uuid.uuid4().hex}/{filename}"


class ComplaintCategory(models.TextChoices):
    """The categories modspec 12.5 names, plus the two an MVP cannot omit.

    ``SAFETY`` and ``OTHER`` are additions. Safety needs to be separable because
    it is the one category that must never sit in a queue behind a billing
    query; "other" exists because a fixed list with no escape hatch produces
    mis-filed complaints rather than fewer of them.

    Module 12.5 will classify free text into these, so the list is the contract
    between the two modules and should not be reordered casually.
    """

    LATE_ARRIVAL = "late_arrival", _("Late or missed visit")
    BEHAVIOUR = "behaviour", _("Behaviour or conduct")
    PAYMENT = "payment", _("Payment")
    QUALITY = "quality", _("Quality of work")
    SAFETY = "safety", _("Safety or security")
    OTHER = "other", _("Something else")


#: Categories whose priority is raised on arrival. A safety complaint entering
#: the queue at normal priority, behind a billing query, is the failure mode
#: this exists to prevent.
ESCALATED_ON_ARRIVAL = frozenset({ComplaintCategory.SAFETY})


class ComplaintPriority(models.TextChoices):
    URGENT = "urgent", _("Urgent")
    HIGH = "high", _("High")
    NORMAL = "normal", _("Normal")


class ComplaintStatus(models.TextChoices):
    OPEN = "open", _("Open")
    IN_PROGRESS = "in_progress", _("Being looked into")
    RESOLVED = "resolved", _("Resolved")
    REJECTED = "rejected", _("Rejected")
    WITHDRAWN = "withdrawn", _("Withdrawn by the person who raised it")


#: Statuses that stop the SLA clock. Rejection and withdrawal count as closed:
#: the administrator has answered, even though the answer was no.
CLOSED_STATUSES = frozenset(
    {
        ComplaintStatus.RESOLVED,
        ComplaintStatus.REJECTED,
        ComplaintStatus.WITHDRAWN,
    }
)


class ComplaintQuerySet(models.QuerySet):
    def open(self):
        return self.exclude(status__in=CLOSED_STATUSES)

    def closed(self):
        return self.filter(status__in=CLOSED_STATUSES)

    def overdue(self, *, now=None):
        """Open complaints past their deadline.

        Escalated ones are deliberately still included — a complaint does not
        stop being overdue because somebody was told about it.
        """
        return self.open().filter(sla_due_at__lt=now or timezone.now())

    def awaiting_escalation(self, *, now=None):
        """Overdue and not yet escalated. What the sweep acts on."""
        return self.overdue(now=now).filter(escalated_at__isnull=True)

    def for_period(self, start, end):
        return self.filter(created_at__date__gte=start, created_at__date__lte=end)


class Complaint(SocietyScopedModel, TimeStampedModel):
    """Module 11.3 — an issue raised by a resident or a worker.

    Either side may raise one. That is not symmetry for its own sake: a worker
    with no way to report a household that withholds pay or behaves badly has
    only the option of leaving, which is precisely the imbalance this platform
    is supposed to reduce.
    """

    #: Human-readable handle, so a complaint can be referred to in a corridor
    #: conversation or a WhatsApp message without anyone reading out a UUID.
    reference = models.CharField(max_length=32, unique=True, db_index=True)

    raised_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="complaints_raised"
    )

    # Who it is about. Both optional: a complaint can be about the society
    # itself — a broken gate, a guard who will not scan — and forcing a target
    # would push those into "other" against a person who had nothing to do
    # with it.
    against_worker = models.ForeignKey(
        "workers.WorkerProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="complaints_against",
    )
    against_resident = models.ForeignKey(
        "societies.Resident",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="complaints_against",
    )

    category = models.CharField(
        max_length=20, choices=ComplaintCategory.choices, db_index=True
    )
    subject = models.CharField(max_length=150)
    description = models.TextField(max_length=2000)

    photo = models.ImageField(
        upload_to=complaint_photo_path,
        blank=True,
        help_text=_("Optional evidence — damage, a paper record, a gate log."),
    )

    priority = models.CharField(
        max_length=20,
        choices=ComplaintPriority.choices,
        default=ComplaintPriority.NORMAL,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ComplaintStatus.choices,
        default=ComplaintStatus.OPEN,
        db_index=True,
    )

    # --- SLA (11.3) ---------------------------------------------------------
    sla_due_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("Set once at creation from the priority. Never recomputed."),
    )
    escalated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the deadline passed and the sweep raised the alarm."),
    )
    first_response_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When an administrator first acted. The number that matters."),
    )

    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints_assigned",
    )

    resolution = models.TextField(blank=True, max_length=2000)
    resolved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    # --- Module 8.6 join ----------------------------------------------------
    payment_dispute = models.OneToOneField(
        "payments.PaymentDispute",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="complaint",
        help_text=_(
            "Set when this complaint was opened from a payment dispute. Module "
            "8.6 deliberately kept its own record thin and routed the handling "
            "here rather than building a second workflow."
        ),
    )

    objects = ComplaintQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["society", "status", "-created_at"]),
            models.Index(fields=["society", "priority", "sla_due_at"]),
            models.Index(fields=["against_worker", "-created_at"]),
            models.Index(fields=["raised_by", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.reference} — {self.subject}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._generate_reference()
        if self.sla_due_at is None:
            # Computed here rather than in the service layer so a complaint
            # created through the Django admin or a data migration still carries
            # a deadline. A complaint with no deadline is invisible to the sweep.
            self.sla_due_at = sla.due_at(
                self.created_at or timezone.now(), sla.hours_for(self.priority)
            )
        super().save(*args, **kwargs)

    def _generate_reference(self) -> str:
        """Date-prefixed, with a random tail rather than a counter.

        Same reasoning as ``Payment.receipt_number``: a running counter needs
        locking and leaks how many complaints the society has had.
        """
        stamp = timezone.localdate().strftime("%Y%m")
        return f"CMP-{stamp}-{uuid.uuid4().hex[:6].upper()}"

    # --- Derived state ------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.status not in CLOSED_STATUSES

    @property
    def is_overdue(self) -> bool:
        return self.is_open and sla.is_breached(self.sla_due_at)

    @property
    def hours_remaining(self) -> float:
        """Active hours to the deadline; negative once past it."""
        if not self.is_open:
            return 0.0
        return sla.hours_remaining(self.sla_due_at)

    @property
    def age_active_hours(self) -> float:
        """How long this has been open, counted in SLA hours."""
        end = self.resolved_at if not self.is_open else timezone.now()
        return sla.active_hours_between(self.created_at, end or timezone.now())

    @property
    def subject_label(self) -> str:
        """Who this is about, in one readable phrase."""
        if self.against_worker_id and self.against_worker.user:
            return self.against_worker.user.get_full_name()
        if self.against_resident_id and self.against_resident.user:
            return self.against_resident.user.get_full_name()
        return "The society"

    # --- Transitions --------------------------------------------------------
    #
    # Each returns whether it changed anything, so a caller can tell a real
    # transition from a repeated tap and avoid notifying twice. The service
    # layer owns notifications and history entries; these only move state.

    def start_progress(self, *, by=None) -> bool:
        if self.status != ComplaintStatus.OPEN:
            return False

        self.status = ComplaintStatus.IN_PROGRESS
        self.assigned_to = by or self.assigned_to
        self.first_response_at = self.first_response_at or timezone.now()
        self.save(
            update_fields=["status", "assigned_to", "first_response_at", "updated_at"]
        )
        return True

    def close(self, *, status: str, resolution: str, by=None) -> bool:
        """Resolve, reject or withdraw. Idempotent once closed."""
        if not self.is_open:
            return False
        if status not in CLOSED_STATUSES:
            raise ValueError(f"{status} is not a closing status.")

        now = timezone.now()
        self.status = status
        self.resolution = resolution
        self.resolved_by = by
        self.resolved_at = now
        self.first_response_at = self.first_response_at or now
        self.save(
            update_fields=[
                "status",
                "resolution",
                "resolved_by",
                "resolved_at",
                "first_response_at",
                "updated_at",
            ]
        )
        return True

    def escalate(self) -> bool:
        """Raise the alarm on an overdue complaint. Fires at most once.

        Bumps the priority one step so the queue reorders, but leaves
        ``sla_due_at`` alone — see the module docstring.
        """
        if not self.is_open or self.escalated_at is not None:
            return False

        self.escalated_at = timezone.now()
        self.priority = _next_priority_up(self.priority)
        self.save(update_fields=["escalated_at", "priority", "updated_at"])
        return True


def _next_priority_up(priority: str) -> str:
    if priority == ComplaintPriority.NORMAL:
        return ComplaintPriority.HIGH
    return ComplaintPriority.URGENT


class ComplaintUpdate(TimeStampedModel):
    """One entry in a complaint's history (SRS 5.5).

    Append-only by construction: there is no edit path and nothing here is ever
    rewritten. A resolution that was changed after the fact leaves both versions
    visible, which is the point of keeping a trail at all.
    """

    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="updates"
    )
    author = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaint_updates",
    )

    note = models.TextField(max_length=2000)

    #: Blank for a plain comment. Set when the entry records a transition, so
    #: the history reads as a narrative rather than as a list of status codes.
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)

    #: True for entries the system wrote — escalations, automatic routing. Kept
    #: apart so a reader can tell a person's judgement from a timer firing.
    is_system = models.BooleanField(default=False)

    #: Notes an administrator writes to themselves. Excluded from what the
    #: person who raised the complaint sees.
    is_internal = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["complaint", "created_at"])]

    def __str__(self):
        return f"{self.complaint_id}: {self.note[:40]}"

    @property
    def is_transition(self) -> bool:
        return bool(self.new_status)


class DemandKind(models.TextChoices):
    """Why demand went unmet. Each maps to a specific, recordable moment."""

    NO_MATCH = "no_match", _("No worker was free for the requested slot")
    HIRE_LAPSED = "hire_lapsed", _("A hire request expired unanswered")
    URGENT_REPLACEMENT = "urgent_replacement", _(
        "An urgent replacement could not be found"
    )


class UnmetDemand(SocietyScopedModel, TimeStampedModel):
    """Module 11.4 — a request the platform could not fill.

    Deliberately thin and denormalised. ``service_label`` is text rather than a
    foreign key because the two catalogues that produce these rows are
    different — Module 3's ``ServiceType`` for hiring, Module 5's
    ``ServiceCategory`` for bookings — and this log exists to be counted and
    read, not joined. A committee asking "what should we recruit for?" wants
    "cooking, 14 times last month", not a normalised schema.
    """

    kind = models.CharField(max_length=30, choices=DemandKind.choices, db_index=True)

    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="unmet_demand",
    )

    service_label = models.CharField(
        max_length=120,
        blank=True,
        help_text=_("What was asked for, as the person asking would name it."),
    )
    requested_date = models.DateField(null=True, blank=True, db_index=True)
    requested_time = models.TimeField(null=True, blank=True)

    detail = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["society", "kind", "-created_at"]),
            models.Index(fields=["society", "service_label"]),
        ]
        verbose_name = _("unmet demand")
        verbose_name_plural = _("unmet demand")

    def __str__(self):
        return f"{self.get_kind_display()}: {self.service_label or 'unspecified'}"


# ---------------------------------------------------------------------------
# 11.1 Directory proxies
# ---------------------------------------------------------------------------
#
# The modspec asks for the worker and resident directory to be "built directly
# on Django Admin customizations rather than a separate internal tool". Modules
# 3.5 and 2.3 already register those two models in the admin, but as *review*
# screens — one pending record at a time, with approve and reject buttons.
#
# A directory is a different job: search everyone, filter, read across. Rather
# than overload one ModelAdmin with two conflicting purposes, these proxies let
# the same table be registered twice with different columns, filters and
# permissions. They add no database table of their own.


class WorkerDirectory(WorkerProfile):
    class Meta:
        proxy = True
        verbose_name = _("worker (directory)")
        verbose_name_plural = _("worker directory")


class ResidentDirectory(Resident):
    class Meta:
        proxy = True
        verbose_name = _("resident (directory)")
        verbose_name_plural = _("resident directory")



# ===========================================================================
# Module 11.5 — Cross-society report jobs
#
# WHY THIS IS A JOB TABLE AND NOT A TASK QUEUE
#
# `docs/free-tier-constraints.md` §7 is explicit: there is no Celery, no Redis
# and no scheduler on this project's plan, and it warns that adding one anyway
# is *worse* than not having it — "tasks would be accepted into a queue nothing
# drains, the client would poll a 'processing' state forever, and no error would
# ever explain why."
#
# So this follows the pattern that document settles on instead: an idempotent,
# bounded sweep with three triggers — a read that naturally passes it, an
# endpoint the external uptime pinger can call, and a management command. The
# rows below are the sweep's work list, not a broker's inbox. Every state
# transition is safe to attempt twice, because "whoever happens to load the
# screen" is an acceptable trigger only if that is true.
#
# ONE SOCIETY MUST NOT VOID THE OTHER HUNDRED AND TWENTY-SEVEN
#
# A cross-society build touches every society in scope, and at that fan-out
# something will always fail — a society with a decade of gate events, a
# transient database timeout. A single status column would make the whole job
# fail for one bad tenant, which in practice means an operator who never gets a
# report at all. Hence ReportJobSociety: per-tenant state, so a job can finish
# *partially*, say which societies did not make it, and retry only those.
# ===========================================================================


class ReportKind(models.TextChoices):
    """The builders in ``reports.py`` this can drive.

    Deliberately the same set the single-society endpoint offers. A cross-
    society report that could show something the society's own report cannot
    would be a second source of truth, and the two would eventually disagree.
    """

    ATTENDANCE = "attendance", _("Attendance")
    PAYMENTS = "payments", _("Payments")
    COMPLAINTS = "complaints", _("Complaints")


class ReportScope(models.TextChoices):
    ALL = "all", _("Every society")
    TIER = "tier", _("Every society on a subscription tier")
    SELECTED = "selected", _("A chosen list of societies")


class ReportFormat(models.TextChoices):
    CSV = "csv", _("CSV")
    PDF = "pdf", _("PDF")


class ReportJobStatus(models.TextChoices):
    PENDING = "pending", _("Queued, waiting for a sweep")
    RUNNING = "running", _("Being built")
    READY = "ready", _("Finished")
    #: Finished, but at least one society could not be built. The file exists
    #: and contains everything that *did* build — see the class docstring.
    PARTIAL = "partial", _("Finished with some societies missing")
    FAILED = "failed", _("Could not be built")


class ReportJobQuerySet(models.QuerySet):
    def claimable(self):
        """Jobs a sweep may pick up.

        Includes RUNNING rows whose lease has lapsed. A process that dies
        mid-build would otherwise leave a job RUNNING forever with nobody
        allowed to touch it, which is precisely the "processing state nobody
        can explain" failure the free-tier doc warns about.
        """
        stale = timezone.now() - dt.timedelta(minutes=ReportJob.LEASE_MINUTES)
        return self.filter(
            models.Q(status=ReportJobStatus.PENDING)
            | models.Q(status=ReportJobStatus.RUNNING, started_at__lt=stale)
        )

    def live(self):
        return self.exclude(status=ReportJobStatus.FAILED)


class ReportJob(models.Model):
    """One cross-society report an operator asked for.

    Not ``SocietyScopedModel``: this is the one model in the codebase that is
    deliberately *about* several societies at once. Its tenancy rule lives in
    :meth:`societies_in_scope` instead, and is asserted by tests rather than by
    a foreign key.
    """

    #: How long a claimed job may stay RUNNING before another sweep may take it.
    #: Generous — a 128-society build is slow — but finite, which is the point.
    LEASE_MINUTES = 15

    #: Attempts per society before that society is given up on. Small: a build
    #: that failed twice for the same tenant is not going to succeed on the
    #: ninth try, and an operator waiting on a report deserves to be told.
    MAX_ATTEMPTS = 3

    #: Signed downloads are pointless if they live forever — a full export is
    #: the largest privacy surface the console has (§9.4d).
    RETENTION_DAYS = 7

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="report_jobs",
    )

    kind = models.CharField(max_length=20, choices=ReportKind.choices)
    scope = models.CharField(
        max_length=20, choices=ReportScope.choices, default=ReportScope.ALL
    )
    #: Meaningful only when scope is TIER.
    tier = models.CharField(max_length=20, blank=True, db_default="")
    #: Meaningful only when scope is SELECTED.
    societies = models.ManyToManyField(
        "societies.Society", blank=True, related_name="report_jobs"
    )

    period_start = models.DateField()
    period_end = models.DateField()
    formats = models.JSONField(default=list)

    #: Off by default and reason-gated at the API. A cross-society export with
    #: names and phone numbers in it is the single largest privacy surface in
    #: the product, so including them is an explicit act with a stated purpose.
    include_pii = models.BooleanField(default=False, db_default=False)
    reason = models.CharField(max_length=300, blank=True, db_default="")

    status = models.CharField(
        max_length=20,
        choices=ReportJobStatus.choices,
        default=ReportJobStatus.PENDING,
        db_default=ReportJobStatus.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    last_error = models.CharField(max_length=300, blank=True, db_default="")

    csv_file = models.FileField(upload_to="reports/%Y/%m/", blank=True)
    pdf_file = models.FileField(upload_to="reports/%Y/%m/", blank=True)
    row_count = models.PositiveIntegerField(default=0, db_default=0)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    objects = ReportJobQuerySet.as_manager()

    class Meta:
        verbose_name = _("report job")
        verbose_name_plural = _("report jobs")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.period_label} ({self.status})"

    @property
    def period_label(self) -> str:
        return f"{self.period_start:%d %b %Y} - {self.period_end:%d %b %Y}"

    @property
    def is_finished(self) -> bool:
        return self.status in {
            ReportJobStatus.READY,
            ReportJobStatus.PARTIAL,
            ReportJobStatus.FAILED,
        }

    @property
    def is_downloadable(self) -> bool:
        """Ready or partial, and not yet expired.

        Partial counts: a report missing three societies out of a hundred and
        twenty-eight is still the answer to most questions somebody asked it.
        """
        if self.status not in {ReportJobStatus.READY, ReportJobStatus.PARTIAL}:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    @property
    def progress(self) -> dict:
        rows = list(self.society_jobs.all())
        done = sum(1 for row in rows if row.status == ReportJobStatus.READY)
        failed = sum(1 for row in rows if row.status == ReportJobStatus.FAILED)
        return {
            "total": len(rows),
            "done": done,
            "failed": failed,
            "percent": round(100 * done / len(rows)) if rows else 0,
        }

    def societies_in_scope(self):
        """Exactly the societies this job may read, and no others.

        The whole tenancy rule for cross-society reporting is this one method.
        Everything downstream builds from what it returns, so a scoping mistake
        cannot leak through a builder — and a test that asserts on this asserts
        on the real boundary rather than on a filter copied into a view.
        """
        from apps.payments.models import SubscriptionTier
        from apps.societies.models import Society

        if self.scope == ReportScope.SELECTED:
            return self.societies.all()

        queryset = Society.objects.all()
        if self.scope == ReportScope.TIER:
            if self.tier == SubscriptionTier.FREE:
                # A society with no subscription row *is* free — the absence is
                # a valid state, so it must not be filtered out here.
                return queryset.filter(
                    models.Q(subscription__isnull=True)
                    | models.Q(subscription__tier=SubscriptionTier.FREE)
                )
            return queryset.filter(subscription__tier=self.tier)
        return queryset


class ReportJobSociety(models.Model):
    """One society's slice of a job. The unit of retry.

    Society-scoped by hand rather than through ``SocietyScopedModel``, because
    the parent is not scoped and inheriting the mixin here would imply a tenancy
    guarantee the pair does not have.
    """

    job = models.ForeignKey(
        ReportJob, on_delete=models.CASCADE, related_name="society_jobs"
    )
    society = models.ForeignKey(
        "societies.Society", on_delete=models.CASCADE, related_name="report_job_slices"
    )

    status = models.CharField(
        max_length=20,
        choices=ReportJobStatus.choices,
        default=ReportJobStatus.PENDING,
        db_default=ReportJobStatus.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    row_count = models.PositiveIntegerField(default=0, db_default=0)
    last_error = models.CharField(max_length=300, blank=True, db_default="")

    #: The built rows, cached so a retry of one society does not rebuild the
    #: other hundred and twenty-seven.
    payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("report job society")
        verbose_name_plural = _("report job societies")
        ordering = ["society__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "society"], name="one_slice_per_society_per_job"
            ),
        ]

    def __str__(self):
        return f"{self.society} in {self.job_id} ({self.status})"

    @property
    def has_auto_attempts_left(self) -> bool:
        """Whether the *sweep* may pick this up again on its own.

        Bounded on purpose: a build that failed three times for the same tenant
        is not going to succeed on the ninth, and an operator waiting on a
        report deserves to be told rather than watched to spin.
        """
        return self.attempts < ReportJob.MAX_ATTEMPTS

    @property
    def can_retry(self) -> bool:
        """Whether an *operator* may retry it. Any failed slice qualifies.

        Deliberately not gated on :attr:`has_auto_attempts_left`. The two are
        different judgements: the sweep gives up because it has no way to know
        whether anything changed, while a person pressing Retry has usually just
        fixed the thing that broke. Tying the button to the automatic budget
        would disable it at exactly the moment it becomes useful — which is the
        only moment anybody presses it.
        """
        return self.status == ReportJobStatus.FAILED


__all__ = [
    "CLOSED_STATUSES",
    "ESCALATED_ON_ARRIVAL",
    "Complaint",
    "ComplaintCategory",
    "ComplaintPriority",
    "ComplaintQuerySet",
    "ComplaintStatus",
    "ComplaintUpdate",
    "DemandKind",
    "ReportFormat",
    "ReportJob",
    "ReportJobSociety",
    "ReportJobStatus",
    "ReportKind",
    "ReportScope",
    "ResidentDirectory",
    "UnmetDemand",
    "WorkerDirectory",
    "complaint_photo_path",
]
