"""
Module 8 — Payments & Payouts: API views.

Endpoint map (mounted at /api/v1/payments/)::

    GET  ./                              the ledger, role-scoped        (8.2)
    GET  <id>/                           one payment
    GET  <id>/receipt/                   digital receipt                (8.3)

    GET  salary-basis/                   attendance arithmetic          (8.1)
    POST engagement/                     pay a month's salary           (8.1/8.4)
    POST booking/                        pay for a one-day booking      (8.1/8.4)
    POST <id>/checkout/                  open a Razorpay order
    POST <id>/confirm/                   settle from a signed response  (8.1)

    POST webhook/                        Razorpay webhook — UNAUTHENTICATED
                                         but signature-verified         (8.1)

    GET  summary/                        monthly salary summary (JSON)  (8.3)
    GET  summary/csv/                    the same as CSV
    GET  summary/pdf/                    the same as PDF

    PUT  split/<engagement_id>/          replacement pay rule           (8.5)
    GET  disputes/                       disputes, role-scoped          (8.6)
    POST <id>/dispute/                   raise one
    POST disputes/<id>/resolve/          administrator closes one

-------------------------------------------------------------------------------
THE WEBHOOK IS THE ONLY UNAUTHENTICATED ENDPOINT IN THE PROJECT
-------------------------------------------------------------------------------
It has to be: Razorpay's servers have no session. What replaces authentication
is an HMAC over the **raw request body**, checked before anything is trusted.
It is also the one place the raw body matters — DRF's parsed data cannot be
re-serialised to the same bytes, and verifying against re-serialised JSON would
reject every genuine webhook.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import (
    IsApprovedResident,
    IsApprovedSocietyAdmin,
    IsEngagementParty,
    IsSocietyAdmin,
)
from apps.bookings.models import Booking, BookingStatus
from apps.hiring.models import Engagement
from apps.societies.services import primary_resident_or_403
from apps.workers.models import WorkerProfile

from . import fees, gateway
from .models import (
    Payment,
    PaymentDispute,
    PaymentKind,
    PaymentStatus,
    ReplacementSplit,
    SocietySubscription,
    format_paise,
    rupees_to_paise,
)
from .serializers import (
    CheckoutPayloadSerializer,
    ConfirmCheckoutSerializer,
    CreateBookingPaymentSerializer,
    CreateEngagementPaymentSerializer,
    FeeQuoteSerializer,
    MonthlySummarySerializer,
    PaymentDisputeSerializer,
    PaymentSerializer,
    RaiseDisputeSerializer,
    ReceiptSerializer,
    ReplacementSplitSerializer,
    ResolveDisputeSerializer,
    SalaryBasisSerializer,
    SocietySubscriptionSerializer,
    TipOwedSerializer,
)
from .services import (
    AlreadyPaid,
    NothingToPay,
    PaymentError,
    SignatureInvalid,
    apply_webhook,
    confirm_checkout,
    create_payment,
    open_order,
    record_webhook,
    salary_basis,
)
from .summary import build_monthly_summary, receipt_dict, render_csv, render_pdf

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _scope_to_caller(queryset, user):
    """Narrow a payment queryset to what this caller may see.

    Money is the most sensitive thing in the system, so this fails closed: an
    unrecognised role sees nothing rather than everything.
    """
    if user.is_superuser:
        return queryset
    if user.role == Role.RESIDENT:
        return queryset.filter(resident__user=user)
    if user.role == Role.WORKER:
        return queryset.filter(worker__user=user)
    if user.is_society_admin and user.society_id is not None:
        return queryset.filter(society_id=user.society_id)
    return queryset.none()


def _payment_queryset(user):
    return _scope_to_caller(
        Payment.objects.select_related(
            "worker__user", "resident__user", "resident__flat__tower",
            "booking__category", "engagement",
        ),
        user,
    )


# ---------------------------------------------------------------------------
# 8.2 Ledger
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Payments"],
    summary="Payment ledger",
    parameters=[
        OpenApiParameter("status", str, description="created | pending | paid | …"),
        OpenApiParameter("kind", str, description="Filter by payment kind"),
    ],
)
class PaymentListView(generics.ListAPIView):
    """Module 8.2 — a running, auditable history for both parties."""

    serializer_class = PaymentSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Payment.objects.none()  # declared for schema generation

    def get_queryset(self):
        queryset = _payment_queryset(self.request.user)

        for field in ("status", "kind"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset


@extend_schema(tags=["Payments"], summary="Retrieve one payment")
class PaymentDetailView(generics.RetrieveAPIView):
    serializer_class = PaymentSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Payment.objects.none()  # declared for schema generation

    def get_queryset(self):
        return _payment_queryset(self.request.user)


@extend_schema(
    tags=["Payments"], summary="Digital receipt", responses=ReceiptSerializer
)
class ReceiptView(APIView):
    """Module 8.3 — issued to both parties for every transaction."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ReceiptSerializer

    def get(self, request, pk):
        payment = _payment_queryset(request.user).filter(pk=pk).first()
        if payment is None:
            return _error("not_found", "Payment not found.", status.HTTP_404_NOT_FOUND)
        return Response(receipt_dict(payment))


