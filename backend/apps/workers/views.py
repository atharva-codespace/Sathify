"""
Module 3 — Worker Onboarding & KYC: API views.

Endpoint map (mounted at /api/v1/workers/)::

    GET    service-types/           the catalogue a worker picks from

    GET    profile/                 own worker profile                    (3.1)
    POST   profile/                 create it
    PATCH  profile/                 update it (multipart for the photo)

    POST   kyc/                     upload an Aadhaar document + consent  (3.2/3.6)
    GET    kyc/                     own attempts
    GET    kyc/<id>/                one attempt — poll status here        (3.2)
    POST   kyc/<id>/confirm/        confirm or correct the pre-fill       (3.2/3.3)

    GET    consents/                own consent records                   (3.6)
    POST   consents/                grant one
    POST   consents/<id>/withdraw/  withdraw one

    GET    review/pending/          approval queue (admin)                (3.5)
    GET    review/<id>/             one worker's full review payload
    POST   review/<id>/decide/      approve or reject                     (3.5)

-------------------------------------------------------------------------------
THESE ENDPOINTS ARE REACHABLE WHILE UNAPPROVED
-------------------------------------------------------------------------------
Everything a worker does here happens *before* they are approved — building the
profile and uploading the document is precisely what an administrator reviews.
Requiring ``IsApproved`` on them would deadlock onboarding, exactly as it would
for a resident claiming a flat (Module 2.3). The review endpoints are the
opposite: administrator-only.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsSocietyAdmin, IsWorker

from .models import (
    ConsentPurpose,
    ConsentRecord,
    KycDocument,
    KycStatus,
    ServiceType,
    WorkerProfile,
)
from .serializers import (
    ConsentGrantSerializer,
    ConsentRecordSerializer,
    KycDocumentSerializer,
    KycManualEntrySerializer,
    KycUploadSerializer,
    ServiceTypeSerializer,
    WorkerDecisionSerializer,
    WorkerProfileSerializer,
    WorkerProfileWriteSerializer,
    WorkerReviewSerializer,
)
from .services import (
    MinorRejected,
    ProfileIncomplete,
    WorkerError,
    apply_manual_corrections,
    approve_worker,
    auto_reject_if_minor,
    process_kyc_document,
    record_consent,
    reject_worker,
)

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _client_ip(request):
    """Best-effort caller IP for the consent audit trail (Module 3.6)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # Render sits behind a proxy, so the client address is the first hop.
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _own_profile(request):
    return WorkerProfile.objects.filter(user=request.user).first()


