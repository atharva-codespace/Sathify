"""
Module 2 — Society & Resident Onboarding: API views.

Endpoint map (mounted at /api/v1/societies/):

    GET    public/                    active societies (UNAUTHENTICATED — registration picker)
    POST   register/                  admin registers a society           (2.1)
    GET    me/                        caller's own society
    PATCH  me/config/                 society configuration               (2.5)

    GET    towers/                    towers in own society               (2.2)
    POST   towers/                    create a tower
    POST   towers/bulk-flats/         generate a tower's flats
    GET    flats/                     flats in own society (?tower=, ?vacant=)
    POST   flats/                     create a single flat

    GET    gates/                     gates in own society                (2.5)
    POST   gates/

    POST   residents/                 claim a flat                        (2.3)
    GET    residents/me/              own resident profile
    GET    residents/all/             directory (admin)
    GET    residents/pending/         approval queue (admin)              (2.3)
    POST   residents/<id>/decide/     approve or reject (admin)
    POST   residents/set-primary/     reassign primary holder (admin)     (2.4)

Access note: several endpoints here are reachable by an AUTHENTICATED BUT
UNAPPROVED user. That is deliberate — a resident must be able to pick their flat
and submit proof of residence before an administrator has anything to approve.
Requiring IsApproved on those would deadlock onboarding.
"""

import logging

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import IsSocietyAdmin

from .models import Flat, Gate, Resident, Society, SocietyStatus, Tower
from .serializers import (
    BulkFlatCreateSerializer,
    FlatSerializer,
    GateSerializer,
    PublicSocietySerializer,
    ResidentApprovalSerializer,
    ResidentProfileCreateSerializer,
    ResidentSerializer,
    SetPrimaryResidentSerializer,
    SocietyConfigurationSerializer,
    SocietyRegistrationSerializer,
    SocietySerializer,
    TowerSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 2.1 Society registration & lookup
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Societies"],
    summary="List active societies (public)",
    description="Unauthenticated. Powers the society picker during registration, "
    "so it exposes only enough to identify a society.",
)
class PublicSocietyListView(generics.ListAPIView):
    """The one endpoint in this module open to anonymous callers.

    A prospective resident or worker must choose their society *before* they
    have an account, so this cannot require authentication. It is kept
    deliberately narrow for that reason.
    """

    serializer_class = PublicSocietySerializer
    permission_classes = [AllowAny]
    queryset = Society.objects.filter(status=SocietyStatus.ACTIVE)
    search_fields = ["name", "city", "pincode"]
    filterset_fields = ["city", "pincode"]


