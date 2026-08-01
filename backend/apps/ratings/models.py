"""
Module 9 — Ratings, Reviews & Trust Score.

The two-way accountability layer. Both sides rate each other after a completed
engagement or booking, and a computed trust score aggregates that history into
one number for each of them.

-------------------------------------------------------------------------------
THE SCORE MUST BE EXPLAINABLE. THAT IS A REQUIREMENT, NOT A NICETY.
-------------------------------------------------------------------------------
The modspec is explicit: "explainable being the key requirement, since a
black-box score nobody can justify will get disputed." A trust score decides
whether a worker gets hired, so a worker who loses work to it is entitled to
know why, and an administrator asked to defend it needs to be able to.

Two things enforce that here:

* :mod:`apps.ratings.trust` computes the score from named, separately testable
  components and returns the breakdown alongside the number.
* :class:`TrustScoreLog` records **every** change with that breakdown frozen
  into it. Recomputing an old score later would give today's answer, not the one
  that was acted on, so the explanation is stored rather than re-derived.

-------------------------------------------------------------------------------
SENTIMENT IS STORED SEPARATELY FROM THE REVIEW
-------------------------------------------------------------------------------
Modspec 9.2 requires it, and the reason is sound: the raw text is what a person
wrote, while the sentiment is a model's opinion about it. Keeping them in one
row would make it easy to overwrite the first with the second, and would leave
no way to re-analyse a corpus when the model improves.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel

#: Stars run 1–5. Module 4's ranking normalises against this, so the two must
#: agree — see ``apps.hiring.scoring.RATING_MAX``.
MIN_STARS = 1
MAX_STARS = 5

#: Trust scores run 0–100, matching ``apps.hiring.scoring.TRUST_SCORE_MAX``.
#: Changing one without the other silently rescales every match percentage.
TRUST_SCORE_MAX = 100


class RatingDirection(models.TextChoices):
    RESIDENT_TO_WORKER = "resident_to_worker", _("Resident rating a worker")
    WORKER_TO_RESIDENT = "worker_to_resident", _("Worker rating a resident")


class RatingQuerySet(models.QuerySet):
    def visible(self):
        """Ratings that count.

        A rating flagged as suspicious still exists and is still shown to the
        person who wrote it — it is withheld from *scoring* until an
        administrator has looked at it. Deleting it outright would make a false
        positive unrecoverable and unappealable.
        """
        return self.filter(is_withheld=False)

    def of_worker(self, worker):
        return self.filter(
            worker=worker, direction=RatingDirection.RESIDENT_TO_WORKER
        )

    def of_resident(self, resident):
        return self.filter(
            resident=resident, direction=RatingDirection.WORKER_TO_RESIDENT
        )


class Rating(SocietyScopedModel, TimeStampedModel):
    """Module 9.1 — one side's verdict on one completed piece of work.

    Both parties are always recorded, whichever way the rating runs, because
    both are needed to scope and display it. ``direction`` says who is being
    judged; :attr:`subject_is_worker` is the readable form of that.
    """

    direction = models.CharField(
        max_length=30, choices=RatingDirection.choices, db_index=True
    )

    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="ratings"
    )
    resident = models.ForeignKey(
        "societies.Resident", on_delete=models.CASCADE, related_name="ratings"
    )
    rater = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="ratings_given"
    )

    # What is being rated. Exactly one is set — the constraint below enforces it.
    engagement = models.ForeignKey(
        "hiring.Engagement",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ratings",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ratings",
    )

    stars = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(MIN_STARS), MaxValueValidator(MAX_STARS)]
    )
    review = models.TextField(
        blank=True,
        max_length=1000,
        help_text=_("The raw text as written. Never overwritten by analysis."),
    )

    # --- Module 9.4 ---------------------------------------------------------
    is_flagged = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Matched a suspicious pattern. Escalated, never auto-deleted."),
    )
    is_withheld = models.BooleanField(
        default=False,
        help_text=_("Excluded from scoring pending an administrator's review."),
    )

    objects = RatingQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Modspec 9.1 — one rating per completed engagement per direction,
            # to prevent review spam. Enforced in the database because the
            # serializer alone would lose a race between two taps.
            models.UniqueConstraint(
                fields=["engagement", "direction"],
                condition=models.Q(engagement__isnull=False),
                name="one_rating_per_engagement_per_direction",
            ),
            models.UniqueConstraint(
                fields=["booking", "direction"],
                condition=models.Q(booking__isnull=False),
                name="one_rating_per_booking_per_direction",
            ),
            # A rating must attach to exactly one piece of work. Neither would
            # make it unscopeable; both would make it double-counted.
            models.CheckConstraint(
                condition=(
                    models.Q(engagement__isnull=False, booking__isnull=True)
                    | models.Q(engagement__isnull=True, booking__isnull=False)
                ),
                name="rating_targets_exactly_one_job",
            ),
        ]
        indexes = [
            models.Index(fields=["worker", "direction", "-created_at"]),
            models.Index(fields=["resident", "direction", "-created_at"]),
            models.Index(fields=["rater", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.stars}★ {self.get_direction_display()}"

    @property
    def subject_is_worker(self) -> bool:
        return self.direction == RatingDirection.RESIDENT_TO_WORKER

    @property
    def counts_toward_score(self) -> bool:
        return not self.is_withheld


class SentimentLabel(models.TextChoices):
    POSITIVE = "positive", _("Positive")
    NEUTRAL = "neutral", _("Neutral")
    NEGATIVE = "negative", _("Negative")
    UNKNOWN = "unknown", _("Could not be determined")


class ReviewSentiment(TimeStampedModel):
    """Module 9.2 — what a model made of a review's free text.

    A separate row from the rating, deliberately (see the module docstring): the
    review is what a person wrote, this is an opinion about it. Re-analysing a
    corpus with a better model replaces these rows and touches no review text.

    ``engine`` records which model produced it. When Module 12's Gemini path
    replaces the built-in lexicon, old rows stay attributable to the thing that
    actually made them.
    """

    rating = models.OneToOneField(
        Rating, on_delete=models.CASCADE, related_name="sentiment"
    )

    label = models.CharField(
        max_length=20, choices=SentimentLabel.choices, default=SentimentLabel.UNKNOWN
    )
    polarity = models.FloatField(
        default=0.0, help_text=_("-1 (negative) to +1 (positive).")
    )
    confidence = models.FloatField(default=0.0)

    themes = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Per-theme verdicts — punctuality, hygiene, behaviour, quality — "
            "so a resident reading a profile sees more than one average."
        ),
    )
    detected_language = models.CharField(
        max_length=20,
        blank=True,
        help_text=_("Reviews arrive in Hindi, Hinglish and English, often mixed."),
    )
    engine = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["label", "-created_at"])]

    def __str__(self):
        return f"{self.label} ({self.polarity:+.2f})"

    @property
    def is_reliable(self) -> bool:
        """Whether this is worth showing to a person.

        The built-in lexicon is a stopgap for mixed-language text and says so
        with a low confidence. Presenting a guess as a finding would be worse
        than presenting nothing.
        """
        return self.label != SentimentLabel.UNKNOWN and self.confidence >= 0.5


class TrustSubject(models.TextChoices):
    WORKER = "worker", _("Worker")
    RESIDENT = "resident", _("Resident")


class TrustScoreLog(SocietyScopedModel, TimeStampedModel):
    """Module 9.3 — the audit trail that makes a score defensible.

    One row per recomputation that actually changed something, holding the
    breakdown **as it was at the time**. This is the whole reason a disputed
    score can be answered: recomputing an old score today would produce today's
    answer against today's data, not the number that was acted on.
    """

    subject_type = models.CharField(max_length=20, choices=TrustSubject.choices)
    worker = models.ForeignKey(
        "workers.WorkerProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="trust_logs",
    )
    resident = models.ForeignKey(
        "societies.Resident",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="trust_logs",
    )

    previous_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    new_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    components = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Per-component weight, value and contribution, frozen at the time."),
    )
    trigger = models.CharField(
        max_length=60,
        blank=True,
        help_text=_("What caused the recomputation — a rating, a job, a batch run."),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["worker", "-created_at"]),
            models.Index(fields=["resident", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.subject_type} trust {self.previous_score} → {self.new_score}"

    @property
    def delta(self):
        return self.new_score - self.previous_score


class FlagReason(models.TextChoices):
    BURST = "burst", _("Many ratings from one person in a short window")
    UNIFORM = "uniform", _("Suspiciously uniform ratings for one subject")
    DUPLICATE_TEXT = "duplicate_text", _("Near-identical review text")
    SELF_INTEREST = "self_interest", _("Rater appears connected to the subject")


class FlagStatus(models.TextChoices):
    OPEN = "open", _("Awaiting review")
    UPHELD = "upheld", _("Confirmed — the rating stays withheld")
    DISMISSED = "dismissed", _("Cleared — the rating counts again")


class ReviewFlag(SocietyScopedModel, TimeStampedModel):
    """Module 9.4 — a suspicious rating, escalated rather than deleted.

    The modspec is explicit that flagged reviews go to an administrator rather
    than being removed automatically, and that is the right call: these are
    heuristics, and a genuine burst of five-star reviews is exactly what a
    genuinely good week looks like. Auto-deleting would silently punish honest
    workers with no way to appeal.
    """

    rating = models.ForeignKey(
        Rating, on_delete=models.CASCADE, related_name="flags"
    )
    reason = models.CharField(max_length=30, choices=FlagReason.choices)
    detail = models.CharField(max_length=300, blank=True)

    status = models.CharField(
        max_length=20, choices=FlagStatus.choices, default=FlagStatus.OPEN, db_index=True
    )
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_review_flags",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["rating", "reason"],
                condition=models.Q(status=FlagStatus.OPEN),
                name="one_open_flag_per_rating_reason",
            )
        ]
        indexes = [models.Index(fields=["society", "status", "-created_at"])]

    def __str__(self):
        return f"{self.get_reason_display()} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == FlagStatus.OPEN

    def resolve(self, *, upheld: bool, by, note: str = "") -> bool:
        """Close a flag. Idempotent.

        Dismissing restores the rating to scoring; upholding leaves it withheld.
        Either way the trust score has to be recomputed, which the service layer
        does — the model does not reach across to another app to do it.
        """
        if not self.is_open:
            return False

        self.status = FlagStatus.UPHELD if upheld else FlagStatus.DISMISSED
        self.reviewed_by = by
        self.reviewed_at = timezone.now()
        self.review_note = note
        self.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"]
        )

        self.rating.is_withheld = upheld
        self.rating.save(update_fields=["is_withheld", "updated_at"])
        return True
