"""
Module 10 — Notifications: API views.

Endpoint map (mounted at /api/v1/notifications/)::

    GET  ./                     the notification centre                  (10.3)
    GET  unread-count/          badge count
    POST <id>/read/             mark one read
    POST read-all/              mark everything read

    POST device/                register this device for push            (10.1)
    DELETE device/              stop pushing to it

    GET  preferences/           per-category mute settings               (10.4)
    PUT  preferences/           mute or unmute one category

    POST deliver-due/           drain Module 6.4's reminder queue

-------------------------------------------------------------------------------
EVERYONE SEES ONLY THEIR OWN
-------------------------------------------------------------------------------
There is deliberately no administrator view of another user's notifications.
The centre holds gate decisions, payment amounts and account rejections, and an
administrator who needs one of those has a purpose-built screen for it in the
module that owns the data.
"""

from __future__ import annotations

import logging

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import DeviceSession
from apps.accounts.permissions import IsSocietyAdmin

from .models import (
    SAFETY_CRITICAL_CATEGORIES,
    Notification,
    NotificationCategory,
    NotificationPreference,
)
from .serializers import (
    DeviceTokenSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    SetPreferenceSerializer,
    UnreadCountSerializer,
)
from .services import deliver_due_reminders, retry_failed_deliveries

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


# ---------------------------------------------------------------------------
# 10.3 Notification centre
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Notifications"],
    summary="Your notifications",
    parameters=[
        OpenApiParameter("unread", bool, description="Unread only"),
        OpenApiParameter("category", str, description="Filter by category"),
    ],
)
class NotificationListView(generics.ListAPIView):
    """Module 10.3 — the persistent log, so nothing is lost to a missed push."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    queryset = Notification.objects.none()  # declared for schema generation

    def get_queryset(self):
        queryset = Notification.objects.filter(recipient=self.request.user)

        if self.request.query_params.get("unread") in {"true", "1"}:
            queryset = queryset.unread()
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)

        return queryset


@extend_schema(
    tags=["Notifications"], summary="Unread count", responses=UnreadCountSerializer
)
class UnreadCountView(APIView):
    """Drives the badge. Deliberately its own endpoint — the home screens poll
    this and should not pull a page of bodies to count them."""

    permission_classes = [IsAuthenticated]
    serializer_class = UnreadCountSerializer

    def get(self, request):
        return Response(
            {
                "unread": Notification.objects.filter(recipient=request.user)
                .unread()
                .count()
            }
        )


@extend_schema(
    tags=["Notifications"],
    summary="Mark one notification read",
    request=None,
    responses=NotificationSerializer,
)
class MarkReadView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def post(self, request, pk):
        notification = Notification.objects.filter(
            pk=pk, recipient=request.user
        ).first()
        if notification is None:
            return _error(
                "not_found", "Notification not found.", status.HTTP_404_NOT_FOUND
            )

        notification.mark_read()
        return Response(NotificationSerializer(notification).data)


@extend_schema(
    tags=["Notifications"], summary="Mark everything read", request=None
)
class MarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UnreadCountSerializer

    def post(self, request):
        updated = (
            Notification.objects.filter(recipient=request.user)
            .unread()
            .update(read_at=timezone.now())
        )
        return Response({"marked_read": updated, "unread": 0})


# ---------------------------------------------------------------------------
# 10.1 Device registration
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Notifications"],
    summary="Register or remove this device for push",
    request=DeviceTokenSerializer,
)
class DeviceTokenView(APIView):
    """Module 10.1.

    Keyed on ``device_id`` so re-registering — which Firebase does whenever it
    rotates a token — replaces the row rather than adding one per app launch.
    Stale rows would mean the same push delivered several times to one phone.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = DeviceTokenSerializer

    def post(self, request):
        serializer = DeviceTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        session, created = DeviceSession.objects.update_or_create(
            user=request.user,
            device_id=data["device_id"],
            defaults={
                "fcm_token": data["fcm_token"],
                "device_name": data.get("device_name", ""),
                "platform": data.get("platform", ""),
                "last_seen_at": timezone.now(),
                # Re-registering revives a device the user signed back in on.
                "revoked_at": None,
                "revoked_reason": "",
            },
        )

        return Response(
            {"registered": True, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        """Stop pushing to this device — on sign-out, or if the user opts out."""
        device_id = request.query_params.get("device_id") or request.data.get("device_id")
        if not device_id:
            return _error(
                "validation_error",
                "Which device? Pass device_id.",
                status.HTTP_400_BAD_REQUEST,
            )

        cleared = DeviceSession.objects.filter(
            user=request.user, device_id=device_id
        ).update(fcm_token="")

        return Response({"cleared": bool(cleared)})


# ---------------------------------------------------------------------------
# 10.4 Preferences
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Notifications"],
    summary="Notification preferences",
    request=SetPreferenceSerializer,
    responses=NotificationPreferenceSerializer(many=True),
)
class PreferencesView(APIView):
    """Module 10.4 — per-category opt-out.

    Every category is returned, muted or not, with ``can_mute`` telling the app
    which controls to lock. Returning only the mutes would leave the client
    guessing at the full list and inventing its own labels.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationPreferenceSerializer

    def get(self, request):
        muted = set(
            NotificationPreference.objects.filter(
                user=request.user, muted=True
            ).values_list("category", flat=True)
        )

        return Response(
            [
                {
                    "category": value,
                    "label": label,
                    "muted": value in muted,
                    "can_mute": value not in SAFETY_CRITICAL_CATEGORIES,
                }
                for value, label in NotificationCategory.choices
            ]
        )

    def put(self, request):
        serializer = SetPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        honoured = NotificationPreference.set_muted(
            request.user, data["category"], muted=data["muted"]
        )
        if not honoured:
            # Belt and braces: the serializer already refuses this, but the
            # model is the authority and should not be able to be bypassed.
            return _error(
                "cannot_mute",
                "This kind of alert cannot be switched off.",
                status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "category": data["category"],
                "muted": data["muted"],
                "message": "Preference saved.",
            }
        )


# ---------------------------------------------------------------------------
# Draining Module 6.4's queue
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Notifications"],
    summary="Deliver due reminders and retry failed sends",
    request=None,
)
class DeliverDueView(APIView):
    """The join between Module 6.4's reminder rows and this module.

    Exposed as an endpoint as well as a management command because there is no
    scheduler on the free tier: an external uptime pinger — the same one that
    keeps the Render instance awake (docs/free-tier-constraints.md §2) — can
    authenticate as an administrator and call this on a timer.

    Restricted to administrators. It is cheap, but it is a write, and an
    unauthenticated trigger would be a free way to make the server work.
    """

    permission_classes = [IsSocietyAdmin]
    serializer_class = UnreadCountSerializer

    def post(self, request):
        delivered = deliver_due_reminders(society_id=request.user.society_id)
        retried = retry_failed_deliveries()

        return Response(
            {
                "reminders_delivered": delivered,
                "deliveries_retried": retried,
                "message": f"{delivered} reminder(s) delivered.",
            }
        )