# ---------------------------------------------------------------------------
# 8.1 / 8.4 Creating and settling
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Payments"],
    summary="Attendance-derived salary suggestion",
    parameters=[
        OpenApiParameter("engagement", int, required=True),
        OpenApiParameter("period_start", str, required=True, description="YYYY-MM-DD"),
        OpenApiParameter("period_end", str, required=True, description="YYYY-MM-DD"),
    ],
    responses=SalaryBasisSerializer,
)
class SalaryBasisView(APIView):
    """Module 8.1 — shows the arithmetic before the resident commits.

    A suggestion, not a verdict. The resident may pay a different amount; see
    ``services.salary_basis`` for why that is deliberate.
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = SalaryBasisSerializer

    def get(self, request):
        params = request.query_params
        try:
            engagement_id = int(params["engagement"])
            period_start = dt.date.fromisoformat(params["period_start"])
            period_end = dt.date.fromisoformat(params["period_end"])
        except (KeyError, ValueError):
            return _error(
                "validation_error",
                "Provide engagement, period_start and period_end.",
                status.HTTP_400_BAD_REQUEST,
            )

        engagement = _scope_engagement(request.user, engagement_id)
        if engagement is None:
            return _error(
                "not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND
            )

        basis = salary_basis(
            engagement, period_start=period_start, period_end=period_end
        )
        return Response(
            {
                "expected_visits": basis.expected_visits,
                "attended_visits": basis.attended_visits,
                "full_rate_paise": basis.full_rate_paise,
                "suggested_paise": basis.suggested_paise,
                "period_start": basis.period_start,
                "period_end": basis.period_end,
                "is_full": basis.is_full,
                "explanation": basis.explain(),
            }
        )


def _scope_engagement(user, engagement_id):
    queryset = Engagement.objects.select_related(
        "worker__user", "resident__user", "society"
    ).filter(pk=engagement_id)

    if user.role == Role.RESIDENT:
        return queryset.filter(resident__user=user).first()
    if user.role == Role.WORKER:
        return queryset.filter(worker__user=user).first()
    if user.is_society_admin and user.society_id is not None:
        return queryset.filter(society_id=user.society_id).first()
    return None


@extend_schema(
    tags=["Payments"],
    summary="Pay a month's salary",
    request=CreateEngagementPaymentSerializer,
    responses=PaymentSerializer,
)
class CreateEngagementPaymentView(APIView):
    """Modules 8.1 and 8.4 — salary, with an optional tip in the same charge."""

    permission_classes = [IsApprovedResident]
    serializer_class = CreateEngagementPaymentSerializer

    def post(self, request):
        resident = primary_resident_or_403(request.user)

        serializer = CreateEngagementPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        engagement = _scope_engagement(request.user, data["engagement"])
        if engagement is None:
            return _error(
                "not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND
            )

        basis = salary_basis(
            engagement,
            period_start=data["period_start"],
            period_end=data["period_end"],
        )
        amount = data.get("amount_paise") or basis.suggested_paise

        try:
            payment = create_payment(
                resident=resident,
                worker=engagement.worker,
                society=engagement.society,
                kind=PaymentKind.ENGAGEMENT_SALARY,
                amount_paise=amount,
                tip_paise=data.get("tip_paise", 0),
                engagement=engagement,
                period_start=data["period_start"],
                period_end=data["period_end"],
                note=data.get("note", ""),
            )
        except NothingToPay as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "basis": {
                    "expected_visits": basis.expected_visits,
                    "attended_visits": basis.attended_visits,
                    "suggested_paise": basis.suggested_paise,
                    "explanation": basis.explain(),
                },
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Payments"],
    summary="Pay for a one-day booking",
    request=CreateBookingPaymentSerializer,
    responses=PaymentSerializer,
)
class CreateBookingPaymentView(APIView):
    permission_classes = [IsApprovedResident]
    serializer_class = CreateBookingPaymentSerializer

    def post(self, request):
        resident = primary_resident_or_403(request.user)

        serializer = CreateBookingPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        booking = (
            Booking.objects.select_related("worker__user", "society", "category")
            .filter(pk=data["booking"], resident__user=request.user)
            .first()
        )
        if booking is None:
            return _error("not_found", "Booking not found.", status.HTTP_404_NOT_FOUND)

        # Module 8 bills for completed work, not requested work — see the
        # docstring on services.complete_booking. Nothing was previously
        # stopping a resident from paying the moment a booking was confirmed,
        # before the worker had even started.
        if booking.status != BookingStatus.COMPLETED:
            return _error(
                "not_completed",
                "This job is not marked complete yet. Payment opens once the "
                "worker has finished.",
                status.HTTP_409_CONFLICT,
            )

        # Idempotent on the booking: nothing stops the app from calling this
        # twice — a resident re-opening the booking, a retried request on a
        # poor connection — and there is no other constraint stopping two
        # ledger rows for the same job. A failed or cancelled attempt is not
        # "alive" and should not block a fresh one.
        existing = (
            Payment.objects.filter(booking=booking, kind=PaymentKind.BOOKING)
            .exclude(status__in=[PaymentStatus.FAILED, PaymentStatus.CANCELLED])
            .first()
        )
        if existing is not None:
            return Response(
                {"payment": PaymentSerializer(existing).data},
                status=status.HTTP_200_OK,
            )

        try:
            payment = create_payment(
                resident=resident,
                worker=booking.worker,
                society=booking.society,
                kind=PaymentKind.BOOKING,
                # The one crossing point from rupees into paise — see models.py.
                amount_paise=rupees_to_paise(booking.quoted_price),
                tip_paise=data.get("tip_paise", 0),
                booking=booking,
                note=data.get("note", ""),
            )
        except NothingToPay as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {"payment": PaymentSerializer(payment).data},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Payments"],
    summary="Open a Razorpay order",
    request=None,
    responses=CheckoutPayloadSerializer,
)
class CheckoutView(APIView):
    """Module 8.1 — creates the order server-side and returns the checkout payload."""

    permission_classes = [IsApprovedResident]
    serializer_class = CheckoutPayloadSerializer

    def post(self, request, pk):
        payment = _payment_queryset(request.user).filter(pk=pk).first()
        if payment is None:
            return _error("not_found", "Payment not found.", status.HTTP_404_NOT_FOUND)

        try:
            payload = open_order(payment)
        except AlreadyPaid as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)
        except gateway.LiveKeyRefused as exc:
            # A configuration mistake about to charge a real card. Loud on
            # purpose.
            logger.error("Refused a live Razorpay key in test mode")
            return _error(exc.code, str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)
        except gateway.GatewayUnavailable as exc:
            return _error(exc.code, str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)
        except gateway.GatewayError as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response({"checkout": payload, "payment": PaymentSerializer(payment).data})


@extend_schema(
    tags=["Payments"],
    summary="Confirm a signed checkout response",
    request=ConfirmCheckoutSerializer,
    responses=PaymentSerializer,
)
class ConfirmCheckoutView(APIView):
    """Module 8.1 — settles a payment from the app's signed response.

    The signature is the trust boundary. Without it this endpoint would let any
    resident mark their own payments as paid.
    """

    permission_classes = [IsApprovedResident]
    serializer_class = ConfirmCheckoutSerializer

    def post(self, request, pk):
        payment = _payment_queryset(request.user).filter(pk=pk).first()
        if payment is None:
            return _error("not_found", "Payment not found.", status.HTTP_404_NOT_FOUND)

        serializer = ConfirmCheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            settled = confirm_checkout(
                payment,
                razorpay_payment_id=serializer.validated_data["razorpay_payment_id"],
                signature=serializer.validated_data["razorpay_signature"],
            )
        except SignatureInvalid as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)
        except PaymentError as exc:  # pragma: no cover — defensive
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {"payment": PaymentSerializer(settled).data, "message": "Payment confirmed."}
        )


@extend_schema(
    tags=["Payments"],
    summary="Razorpay webhook (unauthenticated, signature-verified)",
    request=None,
    responses={200: None},
)
class RazorpayWebhookView(APIView):
    """Module 8.1.

    Verified against the raw request body — see the module docstring. Always
    answers 200 once the signature checks out, even if applying the event fails:
    Razorpay retries on any non-2xx, and a retry loop caused by a bug on our
    side would hammer the endpoint without ever succeeding. The failure is
    recorded on the stored event instead, where it can be replayed deliberately.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @csrf_exempt
    def post(self, request):
        signature = request.headers.get("X-Razorpay-Signature", "")
        event_id = request.headers.get("X-Razorpay-Event-Id", "")
        raw_body = request.body

        if not gateway.verify_webhook_signature(raw_body=raw_body, signature=signature):
            # Stored anyway when identifiable: a run of these is someone
            # probing, and an operator should be able to see it.
            if event_id:
                record_webhook(
                    event_id=event_id,
                    event_type="",
                    payload={},
                    signature_valid=False,
                )
            logger.warning("Rejected a webhook with an invalid signature")
            return _error(
                "invalid_signature", "Invalid signature.", status.HTTP_401_UNAUTHORIZED
            )

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _error(
                "validation_error", "Malformed payload.", status.HTTP_400_BAD_REQUEST
            )

        event_type = payload.get("event", "")
        # Razorpay always sends the header, but falling back keeps a
        # well-formed event from being dropped over a missing one.
        event_id = event_id or f"{event_type}:{payload.get('created_at', timezone.now().timestamp())}"

        event, created = record_webhook(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            signature_valid=True,
        )

        if not created and event.processed:
            # Razorpay retries until it gets a 2xx. Replaying a processed event
            # must be a no-op, not a second settlement.
            return Response({"status": "already processed"})

        try:
            apply_webhook(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to apply webhook %s", event.event_id)
            event.mark_processed(error=str(exc))

        return Response({"status": "ok"})


# ---------------------------------------------------------------------------
# 8.3 Summaries
# ---------------------------------------------------------------------------


def _summary_target(request):
    """Whose summary is being asked for, and may the caller see it?

    A worker reads their own. An administrator reads anyone's in their society.
    A resident reads nobody's — a household has no business holding another
    worker's income statement.
    """
    user = request.user

    if user.role == Role.WORKER:
        return WorkerProfile.objects.filter(user=user).first()

    if user.is_society_admin and user.society_id is not None:
        worker_id = request.query_params.get("worker")
        if not worker_id:
            return None
        return WorkerProfile.objects.filter(
            pk=worker_id, user__society_id=user.society_id
        ).first()

    return None


def _requested_month(request) -> tuple[int, int]:
    today = timezone.localdate()
    return (
        int(request.query_params.get("year", today.year)),
        int(request.query_params.get("month", today.month)),
    )


class _SummaryBase(APIView):
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]

    def resolve(self, request):
        worker = _summary_target(request)
        if worker is None:
            return None, _error(
                "not_found",
                "No worker to summarise. Administrators must pass ?worker=<id>.",
                status.HTTP_404_NOT_FOUND,
            )
        try:
            year, month = _requested_month(request)
            if not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            return None, _error(
                "validation_error", "Invalid month.", status.HTTP_400_BAD_REQUEST
            )

        return build_monthly_summary(worker, year=year, month=month), None