def _save_or_storage_error(serializer, **kwargs):
    """Save a profile, turning a media-backend failure into a real answer.

    Returns the saved instance, or a :class:`Response` the caller should return
    as-is.

    Saving a profile writes the photo, and writing the photo is the one step
    that leaves this process. It had no handling at all, so a misconfigured
    media backend surfaced as a bare 500 — which is exactly what a worker saw
    while a missing storage bucket made every upload fail: "something went
    wrong on our side", with nothing in it to act on and nothing distinguishing
    it from a code fault.

    The KYC view has said the useful thing for a while
    (``storage_unavailable``, 503, retryable); this is the same treatment for
    the other endpoint that stores a file. A ``DatabaseError`` is deliberately
    left to propagate: it is not retryable and should be logged as the fault it
    is rather than dressed up as a transient outage.
    """
    try:
        return serializer.save(**kwargs)
    except DatabaseError:
        raise
    except Exception:  # noqa: BLE001 — any storage error, not just one library's
        logger.exception("Could not store the profile photo")
        return _error(
            "storage_unavailable",
            "We could not save your photo just now. Please try again in a moment.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@extend_schema(tags=["Workers"], summary="List service types")
class ServiceTypeListView(generics.ListAPIView):
    """What a worker chooses from, and what Modules 4 and 5 filter on."""

    serializer_class = ServiceTypeSerializer
    permission_classes = [IsAuthenticated]
    queryset = ServiceType.objects.filter(is_active=True)
    pagination_class = None  # Short, fixed catalogue.


# ---------------------------------------------------------------------------
# 3.1 Worker profile
# ---------------------------------------------------------------------------


@extend_schema(tags=["Workers"], summary="Read, create or update your worker profile")
class MyWorkerProfileView(APIView):
    """Module 3.1.

    One endpoint for all three verbs because there is exactly one profile per
    worker: a route with a primary key would invite a client to guess at
    somebody else's.
    """

    permission_classes = [IsWorker]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = WorkerProfileSerializer

    def get(self, request):
        profile = _own_profile(request)
        if profile is None:
            return _error(
                "not_found",
                "You have not created your worker profile yet.",
                status.HTTP_404_NOT_FOUND,
            )
        return Response(
            WorkerProfileSerializer(profile, context={"request": request}).data
        )

    def post(self, request):
        if _own_profile(request) is not None:
            return _error(
                "already_exists",
                "You already have a worker profile. Update it instead.",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = WorkerProfileWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = _save_or_storage_error(serializer, user=request.user)
        if isinstance(profile, Response):
            return profile

        logger.info("Worker profile created for user %s", request.user.pk)
        return Response(
            {
                "profile": WorkerProfileSerializer(
                    profile, context={"request": request}
                ).data,
                "message": "Profile saved. Upload your Aadhaar document next.",
            },
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request):
        profile = _own_profile(request)
        if profile is None:
            return _error(
                "not_found",
                "Create your worker profile first.",
                status.HTTP_404_NOT_FOUND,
            )

        serializer = WorkerProfileWriteSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        profile = _save_or_storage_error(serializer)
        if isinstance(profile, Response):
            return profile

        return Response(
            {
                "profile": WorkerProfileSerializer(
                    profile, context={"request": request}
                ).data,
                "message": "Profile updated.",
            }
        )


# ---------------------------------------------------------------------------
# 3.2 / 3.3 / 3.4 KYC
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Workers"],
    summary="Upload an Aadhaar document",
    request=KycUploadSerializer,
    responses=KycDocumentSerializer,
)
class KycUploadView(APIView):
    """Modules 3.2, 3.3, 3.4 and 3.6, in one request.

    Consent is captured here rather than earlier because the DPDP Act requires
    it at the point of collection, and it is recorded *before* the file is
    stored so a refusal leaves no document behind.

    The pipeline runs inline; see ``services.process_kyc_document`` for why
    there is no queue. An OCR failure is not an error response: the document is
    stored as FAILED and the worker continues via manual entry.
    """

    permission_classes = [IsWorker]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = KycUploadSerializer

    def post(self, request):
        profile = _own_profile(request)
        if profile is None:
            return _error(
                "no_profile",
                "Create your worker profile before uploading documents.",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = KycUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Module 3.6 — recorded first, so the consent trail reflects what the
        # worker agreed to even if the steps below fail.
        record_consent(
            request.user,
            ConsentPurpose.KYC_AADHAAR,
            ip_address=_client_ip(request),
        )

        # Storing the file is the one step here that talks to something outside
        # this process. A misconfigured or unreachable media backend used to
        # surface as a bare 500; the worker gets a retryable message instead,
        # and the traceback goes to the log where it can be fixed.
        try:
            kyc = KycDocument.objects.create(
                worker=profile,
                document_image=serializer.validated_data["document"],
            )
        except DatabaseError:
            # NOT a storage problem, and it matters that the two are told
            # apart. This branch used to be swallowed by the catch below and
            # reported as "we could not save your document", which sends the
            # worker into a retry loop against a fault that will reproduce
            # every time, and sends whoever is debugging it to the wrong
            # subsystem. The code is distinct so a log line identifies which.
            logger.exception(
                "Database error recording KYC for worker %s", profile.pk
            )
            return _error(
                "kyc_record_failed",
                "We could not record your document just now. Please try again "
                "in a moment.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:  # noqa: BLE001 — any storage error, not just one library's
            # Writing the file is the one step here that leaves this process,
            # so this is a misconfigured or unreachable media backend. See
            # `manage.py check_media_storage`, which reproduces exactly this
            # write and reports why it failed.
            logger.exception("Could not store KYC document for worker %s", profile.pk)
            return _error(
                "storage_unavailable",
                "We could not save your document just now. Please try again in a moment.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        kyc = process_kyc_document(kyc, form_data=serializer.form_data())

        # Module 3.4 — a confirmed minor is rejected immediately rather than
        # queued for discretion. The spec calls this a hard block.
        auto_rejected = auto_reject_if_minor(kyc)

        if auto_rejected:
            message = (
                "The document shows an age under 18, so this registration has "
                "been rejected. Sathify cannot onboard minors."
            )
        elif kyc.status == KycStatus.FAILED:
            message = (
                "We could not read that document. You can retake the photo, or "
                "enter your details manually."
            )
        elif kyc.needs_manual_confirmation:
            message = (
                "We read your document, but please check the highlighted fields "
                "before continuing."
            )
        else:
            message = "Document read successfully. Please confirm your details."

        return Response(
            {
                "kyc": KycDocumentSerializer(kyc).data,
                "auto_rejected": auto_rejected,
                "message": message,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Workers"], summary="List your KYC attempts")
class MyKycListView(generics.ListAPIView):
    """Every attempt is kept, so a re-upload after a poor scan stays auditable."""

    serializer_class = KycDocumentSerializer
    permission_classes = [IsWorker]
    queryset = KycDocument.objects.none()  # declared for schema generation

    def get_queryset(self):
        return KycDocument.objects.filter(worker__user=self.request.user)


@extend_schema(tags=["Workers"], summary="Retrieve one KYC attempt")
class MyKycDetailView(generics.RetrieveAPIView):
    """Also the poll endpoint while a document is PROCESSING."""

    serializer_class = KycDocumentSerializer
    permission_classes = [IsWorker]
    queryset = KycDocument.objects.none()  # declared for schema generation

    def get_queryset(self):
        return KycDocument.objects.filter(worker__user=self.request.user)


@extend_schema(
    tags=["Workers"],
    summary="Confirm or correct the extracted fields",
    request=KycManualEntrySerializer,
    responses=KycDocumentSerializer,
)
class KycConfirmView(APIView):
    """Module 3.2/3.3, and the SRS 2.5 manual-entry fallback.

    The OCR pre-fill is a convenience, never an authority. This is where the
    worker's own answer wins — including for a document OCR could not read at
    all, which is completed by typing the fields in.
    """

    permission_classes = [IsWorker]
    serializer_class = KycManualEntrySerializer

    def post(self, request, pk):
        kyc = KycDocument.objects.filter(pk=pk, worker__user=request.user).first()
        if kyc is None:
            return _error("not_found", "Document not found.", status.HTTP_404_NOT_FOUND)

        if kyc.is_minor:
            # Module 3.4 is non-overridable, including by editing the date of
            # birth afterwards — otherwise the hard block would be one
            # correction away from being bypassed.
            return _error(
                "minor_rejected",
                "This registration was rejected because the document shows an "
                "age under 18. It cannot be amended.",
                status.HTTP_409_CONFLICT,
            )

        serializer = KycManualEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        kyc = apply_manual_corrections(
            kyc,
            name=data.get("name", ""),
            dob=data.get("dob", ""),
            gender=data.get("gender", ""),
            aadhaar=data.get("aadhaar_number", ""),
        )

        return Response(
            {
                "kyc": KycDocumentSerializer(kyc).data,
                "message": "Details confirmed. Your society administrator will review them.",
            }
        )


# ---------------------------------------------------------------------------
# 3.6 Consent
# ---------------------------------------------------------------------------


@extend_schema(tags=["Workers"], summary="List or grant consent records")
class ConsentListCreateView(generics.ListCreateAPIView):
    """Module 3.6 — one row per purpose, never a single blanket flag."""

    permission_classes = [IsAuthenticated]
    queryset = ConsentRecord.objects.none()  # declared for schema generation

    def get_serializer_class(self):
        return (
            ConsentGrantSerializer
            if self.request.method == "POST"
            else ConsentRecordSerializer
        )

    def get_queryset(self):
        return ConsentRecord.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = ConsentGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        consent = record_consent(
            request.user,
            serializer.validated_data["purpose"],
            ip_address=_client_ip(request),
            policy_version=serializer.validated_data.get("policy_version", "1.0"),
        )
        return Response(
            ConsentRecordSerializer(consent).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Workers"], summary="Withdraw a consent record")
class ConsentWithdrawView(APIView):
    """Withdrawal is per purpose.

    Withdrawing face-verification consent must not silently revoke the identity
    verification a worker's approval rests on, which is why these are separate
    rows rather than one flag.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ConsentRecordSerializer

    def post(self, request, pk):
        consent = ConsentRecord.objects.filter(pk=pk, user=request.user).first()
        if consent is None:
            return _error(
                "not_found", "Consent record not found.", status.HTTP_404_NOT_FOUND
            )

        consent.withdraw()
        return Response(
            {
                "consent": ConsentRecordSerializer(consent).data,
                "message": "Consent withdrawn.",
            }
        )


# ---------------------------------------------------------------------------
# 3.5 Admin verification & activation gate
# ---------------------------------------------------------------------------


def _reviewable_workers(user):
    """Workers in the administrator's own society."""
    if user.society_id is None:
        return WorkerProfile.objects.none()
    return (
        WorkerProfile.objects.filter(user__society_id=user.society_id)
        .select_related("user", "user__society")
        .prefetch_related("service_types", "kyc_documents")
    )


@extend_schema(tags=["Worker review"], summary="Pending worker approval queue")
class PendingWorkerListView(generics.ListAPIView):
    """Module 3.5 — what the administrator works through."""

    serializer_class = WorkerReviewSerializer
    permission_classes = [IsSocietyAdmin]
    queryset = WorkerProfile.objects.none()  # declared for schema generation
    search_fields = ["user__first_name", "user__last_name", "user__phone_number"]

    def get_queryset(self):
        return _reviewable_workers(self.request.user).filter(user__is_approved=False)


@extend_schema(tags=["Worker review"], summary="Full review payload for one worker")
class WorkerReviewDetailView(generics.RetrieveAPIView):
    serializer_class = WorkerReviewSerializer
    permission_classes = [IsSocietyAdmin]
    queryset = WorkerProfile.objects.none()  # declared for schema generation

    def get_queryset(self):
        return _reviewable_workers(self.request.user)


@extend_schema(
    tags=["Worker review"],
    summary="Approve or reject a worker",
    request=WorkerDecisionSerializer,
)
class WorkerDecisionView(APIView):
    """Module 3.5 — the gate that admits a worker to the platform.

    Only an approved worker becomes visible to Module 4's search, so this is the
    most consequential button in the administrator's app.
    """

    permission_classes = [IsSocietyAdmin]
    serializer_class = WorkerDecisionSerializer

    def post(self, request, pk):
        worker = _reviewable_workers(request.user).filter(pk=pk).first()
        if worker is None:
            return _error("not_found", "Worker not found.", status.HTTP_404_NOT_FOUND)

        serializer = WorkerDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            if serializer.validated_data["approve"]:
                worker = approve_worker(worker, reviewed_by=request.user)
                message = "Worker approved. They are now visible in search."
            else:
                worker = reject_worker(
                    worker,
                    reason=serializer.validated_data["rejection_reason"],
                    reviewed_by=request.user,
                )
                message = "Worker rejected."
        except (MinorRejected, ProfileIncomplete) as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)
        except WorkerError as exc:  # pragma: no cover — defensive
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "worker": WorkerReviewSerializer(
                    worker, context={"request": request}
                ).data,
                "message": message,
            }
        )
