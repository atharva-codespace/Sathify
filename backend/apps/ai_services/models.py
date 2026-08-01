"""
Module 12 — AI Layer: bookkeeping.

Two models, and neither stores anything a user typed. What is recorded is *how
the layer behaved*: which tier served a call, how long it took, and whether the
answer came from a model or from the fallback path.

-------------------------------------------------------------------------------
WHY THE PROMPT AND THE ANSWER ARE NOT STORED
-------------------------------------------------------------------------------
A chatbot question is "how much did I pay Sunita last month", and a review being
summarised is somebody's opinion of a named person. Keeping either in a log
table would create a second, unaudited copy of data the rest of the platform is
careful about — the DPDP consent captured in Module 3.6 covers KYC, not a
transcript of everything anybody ever asked.

The prompt's *length* is kept, because it is what actually explains a slow call,
and the feature name is kept, because that is what explains a bill.

-------------------------------------------------------------------------------
WHY THE RATE COUNTER IS A DATABASE TABLE
-------------------------------------------------------------------------------
Tier 3 (OpenRouter) allows 20 requests a minute and 50 a day — an order of
magnitude tighter than any other tier, and the ceiling this project is designed
against (docs/free-tier-constraints.md §5). Enforcing that locally means the
tier fails over cleanly to Tier 4 instead of burning a request to be told no.

Redis would be the obvious home for a counter. There is no Redis on the free
tier, and there is no second process to share memory with, so the counter lives
in Postgres and is incremented under a row lock. At this volume — tens of calls
a day against a cap of fifty — a row lock is free.
"""

from __future__ import annotations

import datetime as dt

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class AiFeature(models.TextChoices):
    """Which sub-module made the call. Named so a log line explains a bill."""

    CHAT = "chat", _("12.2 Chatbot")
    REVIEW_SUMMARY = "review_summary", _("12.5 Review summary")
    SENTIMENT = "sentiment", _("12.5 Review sentiment")
    COMPLAINT_CLASSIFY = "complaint_classify", _("12.5 Complaint classification")
    RECOMMENDATION = "recommendation", _("12.1 Worker recommendation")
    OCR = "ocr", _("12.3 Document extraction")
    FACE = "face", _("12.4 Face verification")


class AiOutcomeKind(models.TextChoices):
    """Where the answer actually came from.

    The distinction Module 12.6 exists to make legible: an answer produced by a
    model and an answer produced by the rule-based fallback are both valid
    outcomes, and only one of them costs anything.
    """

    AI = "ai", _("A provider answered")
    FALLBACK = "fallback", _("The rule-based fallback answered")
    UNAVAILABLE = "unavailable", _("Nothing could answer")


