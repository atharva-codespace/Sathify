"""
Module 1 — Identity & Access Management: API views.

Sign-in is phone number plus password. The OTP has one narrow job — proving a
phone number is real — and it appears in exactly two places: once at
registration, and again when somebody has forgotten their password. It is never
a way to sign in, because a code texted on demand beside a password would be a
second and weaker credential path into the same account.

Endpoint map (mounted at /api/v1/auth/):

    POST   register/resident/   self-signup, lands unapproved, sends an OTP
    POST   register/worker/     self-signup, lands unapproved, sends an OTP
    POST   register/admin/      self-signup, no society until Module 2.1
    POST   staff/               administrator creates a guard/admin (pre-approved)
    POST   login/               phone + password -> access + refresh + profile
    POST   otp/request/         send a code (registration | password_reset)
    POST   otp/verify/          finish sign-up -> access + refresh + profile
    POST   password/reset/      code + new password -> access + refresh + profile
    POST   password/change/     current + new password, while signed in
    POST   refresh/             rotate the access token
    POST   logout/              blacklist the refresh token, revoke the session
    GET    me/                  caller's own profile
    PATCH  me/                  update own editable fields
    GET    sessions/            devices this user is signed in on
    DELETE sessions/<id>/       revoke a device (own, or admin revoking in-society)
"""

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import DeviceSession, OtpCode, OtpPurpose, User
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
    PasswordResetSerializer,
    ResidentRegistrationSerializer,
    SathifyTokenObtainPairSerializer,
    SocietyAdminRegistrationSerializer,
    StaffCreationSerializer,
    TokenPairSerializer,
    UserSerializer,
    WorkerRegistrationSerializer,
)
from .services import (
    OtpThrottled,
    OtpVerificationError,
    SMSDeliveryError,
    complete_registration,
    register_device_session,
    request_otp,
    reset_password_with_otp,
    revoke_other_sessions,
)

logger = logging.getLogger(__name__)