@extend_schema(
    tags=["Payments"],
    summary="Monthly salary summary",
    parameters=[
        OpenApiParameter("worker", int, description="Administrators only"),
        OpenApiParameter("year", int),
        OpenApiParameter("month", int),
    ],
    responses=MonthlySummarySerializer,
)
class MonthlySummaryView(_SummaryBase):
    serializer_class = MonthlySummarySerializer

    def get(self, request):
        summary, error = self.resolve(request)
        if error:
            return error
        return Response(summary.as_dict())


@extend_schema(tags=["Payments"], summary="Monthly salary summary as CSV", responses={200: None})
class MonthlySummaryCsvView(_SummaryBase):
    def get(self, request):
        summary, error = self.resolve(request)
        if error:
            return error

        response = HttpResponse(render_csv(summary), content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="sathify-{summary.year}-{summary.month:02d}.csv"'
        )
        return response


@extend_schema(tags=["Payments"], summary="Monthly salary summary as PDF", responses={200: None})
class MonthlySummaryPdfView(_SummaryBase):
    def get(self, request):
        summary, error = self.resolve(request)
        if error:
            return error

        response = HttpResponse(render_pdf(summary), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="sathify-{summary.year}-{summary.month:02d}.pdf"'
        )
        return response


# ---------------------------------------------------------------------------
# 8.5 Replacement split
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Payments"],
    summary="Replacement-worker pay rule",
    request=ReplacementSplitSerializer,
    responses=ReplacementSplitSerializer,
)
class ReplacementSplitView(APIView):
    """Module 8.5 — agreed once per engagement, applied automatically after.

    Both parties may read it; only the flat's primary account holder sets it,
    the same rule that governs hiring and scheduling (Module 2.4).
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ReplacementSplitSerializer

    def get(self, request, engagement_id):
        engagement = _scope_engagement(request.user, engagement_id)
        if engagement is None:
            return _error(
                "not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND
            )

        split = getattr(engagement, "replacement_split", None)
        if split is None:
            # The default, stated rather than returned as null: the replacement
            # is paid in full unless something else was agreed.
            return Response(
                {
                    "replacement_share_percent": 100,
                    "original_share_percent": 0,
                    "note": "",
                    "is_customised": False,
                }
            )

        data = ReplacementSplitSerializer(split).data
        return Response({**data, "is_customised": True})

    def put(self, request, engagement_id):
        engagement = _scope_engagement(request.user, engagement_id)
        if engagement is None:
            return _error(
                "not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND
            )

        if request.user.role == Role.WORKER:
            return _error(
                "permission_denied",
                "The resident sets the replacement pay rule.",
                status.HTTP_403_FORBIDDEN,
            )
        if request.user.role == Role.RESIDENT:
            primary_resident_or_403(request.user)

        existing = ReplacementSplit.objects.filter(engagement=engagement).first()
        serializer = ReplacementSplitSerializer(
            existing, data=request.data, partial=bool(existing)
        )
        serializer.is_valid(raise_exception=True)
        split = serializer.save(engagement=engagement, updated_by=request.user)

        return Response(
            {
                **ReplacementSplitSerializer(split).data,
                "is_customised": True,
                "message": "Replacement pay rule saved.",
            },
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# 8.6 Disputes
# ---------------------------------------------------------------------------


@extend_schema(tags=["Payments"], summary="Payment disputes")
class DisputeListView(generics.ListAPIView):
    """Module 8.6 — deliberately thin; Module 11's queue does the handling."""

    serializer_class = PaymentDisputeSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = PaymentDispute.objects.none()  # declared for schema generation

    def get_queryset(self):
        user = self.request.user
        queryset = PaymentDispute.objects.select_related("payment", "raised_by")

        if user.is_society_admin and user.society_id is not None:
            return queryset.filter(society_id=user.society_id)
        # A party sees disputes on payments they are part of, including ones
        # raised against them — being disputed without being told would be
        # worse than useless.
        if user.role == Role.RESIDENT:
            return queryset.filter(payment__resident__user=user)
        if user.role == Role.WORKER:
            return queryset.filter(payment__worker__user=user)
        return queryset.none()


