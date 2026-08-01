"""
Module 3 — Worker Onboarding & KYC.

The trust foundation of the platform: a worker cannot appear in search, be
hired, or be admitted at the gate until this pipeline completes and an
administrator approves them.

-------------------------------------------------------------------------------
AADHAAR STORAGE — READ BEFORE CHANGING
-------------------------------------------------------------------------------
The full 12-digit Aadhaar number is NEVER persisted. What is stored is:

  * ``aadhaar_last4``  — the last four digits, for display as "XXXX XXXX 9012"
  * ``aadhaar_hash``   — a keyed HMAC-SHA256 of the full number

The hash serves the cross-society de-duplication requirement: the same worker
registering at two societies with slightly different spellings is caught by
comparing hashes, without the platform ever holding the number itself. It is
keyed (an HMAC with a server-side pepper) rather than a plain digest, because
the Aadhaar space is small enough that an unkeyed SHA-256 could be brute-forced
from a leaked database in minutes.

UIDAI restricts how private entities may store Aadhaar numbers, and India's
Digital Personal Data Protection Act 2023 governs the consent trail. This design
is a deliberately conservative engineering choice — it is NOT legal advice, and
the modspec's instruction to have full-number storage reviewed by counsel stands.
"""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def hash_aadhaar(raw_number: str) -> str:
    """Keyed HMAC-SHA256 of a normalised Aadhaar number.

    Used as a global de-duplication key. The pepper defaults to SECRET_KEY;
    set AADHAAR_HASH_PEPPER separately in production so that rotating
    SECRET_KEY does not invalidate every stored hash.
    """
    from apps.workers.ocr.verhoeff import normalise_aadhaar

    digits = normalise_aadhaar(raw_number)
    if not digits:
        return ""

    pepper = getattr(settings, "AADHAAR_HASH_PEPPER", "") or settings.SECRET_KEY
    return hmac.new(
        pepper.encode("utf-8"), digits.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class ServiceType(TimeStampedModel):
    """A category of domestic work (maid, cook, cleaner, …).

    Platform-level rather than per-society, so that a worker's service types
    stay meaningful if they later work at a different society.
    """

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    description = models.CharField(max_length=200, blank=True)
    icon = models.CharField(
        max_length=40,
        blank=True,
        help_text=_("Material icon name; the Flutter UI is icon-led for low-literacy users."),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def worker_photo_path(instance, filename):
    return f"workers/photos/society_{instance.user.society_id}/{instance.user_id}/{filename}"


class WorkerProfile(TimeStampedModel):
    """A domestic worker's profile (Module 3.1).

    As with residents, ``User.is_approved`` remains the single source of truth
    for platform access. This model holds the evidence and the review trail an
    administrator uses to make that decision.
    """

    user = models.OneToOneField(
        "accounts.User", on_delete=models.CASCADE, related_name="worker_profile"
    )
    service_types = models.ManyToManyField(ServiceType, related_name="workers", blank=True)

    photo = models.ImageField(
        upload_to=worker_photo_path,
        blank=True,
        help_text=_(
            "Profile photo. Also the reference image for gate face verification "
            "in Module 7, which is why it is required before approval."
        ),
    )

    years_of_experience = models.PositiveSmallIntegerField(default=0)
    bio = models.TextField(blank=True, max_length=500)
    languages_spoken = models.CharField(
        max_length=120, blank=True, help_text=_("Comma-separated, e.g. Hindi, Marathi")
    )

    expected_monthly_rate = models.PositiveIntegerField(
        null=True, blank=True, help_text=_("Indicative monthly rate in INR.")
    )

    # --- Availability (Module 3.1, consumed by Modules 4 and 5) -------------
    is_available = models.BooleanField(
        default=True,
        help_text=_("Worker's own toggle. Hides them from search without deactivating them."),
    )
    available_from = models.TimeField(null=True, blank=True)
    available_until = models.TimeField(null=True, blank=True)

    # --- Trust score (populated by Module 9) --------------------------------
    trust_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=_("Computed by Module 9. Zero until enough history accumulates."),
    )
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "How many ratings the average is over. Maintained by Module 9. "
            "Module 4.3 needs it to shrink a sparse average toward the prior — "
            "one five-star review is not the same evidence as fifty."
        ),
    )
    completed_engagements = models.PositiveIntegerField(default=0)

    # --- Review trail (Module 3.5) -----------------------------------------
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_workers",
    )
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-trust_score", "-average_rating"]
        indexes = [
            # Supports Module 4's search: approved, available workers in a society.
            models.Index(fields=["is_available", "-trust_score"]),
        ]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.phone_number} (worker)"

    @property
    def society_id(self):
        return self.user.society_id

    @property
    def is_approved(self) -> bool:
        return self.user.is_approved

    @property
    def latest_kyc(self):
        return self.kyc_documents.order_by("-created_at").first()

    @property
    def is_searchable(self) -> bool:
        """Whether Module 4 may surface this worker.

        Every condition must hold: approved by an administrator, self-marked
        available, and carrying a photo — the photo is the reference image gate
        verification compares against, so a worker without one cannot be
        admitted anyway.
        """
        return bool(self.user.is_approved and self.is_available and self.photo)


