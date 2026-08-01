"""
Module 2 — Society & Resident Onboarding.

Entity shape:

    Society ──< Tower ──< Flat ──< Resident ──1:1── User
       └──< Gate

``Society`` is also the multi-tenancy anchor for the whole platform
(``accounts.User.society`` and ``core.SocietyScopedModel`` both point at it),
which is why it was defined during scaffolding ahead of the rest of this module.

Approval note: platform access is governed by ``User.is_approved``, which stays
the single source of truth. ``Resident`` holds the flat linkage and proof of
residence — the evidence an administrator reviews *before* flipping that flag.
Duplicating an approval state here would create two answers to one question.
"""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class SocietyStatus(models.TextChoices):
    """A society is inert until a platform-level check confirms it is real."""

    PENDING = "pending", _("Pending verification")
    ACTIVE = "active", _("Active")
    SUSPENDED = "suspended", _("Suspended")
    REJECTED = "rejected", _("Rejected")


class Society(TimeStampedModel):
    """A registered residential community (SRS Appendix A: Societies).

    Registration is submitted by a prospective society administrator and sits in
    ``PENDING`` until verified, which is what prevents fabricated societies from
    entering the platform (Module 2.1).
    """

    name = models.CharField(max_length=200)
    registration_number = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Society registration / RWA number, where one exists."),
    )

    # --- Address ------------------------------------------------------------
    address_line = models.CharField(max_length=255)
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=6, db_index=True)

    # Coarse coordinates, used by the proximity term of the worker
    # recommendation score (Module 4.3) and by resident GPS self check-in
    # (Module 13.3). Nullable: a society is usable without them.
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # --- Configuration (Module 2.5) -----------------------------------------
    total_towers = models.PositiveSmallIntegerField(default=1)
    total_flats = models.PositiveIntegerField(
        default=0, help_text=_("Declared flat count, used to sanity-check the mapping.")
    )
    gate_count = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Number of staffed gates; each guard is assigned to one."),
    )
    booking_notice_hours = models.PositiveSmallIntegerField(
        default=12,
        help_text=_("Minimum notice required for a one-day booking (Module 5)."),
    )
    guard_shift_hours = models.PositiveSmallIntegerField(
        default=8, help_text=_("Length of a guard shift, used by Module 7 rostering.")
    )
    allow_resident_self_checkin = models.BooleanField(
        default=True,
        help_text=_(
            "Permits the GPS-geofence attendance fallback when no guard is on "
            "duty (Module 13.3, secondary attendance tier)."
        ),
    )

    status = models.CharField(
        max_length=20,
        choices=SocietyStatus.choices,
        default=SocietyStatus.PENDING,
        db_index=True,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = _("societies")
        ordering = ["name"]
        constraints = [
            # Two societies may share a name across cities, but not within one
            # pincode — that is almost certainly a duplicate registration.
            models.UniqueConstraint(
                fields=["name", "pincode"], name="unique_society_name_per_pincode"
            ),
        ]

    def __str__(self):
        return f"{self.name}, {self.city}"

    @property
    def is_active(self) -> bool:
        return self.status == SocietyStatus.ACTIVE

    @property
    def mapped_flat_count(self) -> int:
        """Flats actually created, as opposed to the declared ``total_flats``."""
        return Flat.objects.filter(tower__society=self).count()

    def activate(self):
        """Verify the society and activate its administrators.

        Approving the society is what lets its administrators start operating —
        without this, a verified society would still have no one able to act in
        it. Idempotent.
        """
        if self.status == SocietyStatus.ACTIVE:
            return

        self.status = SocietyStatus.ACTIVE
        self.verified_at = timezone.now()
        self.rejection_reason = ""
        self.save(update_fields=["status", "verified_at", "rejection_reason", "updated_at"])

        from apps.accounts.models import Role

        self.users.filter(role=Role.SOCIETY_ADMIN, is_approved=False).update(
            is_approved=True, approved_at=timezone.now()
        )


class Tower(TimeStampedModel):
    """A tower, wing or block within a society (Module 2.2)."""

    society = models.ForeignKey(Society, on_delete=models.CASCADE, related_name="towers")
    name = models.CharField(max_length=50, help_text=_('e.g. "A", "Tower 1", "West Wing"'))
    floors = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["society", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["society", "name"], name="unique_tower_name_per_society"
            )
        ]

    def __str__(self):
        return f"{self.society.name} - {self.name}"