def _open_complaint_for(dispute):
    """Mirror a payment dispute into Module 11's complaint queue.

    Lazily imported and non-raising, like every other cross-module call site: a
    dispute that was recorded must not be rolled back because the complaint
    queue was unavailable. Returns ``None`` if it could not be mirrored, which
    the caller reports as an empty reference rather than an error.
    """
    try:
        from apps.administration.services import raise_from_payment_dispute

        return raise_from_payment_dispute(dispute)
    except Exception:  # noqa: BLE001 — the dispute itself already succeeded
        logger.exception("Could not open a complaint for dispute %s", dispute.pk)
        return None


@extend_schema(
    tags=["Payments"],
    summary="Raise a dispute on a payment",
    request=RaiseDisputeSerializer,
    responses=PaymentDisputeSerializer,
)
class RaiseDisputeView(APIView):
    permission_classes = [IsEngagementParty]
    serializer_class = RaiseDisputeSerializer

    def post(self, request, pk):
        payment = _payment_queryset(request.user).filter(pk=pk).first()
        if payment is None:
            return _error("not_found", "Payment not found.", status.HTTP_404_NOT_FOUND)

        serializer = RaiseDisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        existing = PaymentDispute.objects.filter(
            payment=payment, raised_by=request.user
        ).first()
        if existing is not None and existing.is_open:
            return _error(
                "already_disputed",
                "You already have an open dispute on this payment.",
                status.HTTP_409_CONFLICT,
            )

        dispute = PaymentDispute.objects.create(
            society=payment.society,
            payment=payment,
            raised_by=request.user,
            reason=serializer.validated_data["reason"],
            description=serializer.validated_data["description"],
        )
        logger.info(
            "Dispute raised on payment %s by user %s", payment.pk, request.user.pk
        )

        # Module 8.6 kept this record deliberately thin and routed the handling
        # into Module 11's complaint queue rather than building a second
        # workflow. This is that join: the administrator works one queue, and
        # the dispute inherits an SLA it would otherwise never have had.
        complaint = _open_complaint_for(dispute)

        return Response(
            {
                "dispute": PaymentDisputeSerializer(dispute).data,
                "complaint_reference": getattr(complaint, "reference", ""),
                "message": "Your society administrator will look into this.",
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Payments"],
    summary="Resolve a dispute",
    request=ResolveDisputeSerializer,
    responses=PaymentDisputeSerializer,
)
class ResolveDisputeView(APIView):
    """The administrator closes a dispute. Module 11 will own the wider queue."""

    permission_classes = [IsSocietyAdmin]
    serializer_class = ResolveDisputeSerializer

    def post(self, request, pk):
        dispute = PaymentDispute.objects.filter(
            pk=pk, society_id=request.user.society_id
        ).first()
        if dispute is None:
            return _error("not_found", "Dispute not found.", status.HTTP_404_NOT_FOUND)

        serializer = ResolveDisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        changed = dispute.resolve(
            resolution=serializer.validated_data["resolution"],
            by=request.user,
            upheld=serializer.validated_data["upheld"],
        )
        if not changed:
            return _error(
                "already_resolved",
                "This dispute has already been closed.",
                status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "dispute": PaymentDisputeSerializer(dispute).data,
                "message": "Dispute closed.",
            }
        )