def _error(code, message, details=None, status_code=status.HTTP_400_BAD_REQUEST):
    """The error envelope every endpoint in this module returns."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=status_code,
    )


def _device_from(request):
    """Pull the optional device block out of a request body."""
    device = DeviceInfoSerializer(data=request.data.get("device", {}) or {})
    device.is_valid(raise_exception=False)
    return device.validated_data


# ---------------------------------------------------------------------------
# 1.1 Registration
# ---------------------------------------------------------------------------


class _BaseRegistrationView(generics.CreateAPIView):
    """Shared response shape for every self-registration flow.

    Creating the account immediately sends a verification code to the number
    given. Redeeming it at ``otp/verify/`` marks the phone verified and signs
    the new user in with the password they just chose, so sign-up runs straight
    through into the app.
    """

    permission_classes = [AllowAny]
    pending_message = "Registration received. An administrator will review your account."

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Dispatch the code the client is about to prompt for.
        #
        # A throttle here must not fail the registration: the account is
        # already created and rolling it back would leave the user unable to
        # retry with the same number. The client is told no code went out and
        # offers a resend instead, which the cooldown will then allow.
        otp_sent = True
        try:
            request_otp(user.phone_number, OtpPurpose.REGISTRATION)
        except (OtpThrottled, SMSDeliveryError) as exc:
            otp_sent = False
            logger.warning("No code sent at registration for %s: %s", user.phone_number, exc)

        logger.info("Registered %s user %s", user.role, user.phone_number)
        return Response(
            {
                "user": UserSerializer(user).data,
                "message": self.pending_message,
                # The client shows a "pending approval" screen rather than a
                # dashboard the user cannot yet act in.
                "requires_approval": True,
                # Drives the next screen: the OTP prompt either way, with the
                # resend button already live when this is false.
                "otp_sent": otp_sent,
                "otp_purpose": OtpPurpose.REGISTRATION,
                "verification_message": (
                    f"We sent a 6-digit code to {user.phone_number}. "
                    f"It expires in {OtpCode.VALIDITY_MINUTES} minutes."
                    if otp_sent
                    else "We could not send a code just yet. Tap resend in a moment."
                ),
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

    A user who has not yet redeemed their registration code can still sign in
    with the password they chose — ``is_phone_verified`` stays false and the
    ``IsPhoneVerified`` permission is what gates anything requiring it. Refusing
    the sign-in outright would strand somebody whose SMS never arrived, with no
    screen to ask for another from.
    """

    serializer_class = SathifyTokenObtainPairSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == status.HTTP_200_OK:
            user = User.objects.get(pk=response.data["user"]["id"])
            refresh = RefreshToken(response.data["refresh"])
            register_device_session(
                user=user,
                refresh_token=refresh,
                request=request,
                device=_device_from(request),
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
    """Changing a password you already know, from inside the app."""

    permission_classes = [IsAuthenticated]
    serializer_class = PasswordChangeSerializer

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password", "updated_at"])

        # A password change should end every other session: if the change was
        # prompted by a suspected compromise, leaving them alive defeats it.
        revoke_other_sessions(request.user, reason="Password changed")

        return Response({"message": "Password updated. Please sign in again."})


# ---------------------------------------------------------------------------
# 1.4 OTP & phone verification
#
# Two flows, both about proving control of a phone number rather than signing
# in: verifying a new account, and resetting a forgotten password.
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Auth"],
    summary="Request an OTP",
    description="Serves phone verification at sign-up and password reset. Rate "
    "limited: one code per 60 seconds, five per hour per number. Codes are valid "
    "for 2 minutes and survive 5 wrong guesses before dying.",
    request=OtpRequestSerializer,
    responses={
        200: MessageResponseSerializer,
        429: OpenApiResponse(description="Resend cooldown or hourly ceiling hit"),
    },
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
            return _error(
                "throttled",
                str(exc),
                {"retry_after_seconds": exc.retry_after_seconds},
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except SMSDeliveryError as exc:
            # Reported rather than swallowed. The user is about to sit waiting
            # for a code that is never coming, and "try again" is something they
            # can act on where a silent success is not.
            logger.error("Could not deliver an OTP to %s: %s", phone, exc)
            return _error(
                "sms_unavailable",
                "We could not send a code just now. Please try again in a moment.",
                {},
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Never reveal whether the number is registered — that would turn this
        # endpoint into a user-enumeration oracle. Note that request_otp is
        # called for unknown numbers too, so the timing matches as well as the
        # wording; short-circuiting on "no such user" would leak through latency
        # what this message is careful not to say.
        return Response(
            {"message": "If that number is valid, a verification code has been sent."}
        )


@extend_schema(
    tags=["Auth"],
    summary="Verify a new account's phone number",
    description="Finishes sign-up. A correct code marks the phone verified and "
    "signs the new user in, so they do not have to retype the password they set "
    "moments earlier. Every later sign-in uses `login/`.",
    request=OtpVerifySerializer,
    responses={
        200: OtpVerifyResponseSerializer,
        400: OpenApiResponse(description="Code incorrect, expired, or out of attempts"),
    },
)
class OtpVerifyView(APIView):
    permission_classes = [AllowAny]
    serializer_class = OtpVerifySerializer

    def post(self, request):
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user, tokens = complete_registration(
                phone_number=serializer.validated_data["phone_number"],
                code=serializer.validated_data["code"],
                request=request,
                device=_device_from(request),
            )
        except OtpVerificationError as exc:
            return _error("invalid_otp", str(exc))

        return Response(
            {"verified": True, **tokens, "user": UserSerializer(user).data}
        )


@extend_schema(
    tags=["Auth"],
    summary="Reset a forgotten password",
    description="Takes a code requested with `purpose=password_reset` plus the "
    "new password. Signs the user in and revokes every other session.",
    request=PasswordResetSerializer,
    responses={
        200: TokenPairSerializer,
        400: OpenApiResponse(description="Code incorrect, expired, or password rejected"),
    },
)
class PasswordResetView(APIView):
    """"Forgot password", answered by SMS rather than by email.

    Module 1.4's premise: many domestic workers have no reliable email address,
    so an emailed reset link would exclude a large share of the users this
    platform exists for. The phone number is the account's anchor, so proving
    control of it is both the strongest signal available and the one every user
    can actually complete.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetSerializer

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user, tokens = reset_password_with_otp(
                phone_number=serializer.validated_data["phone_number"],
                code=serializer.validated_data["code"],
                new_password=serializer.validated_data["new_password"],
                request=request,
                device=_device_from(request),
            )
        except OtpVerificationError as exc:
            return _error("invalid_otp", str(exc))

        return Response({**tokens, "user": UserSerializer(user).data})


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