class Flat(TimeStampedModel):
    """A single dwelling. Residents register against a flat, not a society.

    Anchoring residents to a specific flat is what makes occupancy accurate and
    what lets Module 2.4 designate one primary account holder per household.
    """

    tower = models.ForeignKey(Tower, on_delete=models.CASCADE, related_name="flats")
    number = models.CharField(max_length=20, help_text=_('e.g. "301", "B-12"'))
    floor = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["tower", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["tower", "number"], name="unique_flat_number_per_tower"
            )
        ]
        indexes = [models.Index(fields=["tower", "number"])]

    def __str__(self):
        return f"{self.tower.name}-{self.number}"

    @property
    def society_id_via_tower(self):
        return self.tower.society_id

    @property
    def primary_resident(self):
        return self.residents.filter(is_primary=True).first()


class Gate(TimeStampedModel):
    """A staffed entry point (Module 2.5).

    Guards are assigned per gate, and every entry/exit log in Module 7 records
    which gate it happened at.
    """

    society = models.ForeignKey(Society, on_delete=models.CASCADE, related_name="gates")
    name = models.CharField(max_length=50, help_text=_('e.g. "Main Gate", "Service Gate"'))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["society", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["society", "name"], name="unique_gate_name_per_society"
            )
        ]

    def __str__(self):
        return f"{self.society.name} - {self.name}"


class ResidentRelationship(models.TextChoices):
    OWNER = "owner", _("Owner")
    TENANT = "tenant", _("Tenant")
    FAMILY_MEMBER = "family_member", _("Family member")


def resident_proof_upload_path(instance, filename):
    """Namespaced by society so storage stays browsable and scoped."""
    return f"residents/society_{instance.flat.tower.society_id}/{instance.user_id}/{filename}"


class Resident(TimeStampedModel):
    """A resident's link to their flat, plus their proof of residence.

    Created by the resident after registration and reviewed by an administrator
    (Module 2.3). Approval itself is recorded on ``User.is_approved``.
    """

    user = models.OneToOneField(
        "accounts.User", on_delete=models.CASCADE, related_name="resident_profile"
    )
    flat = models.ForeignKey(Flat, on_delete=models.PROTECT, related_name="residents")

    relationship = models.CharField(
        max_length=20,
        choices=ResidentRelationship.choices,
        default=ResidentRelationship.OWNER,
    )

    # --- Module 2.4: multi-resident-per-flat --------------------------------
    is_primary = models.BooleanField(
        default=False,
        help_text=_(
            "The flat's primary account holder. Only the primary may create or "
            "edit hires and schedules, which prevents two people in one "
            "household issuing conflicting bookings."
        ),
    )

    proof_document = models.FileField(
        upload_to=resident_proof_upload_path,
        blank=True,
        help_text=_("Rent agreement, sale deed, utility bill, or similar."),
    )
    move_in_date = models.DateField(null=True, blank=True)

    # --- Trust (computed by Module 9) ---------------------------------------
    # SRS 3.9 scores both sides. A worker deciding whether to accept a hire
    # request is entitled to the same signal a resident gets about them —
    # chiefly whether this household actually pays.
    trust_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=_("0–100, computed by Module 9. Zero until there is history."),
    )
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(default=0)

    # --- Review trail (Module 2.3) ------------------------------------------
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_residents",
    )
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Module 2.4: at most one primary account holder per flat. Enforced
            # in the database, not just the serializer — a race between two
            # concurrent requests would otherwise create two primaries.
            models.UniqueConstraint(
                fields=["flat"],
                condition=models.Q(is_primary=True),
                name="one_primary_resident_per_flat",
            )
        ]
        indexes = [models.Index(fields=["flat", "is_primary"])]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.phone_number} @ {self.flat}"

    @property
    def society_id(self):
        return self.flat.tower.society_id

    @property
    def household(self):
        """Everyone else registered against the same flat (Module 2.4)."""
        return Resident.objects.filter(flat=self.flat).exclude(pk=self.pk)