class AiRequestLog(TimeStampedModel):
    """One trip through the AI layer.

    Kept because "how often does the chain actually fall past Tier 1?" is a
    question this project needs an answer to — the whole four-tier design is a
    bet on free ceilings, and this is the only evidence of whether the bet holds.
    """

    feature = models.CharField(
        max_length=30, choices=AiFeature.choices, db_index=True
    )
    outcome = models.CharField(
        max_length=20,
        choices=AiOutcomeKind.choices,
        default=AiOutcomeKind.AI,
        db_index=True,
    )

    #: Which provider answered. Blank when the fallback served it.
    tier = models.CharField(max_length=30, blank=True, db_index=True)

    #: Tiers that were tried and refused, in order, so a chain that is quietly
    #: always falling through to Tier 4 is visible without provider dashboards.
    tiers_attempted = models.JSONField(default=list, blank=True)

    latency_ms = models.PositiveIntegerField(default=0)

    #: Length, not content. See the module docstring.
    prompt_chars = models.PositiveIntegerField(default=0)
    response_chars = models.PositiveIntegerField(default=0)

    error = models.CharField(max_length=300, blank=True)

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_requests",
    )
    society = models.ForeignKey(
        "societies.Society",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_requests",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["feature", "-created_at"]),
            models.Index(fields=["outcome", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.feature} via {self.tier or self.outcome} ({self.latency_ms} ms)"

    @property
    def fell_through(self) -> bool:
        """Whether the primary tier failed and something else picked it up."""
        return len(self.tiers_attempted) > 1


class UsageWindow(models.TextChoices):
    MINUTE = "minute", _("Per minute")
    DAY = "day", _("Per day")


class AiUsageCounter(models.Model):
    """A rate-limit bucket for one tier over one window.

    Not a :class:`TimeStampedModel` — the bucket key already carries the time,
    and two more datetime columns on a row that is written on every AI call is
    waste.
    """

    tier = models.CharField(max_length=30, db_index=True)
    window = models.CharField(max_length=10, choices=UsageWindow.choices)

    #: The bucket this counts, as text: ``2026-07-31`` or ``2026-07-31T14:22``.
    #: Text rather than a datetime so the granularity is part of the key and two
    #: windows can never collide.
    bucket = models.CharField(max_length=20)

    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tier", "window", "bucket"], name="one_counter_per_bucket"
            )
        ]
        indexes = [models.Index(fields=["tier", "window", "bucket"])]

    def __str__(self):
        return f"{self.tier} {self.window} {self.bucket}: {self.count}"

    # --- Bucket keys --------------------------------------------------------

    @staticmethod
    def bucket_for(window: str, *, now: dt.datetime | None = None) -> str:
        moment = timezone.localtime(now or timezone.now())
        if window == UsageWindow.MINUTE:
            return moment.strftime("%Y-%m-%dT%H:%M")
        return moment.strftime("%Y-%m-%d")

    # --- Enforcement --------------------------------------------------------

    @classmethod
    def reserve(cls, tier: str, *, window: str, cap: int) -> bool:
        """Take one unit of quota. Returns False when the cap is already spent.

        Locks the row so two concurrent requests cannot both read 49 and both
        proceed. On a free-tier instance that races rarely, but the failure it
        prevents — a 429 from the provider — is exactly what enforcing locally
        was supposed to avoid.
        """
        if cap <= 0:
            return True  # No cap configured for this tier.

        bucket = cls.bucket_for(window)

        with transaction.atomic():
            row, _created = cls.objects.select_for_update().get_or_create(
                tier=tier, window=window, bucket=bucket, defaults={"count": 0}
            )
            if row.count >= cap:
                return False

            row.count += 1
            row.save(update_fields=["count"])
            return True

    @classmethod
    def release(cls, tier: str, *, window: str) -> None:
        """Give a reserved unit back.

        Called when a tier was reserved but never actually reached — an
        unreachable endpoint, say. Without this, a provider outage would eat the
        day's quota for a provider that answered nothing.
        """
        bucket = cls.bucket_for(window)
        with transaction.atomic():
            row = (
                cls.objects.select_for_update()
                .filter(tier=tier, window=window, bucket=bucket, count__gt=0)
                .first()
            )
            if row is not None:
                row.count -= 1
                row.save(update_fields=["count"])

    @classmethod
    def used(cls, tier: str, *, window: str) -> int:
        row = cls.objects.filter(
            tier=tier, window=window, bucket=cls.bucket_for(window)
        ).first()
        return row.count if row else 0

    @classmethod
    def prune(cls, *, keep_days: int = 7) -> int:
        """Drop buckets nobody will read again. Returns how many went.

        Called opportunistically rather than on a schedule — the free tier has
        no scheduler — and cheap enough that doing it on the occasional request
        costs nothing measurable.
        """
        cutoff = (timezone.localdate() - dt.timedelta(days=keep_days)).isoformat()
        deleted, _ = cls.objects.filter(bucket__lt=cutoff).delete()
        return deleted


__all__ = [
    "AiFeature",
    "AiOutcomeKind",
    "AiRequestLog",
    "AiUsageCounter",
    "UsageWindow",
]
