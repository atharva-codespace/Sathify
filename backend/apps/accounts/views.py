"""
Module 1 — Identity & Access Management: API views.

Endpoint map (mounted at /api/v1/auth/):

    POST   register/resident/   self-signup, lands unapproved
    POST   register/worker/     self-signup, lands unapproved
    POST   register/admin/      self-signup, no society until Module 2.1
    POST   staff/               administrator creates a guard/admin (pre-approved)
    POST   login/               phone + password -> access + refresh + profile
    POST   refresh/             rotate the access token
    POST   logout/              blacklist the refresh token, revoke the session
    GET    me/                  caller's own profile
    PATCH  me/                  update own editable fields
    POST   password/change/
    POST   otp/request/
    POST   otp/verify/
    GET    sessions/            devices this user is signed in on
    DELETE sessions/<id>/       revoke a device (own, or admin revoking in-society)
"""

import logging

from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import DeviceSession, User
from .permissions import IsSocietyAdmin
from .serializers import (
    DeviceInfoSerializer,
    DeviceSessionSerializer,
    LogoutSerializer,
    MessageResponseSerializer,
    OtpRequestSerializer,
    OtpVerifyResponseSerializer,
    OtpVerifySerializer,
    PasswordChangeSerializer,
    ResidentRegistrationSerializer,
    SathifyTokenObtainPairSerializer,
    SocietyAdminRegistrationSerializer,
    StaffCreationSerializer,
    UserSerializer,
    WorkerRegistrationSerializer,
)
from .services import OtpThrottled, register_device_session, request_otp, verify_otp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1.1 Registration
# ---------------------------------------------------------------------------