class KycStatus(models.TextChoices):
    PENDING = "pending", _("Awaiting processing")
    PROCESSING = "processing", _("OCR in progress")
    COMPLETED = "completed", _("OCR completed")
    FAILED = "failed", _("OCR failed — manual entry required")


def kyc_document_path(instance, filename):
    return f"workers/kyc/society_{instance.worker.user.society_id}/{instance.worker_id}/{filename}"


class KycDocument(TimeStampedModel):
    """An uploaded Aadhaar document and everything the OCR pipeline made of it.

    Kept as a separate model rather than fields on ``WorkerProfile`` because a
    worker may re-upload after a poor scan, and each attempt is retained for the
    audit trail (SRS 5.5).
    """

    worker = models.ForeignKey(
        WorkerProfile, on_delete=models.CASCADE, related_name="kyc_documents"
    )
    document_image = models.FileField(upload_to=kyc_document_path)

    status = models.CharField(
        max_length=20, choices=KycStatus.choices, default=KycStatus.PENDING, db_index=True
    )
    error_message = models.TextField(blank=True)

    # --- Stage 6 output -----------------------------------------------------
    extracted_name = models.CharField(max_length=120, blank=True)
    extracted_dob = models.CharField(max_length=20, blank=True)
    extracted_gender = models.CharField(max_length=20, blank=True)

    # --- Aadhaar: masked + hashed only. NEVER the full number. --------------
    aadhaar_last4 = models.CharField(max_length=4, blank=True)
    aadhaar_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Keyed HMAC of the full number. Global de-duplication key."),
    )
    aadhaar_checksum_valid = models.BooleanField(
        default=False, help_text=_("Stage 7 — Verhoeff validation result.")
    )

    # --- Module 3.4: age gate ----------------------------------------------
    extracted_age = models.PositiveSmallIntegerField(null=True, blank=True)
    is_minor = models.BooleanField(
        default=False,
        help_text=_("Under 18. Triggers automatic, non-overridable rejection."),
    )

    # --- Stage 5 / Stage 8 diagnostics -------------------------------------
    ocr_engine = models.CharField(max_length=30, blank=True)
    mean_confidence = models.FloatField(default=0.0)
    low_confidence_fields = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Fields read below the threshold; must be confirmed manually."),
    )
    cross_check = models.JSONField(
        default=dict, blank=True, help_text=_("Stage 8 per-field matched/mismatch verdicts.")
    )
    has_mismatch = models.BooleanField(default=False)
    ocr_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Full safe pipeline summary, shown in the admin review screen."),
    )

    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"KYC for {self.worker} ({self.status})"

    @property
    def masked_aadhaar(self) -> str:
        return f"XXXX XXXX {self.aadhaar_last4}" if self.aadhaar_last4 else "XXXX XXXX XXXX"

    @property
    def needs_manual_confirmation(self) -> bool:
        """Whether a critical field was read too poorly to auto-fill."""
        return bool(set(self.low_confidence_fields) & {"aadhaar", "dob"})

    def find_duplicate(self):
        """Another worker already registered with the same Aadhaar number.

        Catches the same person registering at two societies under slightly
        different details — the profile data varies, the Aadhaar hash does not.
        """
        if not self.aadhaar_hash:
            return None
        return (
            KycDocument.objects.filter(aadhaar_hash=self.aadhaar_hash)
            .exclude(worker_id=self.worker_id)
            .select_related("worker__user")
            .first()
        )


class ConsentPurpose(models.TextChoices):
    KYC_AADHAAR = "kyc_aadhaar", _("Identity verification using Aadhaar")
    FACE_BIOMETRIC = "face_biometric", _("Face verification at the society gate")
    DATA_PROCESSING = "data_processing", _("General platform data processing")


class ConsentRecord(TimeStampedModel):
    """Module 3.6 — timestamped, purpose-limited consent.

    India's Digital Personal Data Protection Act 2023 requires explicit consent
    for processing sensitive personal data, captured at the point of collection
    and tied to a specific stated purpose. One row per purpose, never a single
    blanket flag: withdrawing consent for face verification must not silently
    revoke the identity verification the worker's approval rests on.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="consent_records"
    )
    purpose = models.CharField(max_length=30, choices=ConsentPurpose.choices)

    granted = models.BooleanField(default=True)
    granted_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    policy_version = models.CharField(
        max_length=20,
        default="1.0",
        help_text=_("Which privacy policy version the user actually agreed to."),
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-granted_at"]
        indexes = [models.Index(fields=["user", "purpose", "granted"])]

    def __str__(self):
        state = "granted" if self.is_active else "withdrawn"
        return f"{self.user} — {self.get_purpose_display()} ({state})"

    @property
    def is_active(self) -> bool:
        return self.granted and self.withdrawn_at is None

    def withdraw(self):
        """Withdraw consent. Idempotent."""
        if self.withdrawn_at is not None:
            return
        self.withdrawn_at = timezone.now()
        self.granted = False
        self.save(update_fields=["withdrawn_at", "granted", "updated_at"])

    @classmethod
    def has_consent(cls, user, purpose: str) -> bool:
        return cls.objects.filter(
            user=user, purpose=purpose, granted=True, withdrawn_at__isnull=True
        ).exists()
