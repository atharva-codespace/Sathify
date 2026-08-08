"""
Module 3 — worker onboarding: the orchestration layer.

Two responsibilities, kept apart from both the eight-stage pipeline (which knows
nothing about Django) and the views (which know nothing about OCR):

* running a document through the pipeline and persisting what came out, and
* the approval gate that decides whether a worker enters the platform.

-------------------------------------------------------------------------------
THE FULL AADHAAR NUMBER NEVER REACHES THE DATABASE
-------------------------------------------------------------------------------
``PipelineResult.fields.aadhaar.value`` holds the full twelve digits in memory,
because Stage 7 and Stage 8 need them. :func:`persist_pipeline_result` is the
one place they are touched, and it writes only the last four digits and a keyed
HMAC. Nothing else in this module — or any other — should read
``fields.aadhaar.value``. See ``apps/workers/models.py`` for why.

-------------------------------------------------------------------------------
WHY THIS RUNS SYNCHRONOUSLY
-------------------------------------------------------------------------------
The modspec calls for an asynchronous Celery task. Celery needs a broker and a
worker process, and neither exists in this project's budget — see
docs/free-tier-constraints.md, which also establishes that the CV stack cannot
run in the Render free web service at all. So the pipeline is invoked inline and
``KycDocument.status`` still carries the full PENDING → PROCESSING →
COMPLETED/FAILED lifecycle, which means:

* the client polls the same way it would against a queue, and
* moving to a real queue (or the Hugging Face microservice the constraints doc
  recommends) is a change to :func:`process_kyc_document`'s call site only.

Failure is a first-class outcome, not an exception to be swallowed: SRS 2.5 and
modspec 3.2 both require a manual-entry fallback when OCR is unavailable or
unreadable, so a FAILED document is a normal state the worker can proceed from.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from .models import (
    ConsentPurpose,
    ConsentRecord,
    KycDocument,
    KycStatus,
    WorkerProfile,
    hash_aadhaar,
)
from .ocr import MINIMUM_WORKER_AGE, OcrPipelineError, PipelineResult, run_ocr_pipeline

logger = logging.getLogger(__name__)


class WorkerError(Exception):
    """Base for refusals that are business rules, not bugs."""

    code = "worker_error"


class ConsentRequired(WorkerError):
    code = "consent_required"


class MinorRejected(WorkerError):
    code = "minor_rejected"


class ProfileIncomplete(WorkerError):
    code = "profile_incomplete"


class DuplicateAadhaar(WorkerError):
    code = "duplicate_aadhaar"


# ---------------------------------------------------------------------------
# 3.6 Consent
# ---------------------------------------------------------------------------


def record_consent(user, purpose: str, *, ip_address=None, policy_version="1.0"):
    """Capture consent at the point of collection (Module 3.6).

    Idempotent per purpose: re-granting a live consent returns the existing
    record rather than stacking duplicates, so a retried upload does not inflate
    the audit trail. A previously withdrawn consent creates a *new* row, because
    the DPDP audit trail should show that consent was given, withdrawn, and
    given again — not silently reopened.
    """
    existing = ConsentRecord.objects.filter(
        user=user, purpose=purpose, granted=True, withdrawn_at__isnull=True
    ).first()
    if existing is not None:
        return existing

    return ConsentRecord.objects.create(
        user=user,
        purpose=purpose,
        granted=True,
        policy_version=policy_version,
        ip_address=ip_address,
    )


def require_consent(user, purpose: str) -> None:
    """Refuse to process sensitive data without live consent for that purpose."""
    if not ConsentRecord.has_consent(user, purpose):
        raise ConsentRequired(
            "We need your consent before we can process this document."
        )


# ---------------------------------------------------------------------------
# 3.2 / 3.3 / 3.4 OCR
# ---------------------------------------------------------------------------


def persist_pipeline_result(kyc: KycDocument, result: PipelineResult) -> KycDocument:
    """Write a pipeline run onto its document.

    The only place the full Aadhaar number is read. It is converted immediately
    into the last four digits (for display) and a keyed HMAC (for cross-society
    de-duplication), and then dropped.
    """
    fields = result.fields

    kyc.extracted_name = fields.name.value if fields and fields.name else ""
    kyc.extracted_dob = fields.dob.value if fields and fields.dob else ""
    kyc.extracted_gender = fields.gender.value if fields and fields.gender else ""

    raw_aadhaar = fields.aadhaar.value if fields and fields.aadhaar else ""
    if raw_aadhaar:
        digits = "".join(c for c in raw_aadhaar if c.isdigit())
        kyc.aadhaar_last4 = digits[-4:] if len(digits) >= 4 else ""
        kyc.aadhaar_hash = hash_aadhaar(raw_aadhaar)
    kyc.aadhaar_checksum_valid = result.aadhaar_checksum_valid

    kyc.extracted_age = result.age
    kyc.is_minor = result.is_minor

    kyc.ocr_engine = result.engine_used
    kyc.mean_confidence = result.ocr.mean_confidence if result.ocr else 0.0
    kyc.low_confidence_fields = fields.low_confidence_fields if fields else []

    kyc.cross_check = result.cross_check.as_dict() if result.cross_check else {}
    kyc.has_mismatch = result.has_mismatch
    kyc.ocr_summary = result.as_dict()

    kyc.status = KycStatus.COMPLETED
    kyc.error_message = ""
    kyc.processed_at = timezone.now()
    kyc.save()
    return kyc


def process_kyc_document(kyc: KycDocument, *, form_data: dict | None = None) -> KycDocument:
    """Run the eight stages against an uploaded document and store the result.

    Never raises for an OCR failure. A document the pipeline cannot read is
    marked FAILED with a message, which is the signal the app uses to switch to
    manual entry (SRS 2.5). Raising here would turn a supported degradation into
    a 500.
    """
    kyc.status = KycStatus.PROCESSING
    kyc.save(update_fields=["status", "updated_at"])

    try:
        # Bytes, not ``.path``: only FileSystemStorage has a filesystem path, and
        # production stores documents in Supabase. Stage 1 takes either, so
        # reading the file through the storage API keeps this backend-agnostic.
        with kyc.document_image.open("rb") as handle:
            document_bytes = handle.read()

        result = run_ocr_pipeline(
            document_bytes,
            filename=kyc.document_image.name,
            form_data=form_data,
        )
    except OcrPipelineError as exc:
        logger.warning("OCR failed for KYC document %s: %s", kyc.pk, exc)
        kyc.status = KycStatus.FAILED
        kyc.error_message = str(exc)
        kyc.processed_at = timezone.now()
        kyc.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
        return kyc
    except Exception as exc:  # noqa: BLE001 — see below
        # A crash inside a third-party OCR engine must not cost the worker their
        # upload. It is recorded as a failure so they can retry or type the
        # fields in, and logged with a traceback so it can actually be fixed.
        logger.exception("Unexpected OCR error for KYC document %s", kyc.pk)
        kyc.status = KycStatus.FAILED
        kyc.error_message = f"The document could not be processed: {exc}"
        kyc.processed_at = timezone.now()
        kyc.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
        return kyc

    return persist_pipeline_result(kyc, result)


def apply_manual_corrections(kyc: KycDocument, *, name="", dob="", gender="", aadhaar="") -> KycDocument:
    """The worker confirms or corrects what OCR read (Module 3.2).

    The pre-fill is a convenience, never an authority — the spec has the worker
    confirm or correct rather than retype, and this is where their answer wins.
    A corrected Aadhaar number is re-validated and re-hashed here, so a fix to an
    OCR misread also fixes the de-duplication key.
    """
    from .ocr import is_valid_aadhaar, normalise_aadhaar

    updated = ["updated_at"]

    if name:
        kyc.extracted_name = name
        updated.append("extracted_name")
    if dob:
        kyc.extracted_dob = dob
        updated.append("extracted_dob")
    if gender:
        kyc.extracted_gender = gender
        updated.append("extracted_gender")

    if aadhaar:
        digits = normalise_aadhaar(aadhaar)
        kyc.aadhaar_last4 = digits[-4:] if len(digits) >= 4 else ""
        kyc.aadhaar_hash = hash_aadhaar(aadhaar)
        kyc.aadhaar_checksum_valid = is_valid_aadhaar(aadhaar)
        updated += ["aadhaar_last4", "aadhaar_hash", "aadhaar_checksum_valid"]

    # Whatever the worker confirmed is no longer low-confidence — a human has
    # now looked at it, which is exactly what the flag was asking for.
    if kyc.low_confidence_fields:
        confirmed = {
            "name": bool(name),
            "dob": bool(dob),
            "gender": bool(gender),
            "aadhaar": bool(aadhaar),
        }
        kyc.low_confidence_fields = [
            f for f in kyc.low_confidence_fields if not confirmed.get(f)
        ]
        updated.append("low_confidence_fields")

    # A document that failed OCR entirely is completed by the manual entry,
    # which is the whole point of the fallback path.
    if kyc.status == KycStatus.FAILED and (name or dob or aadhaar):
        kyc.status = KycStatus.COMPLETED
        kyc.error_message = ""
        updated += ["status", "error_message"]

    kyc.save(update_fields=list(dict.fromkeys(updated)))
    return kyc


# ---------------------------------------------------------------------------
# 3.5 Admin verification & activation gate
# ---------------------------------------------------------------------------


def approval_blockers(worker: WorkerProfile) -> list[str]:
    """Reasons this worker cannot be approved, in plain language.

    Returned rather than raised so the review screen can show every problem at
    once instead of revealing them one refused click at a time.
    """
    blockers: list[str] = []
    kyc = worker.latest_kyc

    if kyc is None:
        blockers.append("No Aadhaar document has been uploaded yet.")
    else:
        if kyc.is_minor:
            # Module 3.4 — a hard block, explicitly not admin discretion.
            blockers.append(
                f"The document shows an age under {MINIMUM_WORKER_AGE}. This is an "
                "automatic rejection and cannot be overridden."
            )
        if kyc.status == KycStatus.PENDING:
            blockers.append("The document has not been processed yet.")

    if not worker.photo:
        # Approving without one produces a worker who is approved but can never
        # appear in search (WorkerProfile.is_searchable) and would be turned
        # away at the gate — a silent failure worth refusing loudly.
        blockers.append(
            "No profile photo. It is the reference image for gate face "
            "verification, so a worker without one cannot be admitted."
        )

    if not worker.service_types.exists():
        blockers.append("No service types selected, so this worker cannot be found.")

    return blockers


def duplicate_warning(worker: WorkerProfile):
    """Another worker already registered with the same Aadhaar, if any.

    Surfaced as a warning rather than a blocker: the same person legitimately
    moving between societies looks identical to a fraudulent double
    registration, and only a human can tell them apart.
    """
    kyc = worker.latest_kyc
    return kyc.find_duplicate() if kyc else None


@transaction.atomic
def approve_worker(worker: WorkerProfile, *, reviewed_by) -> WorkerProfile:
    """Module 3.5 — the gate that lets a worker onto the platform."""
    blockers = approval_blockers(worker)
    if blockers:
        kyc = worker.latest_kyc
        if kyc is not None and kyc.is_minor:
            raise MinorRejected(blockers[0])
        raise ProfileIncomplete(" ".join(blockers))

    worker.reviewed_at = timezone.now()
    worker.reviewed_by = reviewed_by
    worker.rejection_reason = ""
    worker.save(update_fields=["reviewed_at", "reviewed_by", "rejection_reason", "updated_at"])

    # User.is_approved stays the single source of truth for platform access.
    worker.user.approve(approved_by=reviewed_by)

    # Module 10 — ACCOUNT is safety-critical and cannot be muted: a worker who
    # is never told they were approved cannot start working.
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    notify(
        recipient=worker.user,
        category=NotificationCategory.ACCOUNT,
        title="You are verified",
        body="Residents can now find you and send you work.",
        data={"route": "/schedule"},
    )

    logger.info("Worker %s approved by %s", worker.pk, getattr(reviewed_by, "pk", None))
    return worker


@transaction.atomic
def reject_worker(worker: WorkerProfile, *, reason: str, reviewed_by) -> WorkerProfile:
    """Reject with a reason the worker can act on.

    Approval is revoked rather than the record deleted: the worker keeps their
    account, sees why, and can correct and resubmit.
    """
    worker.reviewed_at = timezone.now()
    worker.reviewed_by = reviewed_by
    worker.rejection_reason = reason
    worker.save(update_fields=["reviewed_at", "reviewed_by", "rejection_reason", "updated_at"])

    worker.user.is_approved = False
    worker.user.save(update_fields=["is_approved", "updated_at"])

    # Being rejected and not told is the failure this category's mute exclusion
    # exists to prevent — the worker cannot correct what they do not know about.
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    notify(
        recipient=worker.user,
        category=NotificationCategory.ACCOUNT,
        title="Your registration needs attention",
        body=reason[:400],
        data={"route": "/onboarding/worker"},
    )

    logger.info("Worker %s rejected by %s", worker.pk, getattr(reviewed_by, "pk", None))
    return worker


def auto_reject_if_minor(kyc: KycDocument, *, reviewed_by=None) -> bool:
    """Module 3.4 — reject a confirmed minor without waiting for an administrator.

    The specification calls this a hard block rather than something queued for
    discretion, so it fires as soon as the age is known. Returns whether it did.
    """
    if not kyc.is_minor:
        return False

    reject_worker(
        kyc.worker,
        reason=(
            f"The uploaded document shows an age under {MINIMUM_WORKER_AGE}. "
            "Sathify cannot onboard minors."
        ),
        reviewed_by=reviewed_by,
    )
    logger.warning("Worker %s auto-rejected: under age", kyc.worker_id)
    return True


__all__ = [
    "ConsentRequired",
    "DuplicateAadhaar",
    "MinorRejected",
    "ProfileIncomplete",
    "WorkerError",
    "apply_manual_corrections",
    "approval_blockers",
    "approve_worker",
    "auto_reject_if_minor",
    "duplicate_warning",
    "persist_pipeline_result",
    "process_kyc_document",
    "record_consent",
    "reject_worker",
    "require_consent",
]