class _BaseRegistrationView(generics.CreateAPIView):
    """Shared response shape for every self-registration flow."""

    permission_classes = [AllowAny]
    pending_message = "Registration received. An administrator will review your account."

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        logger.info("Registered %s user %s", user.role, user.phone_number)
        return Response(
            {
                "user": UserSerializer(user).data,
                "message": self.pending_message,
                # The client shows a "pending approval" screen rather than a
                # dashboard the user cannot yet act in.
                "requires_approval": True,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Auth"], summary="Register as a resident")
class ResidentRegistrationView(_BaseRegistrationView):
    serializer_class = ResidentRegistrationSerializer


@extend_schema(tags=["Auth"], summary="Register as a domestic worker")
class WorkerRegistrationView(_BaseRegistrationView):
    serializer_class = WorkerRegistrationSerializer
    pending_message = (
        "Registration received. Next, upload your Aadhaar card and photo to "
        "complete verification."
    )


@extend_schema(tags=["Auth"], summary="Register as a society administrator")
class SocietyAdminRegistrationView(_BaseRegistrationView):
    serializer_class = SocietyAdminRegistrationSerializer
    pending_message = "Account created. Next, register your society for verification."


@extend_schema(
    tags=["Auth"],
    summary="Create a guard or administrator account",
    description="Society administrators only. Staff accounts are created "
    "pre-approved and scoped to the creator's society.",
)
class StaffCreationView(generics.CreateAPIView):
    """Module 1.1 — guards and admins are created, never self-registered."""

    serializer_class = StaffCreationSerializer
    permission_classes = [IsSocietyAdmin]

    def create(self, request, *args, **kwargs):
        # An administrator with no society cannot yet create staff for one.
        if request.user.society_id is None:
            return Response(
                {
                    "error": {
                        "code": "no_society",
                        "message": "Register and get your society approved before adding staff.",
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        logger.info(
            "Admin %s created %s account %s", request.user.pk, user.role, user.phone_number
        )
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# 1.2 JWT token management
# ---------------------------------------------------------------------------


@extend_schema(tags=["Auth"], summary="Sign in with phone number and password")
class SathifyTokenObtainPairView(TokenObtainPairView):
    """Issues a token pair and opens a device session.

    Unapproved users CAN sign in. They receive a token whose ``is_approved``
    claim is false, so the app can show them their pending status instead of a
    dead end. Authorisation to act is enforced separately by ``IsApproved``.
    """

    serializer_class = SathifyTokenObtainPairSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == status.HTTP_200_OK:
            device = DeviceInfoSerializer(data=request.data.get("device", {}))
            device.is_valid(raise_exception=False)

            user = User.objects.get(pk=response.data["user"]["id"])
            refresh = RefreshToken(response.data["refresh"])
            register_device_session(
                user=user,
                refresh_token=refresh,
                request=request,
                device=device.validated_data,
            )
            logger.info("Login: %s (%s)", user.phone_number, user.role)

        return response


@extend_schema(
    tags=["Auth"],
    summary="Sign out",
    request=LogoutSerializer,
    responses={205: OpenApiResponse(description="Token blacklisted, session revoked")},
)
class LogoutView(APIView):
    """Blacklists the supplied refresh token and revokes its device session.

    Without blacklisting, a "logged out" refresh token would keep working until
    it expired — up to 30 days on a device the user believes they signed out of.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {
                    "error": {
                        "code": "validation_error",
                        "message": "A refresh token is required to sign out.",
                        "details": {"refresh": ["This field is required."]},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            jti = token.get("jti", "")
            token.blacklist()
        except TokenError:
            # Already expired or blacklisted: the desired end state either way,
            # so report success rather than confusing the user with an error.
            jti = ""

        session_qs = DeviceSession.objects.filter(user=request.user, revoked_at__isnull=True)
        if jti:
            session_qs = session_qs.filter(refresh_token_jti=jti)
        for session in session_qs:
            session.revoke(reason="User signed out")

        logger.info("Logout: %s", request.user.phone_number)
        return Response(status=status.HTTP_205_RESET_CONTENT)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@extend_schema(tags=["Auth"], summary="Get or update the signed-in user's profile")
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user


@extend_schema(
    tags=["Auth"],
    summary="Change password",
    request=PasswordChangeSerializer,
    responses={200: MessageResponseSerializer},
)
class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PasswordChangeSerializer

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password", "updated_at"])

        # A password change should end every other session: if the change was
        # prompted by a suspected compromise, leaving them alive defeats it.
        for session in DeviceSession.objects.filter(
            user=request.user, revoked_at__isnull=True
        ):
            session.revoke(reason="Password changed")

        return Response({"message": "Password updated. Please sign in again."})


# ---------------------------------------------------------------------------
# 1.4 OTP & phone verification
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Auth"],
    summary="Request an OTP",
    description="Rate limited: one code per 60 seconds, five per hour per number.",
    request=OtpRequestSerializer,
    responses={200: MessageResponseSerializer},
)
class OtpRequestView(APIView):
    permission_classes = [AllowAny]
    serializer_class = OtpRequestSerializer

    def post(self, request):
        serializer = OtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone_number"]
        purpose = serializer.validated_data["purpose"]

        try:
            request_otp(phone, purpose)
        except OtpThrottled as exc:
            return Response(
                {
                    "error": {
                        "code": "throttled",
                        "message": str(exc),
                        "details": {"retry_after_seconds": exc.retry_after_seconds},
                    }
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Never reveal whether the number is registered — that would turn this
        # endpoint into a user-enumeration oracle.
        return Response(
            {"message": "If that number is valid, a verification code has been sent."}
        )


@extend_schema(
    tags=["Auth"],
    summary="Verify an OTP",
    request=OtpVerifySerializer,
    responses={200: OtpVerifyResponseSerializer},
)
class OtpVerifyView(APIView):
    permission_classes = [AllowAny]
    serializer_class = OtpVerifySerializer

    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone_number"]

        if not verify_otp(
            phone,
            serializer.validated_data["purpose"],
            serializer.validated_data["code"],
        ):
            return Response(
                {
                    "error": {
                        "code": "invalid_otp",
                        "message": "That code is incorrect or has expired.",
                        "details": {},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = User.objects.filter(phone_number=phone, is_phone_verified=False).update(
            is_phone_verified=True, updated_at=timezone.now()
        )
        if updated:
            logger.info("Phone verified for %s", phone)

        return Response({"verified": True, "message": "Phone number verified."})


# ---------------------------------------------------------------------------
# 1.5 Session & device management
# ---------------------------------------------------------------------------


@extend_schema(tags=["Auth"], summary="List this user's device sessions")
class DeviceSessionListView(generics.ListAPIView):
    serializer_class = DeviceSessionSerializer
    permission_classes = [IsAuthenticated]
    # Declared so drf-spectacular can infer the model without evaluating
    # get_queryset() against the anonymous user it uses to introspect views.
    queryset = DeviceSession.objects.none()

    def get_queryset(self):
        return DeviceSession.objects.filter(user=self.request.user)


@extend_schema(
    tags=["Auth"],
    summary="Revoke a device session",
    description="Users may revoke their own devices. Society administrators may "
    "revoke any device belonging to a user in their society — this is the "
    "lost-or-stolen-phone path, which must not require the user to sign in first.",
    request=None,
    responses={
        204: OpenApiResponse(description="Session revoked and its token blacklisted"),
        403: OpenApiResponse(description="Not your session, or a different society"),
        404: OpenApiResponse(description="No such session"),
    },
)
class DeviceSessionRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        session = DeviceSession.objects.filter(pk=pk).select_related("user").first()
        if session is None:
            return Response(
                {"error": {"code": "not_found", "message": "Session not found.", "details": {}}},
                status=status.HTTP_404_NOT_FOUND,
            )

        user = request.user
        is_own = session.user_id == user.id
        # Society-scoped: an admin at one society cannot revoke sessions at another.
        is_admin_in_scope = (
            user.is_society_admin
            and user.society_id is not None
            and session.user.society_id == user.society_id
        )

        if not (is_own or is_admin_in_scope or user.is_superuser):
            return Response(
                {
                    "error": {
                        "code": "permission_denied",
                        "message": "You cannot revoke this session.",
                        "details": {},
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        session.revoke(
            reason="Revoked by user" if is_own else f"Revoked by administrator {user.pk}"
        )
        logger.info("Session %s revoked by %s", pk, user.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