# ---------------------------------------------------------------------------
# 8.7 Platform fees, subscription, and the tip settlement list
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Payments"],
    summary="What a booking will cost, fee included",
    parameters=[
        OpenApiParameter("amount_paise", int, description="The worker's quoted price"),
    ],
)
class FeeQuoteView(APIView):
    """Module 8.7 - the figure shown *before* the resident confirms.

    Exists so the confirmation screen renders a number the server calculated,
    rather than reconstructing it from a rate the client would have to know and
    keep in step. A fee discovered on the receipt is worse than a larger fee
    disclosed up front.

    Returns zeroes while fees are switched off, which is the current state - the
    screen simply has nothing to show, and will have when the rate turns on.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = FeeQuoteSerializer

    def get(self, request):
        try:
            amount = int(request.query_params.get("amount_paise", 0))
        except (TypeError, ValueError):
            return _error(
                "validation_error",
                "amount_paise must be a whole number of paise.",
                status.HTTP_400_BAD_REQUEST,
            )

        if amount < 0:
            return _error(
                "validation_error",
                "amount_paise cannot be negative.",
                status.HTTP_400_BAD_REQUEST,
            )

        society = getattr(request.user, "society", None)
        return Response(
            fees.quote(kind=PaymentKind.BOOKING, amount_paise=amount, society=society)
        )


@extend_schema(
    tags=["Payments"],
    summary="This society's subscription",
    responses=SocietySubscriptionSerializer,
)
class SocietySubscriptionView(APIView):
    """Module 8.7 - what the society is entitled to.

    Read-only over the API. Tiers are sold and set by hand for now, from the
    Django admin: there is deliberately no self-serve checkout until somebody
    has actually paid for one.
    """

    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = SocietySubscriptionSerializer

    def get(self, request):
        if request.user.society_id is None:
            return _error(
                "no_society",
                "This account is not attached to a society.",
                status.HTTP_400_BAD_REQUEST,
            )

        subscription = SocietySubscription.for_society(request.user.society)
        return Response(SocietySubscriptionSerializer(subscription).data)


@extend_schema(
    tags=["Payments"],
    summary="Tips owed to workers, for hand settlement",
    responses=TipOwedSerializer(many=True),
)
class TipsOwedView(APIView):
    """Module 8.7 - the interim tipping mechanism, and it is deliberately manual.

    Routing a tip to a worker's own account needs Razorpay Route, which needs a
    linked account with a bank account **and a PAN** per worker. Much of this
    workforce has neither. Building the automated path first would mean tipping
    only worked for the workers who least need it.

    So: the resident's tip is collected with the payment as it already is, and
    this endpoint gives the administrator the list to hand over in cash, with
    receipt numbers so it can be reconciled afterwards. The ledger is identical
    to what the automated path will read; only settlement changes.

    Next step, documented rather than half-built: RazorpayX payouts to a UPI ID,
    which needs no PAN and clears most of this population. See docs/monetisation.md.
    """

    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = TipOwedSerializer

    def get(self, request):
        if request.user.society_id is None:
            return _error(
                "no_society",
                "This account is not attached to a society.",
                status.HTTP_400_BAD_REQUEST,
            )

        # Paid by the resident and therefore genuinely owed onward. An unpaid or
        # failed payment owes the worker nothing, and listing it would have an
        # administrator handing over money that never arrived.
        rows = (
            Payment.objects.filter(
                society_id=request.user.society_id,
                status=PaymentStatus.PAID,
                tip_paise__gt=0,
            )
            .select_related("worker__user")
            .order_by("worker_id", "-paid_at")
        )

        owed: dict[int, dict] = {}
        for payment in rows:
            entry = owed.setdefault(
                payment.worker_id,
                {
                    "worker_id": payment.worker_id,
                    "worker_name": payment.worker.user.get_full_name(),
                    "worker_phone": payment.worker.user.phone_number,
                    "tip_paise": 0,
                    "payment_count": 0,
                    "receipts": [],
                },
            )
            entry["tip_paise"] += payment.tip_paise
            entry["payment_count"] += 1
            entry["receipts"].append(payment.receipt_number)

        results = sorted(owed.values(), key=lambda row: -row["tip_paise"])
        for row in results:
            row["tip_display"] = format_paise(row["tip_paise"])

        return Response(
            {
                "count": len(results),
                "total_paise": sum(row["tip_paise"] for row in results),
                "total_display": format_paise(
                    sum(row["tip_paise"] for row in results)
                ),
                "settlement": "cash",
                "results": results,
            }
        )