@extend_schema(tags=["Societies"], summary="Register a society")
class SocietyRegistrationView(generics.CreateAPIView):
    """Module 2.1. The registering administrator is bound to the new society."""

    serializer_class = SocietyRegistrationSerializer
    permission_classes = [IsSocietyAdmin]

    def create(self, request, *args, **kwargs):
        if request.user.society_id is not None:
            return Response(
                {
                    "error": {
                        "code": "already_registered",
                        "message": "You are already attached to a society.",
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        society = serializer.save()

        logger.info("Society %s registered by admin %s", society.pk, request.user.pk)
        return Response(
            {
                "society": SocietySerializer(society).data,
                "message": "Society registered. It will be reviewed before activation.",
                "requires_verification": True,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Societies"], summary="Get the caller's own society")
class MySocietyView(generics.RetrieveAPIView):
    serializer_class = SocietySerializer
    permission_classes = [IsAuthenticated]
    queryset = Society.objects.none()  # declared for schema generation

    def get_object(self):
        if self.request.user.society_id is None:
            raise NotFound("You are not attached to a society yet.")
        return Society.objects.get(pk=self.request.user.society_id)


@extend_schema(tags=["Societies"], summary="Update society configuration")
class SocietyConfigurationView(generics.UpdateAPIView):
    """Module 2.5. Administrators only, and only their own society."""

    serializer_class = SocietyConfigurationSerializer
    permission_classes = [IsSocietyAdmin]
    http_method_names = ["patch", "put", "head", "options"]
    queryset = Society.objects.none()

    def get_object(self):
        if self.request.user.society_id is None:
            raise NotFound("You are not attached to a society yet.")
        return Society.objects.get(pk=self.request.user.society_id)


# ---------------------------------------------------------------------------
# 2.2 Tower & flat mapping
# ---------------------------------------------------------------------------


class _SocietyScopedMixin:
    """Restricts a queryset to the caller's society via ``society_filter``.

    Module 2's models reach their society through different relations
    (``society``, ``tower__society``), so the lookup is declared per view.
    """

    society_filter = "society"

    def get_queryset(self):
        user = self.request.user
        if user.society_id is None:
            return self.queryset.none()
        return self.queryset.filter(**{self.society_filter: user.society_id})


@extend_schema(tags=["Societies"], summary="List or create towers")
class TowerListCreateView(_SocietyScopedMixin, generics.ListCreateAPIView):
    serializer_class = TowerSerializer
    queryset = Tower.objects.select_related("society")

    def get_permissions(self):
        # Any member may read the tower list — a resident needs it to pick a
        # flat — but only an administrator may change the mapping.
        if self.request.method == "POST":
            return [IsSocietyAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(society_id=self.request.user.society_id)


@extend_schema(
    tags=["Societies"],
    summary="List or create flats",
    parameters=[
        OpenApiParameter("tower", int, description="Filter by tower id"),
        OpenApiParameter("vacant", bool, description="Only flats with no residents"),
    ],
)
class FlatListCreateView(_SocietyScopedMixin, generics.ListCreateAPIView):
    serializer_class = FlatSerializer
    queryset = Flat.objects.select_related("tower")
    society_filter = "tower__society"

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSocietyAdmin()]
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = super().get_queryset()

        tower = self.request.query_params.get("tower")
        if tower:
            queryset = queryset.filter(tower_id=tower)

        if self.request.query_params.get("vacant") in {"true", "1"}:
            queryset = queryset.filter(residents__isnull=True)

        return queryset

    def perform_create(self, serializer):
        tower = serializer.validated_data["tower"]
        if tower.society_id != self.request.user.society_id:
            raise ValidationError({"tower": "That tower belongs to another society."})
        serializer.save()


@extend_schema(
    tags=["Societies"],
    summary="Bulk-generate flats for a tower",
    request=BulkFlatCreateSerializer,
)
class BulkFlatCreateView(APIView):
    """Module 2.2 — mapping a tower by hand is the most tedious onboarding step."""

    permission_classes = [IsSocietyAdmin]
    serializer_class = BulkFlatCreateSerializer

    def post(self, request):
        serializer = BulkFlatCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(result, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# 2.5 Gates
# ---------------------------------------------------------------------------


@extend_schema(tags=["Societies"], summary="List or create gates")
class GateListCreateView(_SocietyScopedMixin, generics.ListCreateAPIView):
    serializer_class = GateSerializer
    queryset = Gate.objects.select_related("society")

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSocietyAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(society_id=self.request.user.society_id)


# ---------------------------------------------------------------------------
# 2.3 / 2.4 Residents
# ---------------------------------------------------------------------------


@extend_schema(tags=["Residents"], summary="Claim a flat (create resident profile)")
class ResidentProfileCreateView(generics.CreateAPIView):
    """Module 2.3.

    Reachable while unapproved by design: submitting proof of residence is what
    an administrator reviews in order to approve.
    """

    serializer_class = ResidentProfileCreateSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        if request.user.role != Role.RESIDENT:
            return Response(
                {
                    "error": {
                        "code": "permission_denied",
                        "message": "Only residents can claim a flat.",
                        "details": {},
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resident = serializer.save()

        logger.info(
            "Resident profile created for user %s at flat %s", request.user.pk, resident.flat_id
        )
        return Response(
            {
                "resident": ResidentSerializer(resident).data,
                "message": "Flat claimed. Your administrator will review your registration.",
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Residents"], summary="Get the caller's own resident profile")
class MyResidentProfileView(generics.RetrieveAPIView):
    serializer_class = ResidentSerializer
    permission_classes = [IsAuthenticated]
    queryset = Resident.objects.none()

    def get_object(self):
        resident = (
            Resident.objects.filter(user=self.request.user)
            .select_related("flat__tower", "user")
            .first()
        )
        if resident is None:
            raise NotFound("You have not claimed a flat yet.")
        return resident


@extend_schema(tags=["Residents"], summary="Resident directory (administrators)")
class ResidentListView(generics.ListAPIView):
    serializer_class = ResidentSerializer
    permission_classes = [IsSocietyAdmin]
    queryset = Resident.objects.select_related("user", "flat__tower")
    search_fields = [
        "user__first_name",
        "user__last_name",
        "user__phone_number",
        "flat__number",
    ]

    def get_queryset(self):
        user = self.request.user
        if user.society_id is None:
            return self.queryset.none()
        return self.queryset.filter(flat__tower__society_id=user.society_id)


@extend_schema(tags=["Residents"], summary="Pending resident approval queue")
class PendingResidentListView(ResidentListView):
    """Module 2.3 — what the administrator actually works through."""

    def get_queryset(self):
        return super().get_queryset().filter(user__is_approved=False)


@extend_schema(
    tags=["Residents"],
    summary="Approve or reject a resident",
    request=ResidentApprovalSerializer,
)
class ResidentDecisionView(APIView):
    """Module 2.3 — the approval gate that grants platform access."""

    permission_classes = [IsSocietyAdmin]
    serializer_class = ResidentApprovalSerializer

    @transaction.atomic
    def post(self, request, pk):
        resident = (
            Resident.objects.select_related("user", "flat__tower").filter(pk=pk).first()
        )
        if resident is None:
            return Response(
                {"error": {"code": "not_found", "message": "Resident not found.", "details": {}}},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Society isolation: an administrator decides only for their own society.
        if resident.flat.tower.society_id != request.user.society_id:
            return Response(
                {
                    "error": {
                        "code": "permission_denied",
                        "message": "That resident belongs to another society.",
                        "details": {},
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ResidentApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        resident.reviewed_at = timezone.now()
        resident.reviewed_by = request.user

        if serializer.validated_data["approve"]:
            resident.rejection_reason = ""
            resident.user.approve(approved_by=request.user)
            message = "Resident approved."
        else:
            resident.rejection_reason = serializer.validated_data.get("rejection_reason", "")
            # Approval is revoked rather than the record deleted: the resident
            # keeps their account and can correct and resubmit.
            resident.user.is_approved = False
            resident.user.save(update_fields=["is_approved", "updated_at"])
            message = "Resident rejected."

        resident.save(
            update_fields=["reviewed_at", "reviewed_by", "rejection_reason", "updated_at"]
        )
        logger.info("Resident %s decided by admin %s: %s", pk, request.user.pk, message)

        return Response({"resident": ResidentSerializer(resident).data, "message": message})


@extend_schema(
    tags=["Residents"],
    summary="Reassign the primary account holder of a flat",
    request=SetPrimaryResidentSerializer,
)
class SetPrimaryResidentView(APIView):
    """Module 2.4 — exactly one primary account holder per flat."""

    permission_classes = [IsSocietyAdmin]
    serializer_class = SetPrimaryResidentSerializer

    @transaction.atomic
    def post(self, request):
        serializer = SetPrimaryResidentSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        resident = serializer.context["resident"]

        # Clear the existing primary first: the unique constraint permits only
        # one per flat, so setting the new one first would violate it.
        Resident.objects.filter(flat=resident.flat, is_primary=True).update(is_primary=False)
        resident.is_primary = True
        resident.save(update_fields=["is_primary", "updated_at"])

        logger.info("Primary resident for flat %s set to %s", resident.flat_id, resident.pk)
        return Response(
            {
                "resident": ResidentSerializer(resident).data,
                "message": "Primary account holder updated.",
            }
        )
