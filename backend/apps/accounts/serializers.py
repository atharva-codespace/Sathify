"""
Module 1 — Identity & Access Management: serializers.

Registration is split by role rather than handled by one polymorphic serializer,
because the rules genuinely differ:

* Residents and workers SELF-register and land unapproved (SRS 3.1, 3.2).
* Guards and administrators are CREATED BY a society administrator — they are
  trusted operational staff, not open sign-ups, so exposing them to
  self-registration would let anyone claim gate authority.

Sign-in is phone number plus password. The OTP has a narrower job — proving the
phone number is real at sign-up, and re-proving it when a password has been
forgotten — so it appears only in those two flows, never as a way to sign in.
"""

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import DeviceSession, OtpPurpose, Role, User, phone_validator


class SathifyTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Adds Sathify-specific claims to the access token.

    Embedding role and society in the token lets the Flutter app route straight
    to the correct dashboard on login, and lets the guard app know which
    society's booking list to cache offline — both without an extra round trip
    to /me on a possibly-unreliable gate connection.

    Never put anything secret in a JWT: the payload is base64-encoded, not
    encrypted, and the client can read it.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        for claim, value in token_claims_for(user).items():
            token[claim] = value
        return token

    def validate(self, attrs):
        """Return the token pair plus the profile fields the app needs at login."""
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


def token_claims_for(user) -> dict:
    """The custom claims every Sathify token carries, however it was issued.

    Shared with ``services.build_token_pair`` so a token minted after the
    registration OTP is indistinguishable from one minted at password sign-in.
    When these drifted apart, a newly registered user's app read a token with no
    ``role`` claim and could not choose a dashboard.
    """
    return {
        "role": user.role,
        "society_id": user.society_id,
        "is_approved": user.is_approved,
        "full_name": user.get_full_name(),
        "preferred_language": user.preferred_language,
    }


class UserSerializer(serializers.ModelSerializer):
    """The caller's own profile. Safe to return to the account owner."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    society_name = serializers.CharField(source="society.name", read_only=True, default=None)

    class Meta:
        model = User
        fields = [
            "id",
            "phone_number",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "role",
            "role_display",
            "society",
            "society_name",
            "is_approved",
            "is_phone_verified",
            "preferred_language",
            "date_joined",
        ]
        # Role, society and approval are set by the registration and approval
        # flows. If they were writable a resident could PATCH themselves into a
        # society administrator — the single most damaging bug this module could
        # ship.
        read_only_fields = [
            "id",
            "phone_number",
            "role",
            "society",
            "is_approved",
            "is_phone_verified",
            "date_joined",
        ]


class _BaseRegistrationSerializer(serializers.ModelSerializer):
    """Shared password handling and phone-uniqueness rules.

    The password set here is the credential for every future sign-in. The OTP
    that follows registration verifies the phone number; it does not replace
    this.
    """

    password = serializers.CharField(
        write_only=True, min_length=8, style={"input_type": "password"}
    )
    password_confirm = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = [
            "phone_number",
            "first_name",
            "last_name",
            "email",
            "password",
            "password_confirm",
            "preferred_language",
        ]

    def validate_phone_number(self, value):
        if User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError(
                "An account with this phone number already exists. Try signing in."
            )
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        validate_password(attrs["password"])
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        return User.objects.create_user(password=password, **validated_data)


class ActiveSocietyField(serializers.PrimaryKeyRelatedField):
    """Accepts only societies that are ACTIVE.

    A society still pending verification must not accumulate registrations, and
    a rejected or suspended one must not accept new ones. Resolving the queryset
    in ``get_queryset`` rather than at class-definition time keeps the import
    order between the accounts and societies apps from mattering.
    """

    def get_queryset(self):
        from apps.societies.models import Society, SocietyStatus

        return Society.objects.filter(status=SocietyStatus.ACTIVE)


class ResidentRegistrationSerializer(_BaseRegistrationSerializer):
    """Resident self-registration (Module 1.1, feeding Module 2.3's queue).

    The resident picks the society they live in; the flat linkage and proof of
    residence are added by Module 2, which owns the Flat model and the
    administrator approval queue.
    """

    society = ActiveSocietyField(
        required=True,
        help_text="The society this resident lives in.",
    )

    class Meta(_BaseRegistrationSerializer.Meta):
        fields = _BaseRegistrationSerializer.Meta.fields + ["society"]

    def create(self, validated_data):
        validated_data["role"] = Role.RESIDENT
        validated_data["is_approved"] = False  # explicit: awaits admin approval
        return super().create(validated_data)


class WorkerRegistrationSerializer(_BaseRegistrationSerializer):
    """Domestic worker self-registration (Module 1.1, feeding Module 3).

    A worker registers against the society they intend to work in, then
    completes KYC (Aadhaar + photo) in Module 3. They stay invisible to search
    and are refused gate entry until an administrator approves them.
    """

    society = ActiveSocietyField(
        required=True,
        help_text="The society this worker intends to work in.",
    )

    class Meta(_BaseRegistrationSerializer.Meta):
        fields = _BaseRegistrationSerializer.Meta.fields + ["society"]

    def create(self, validated_data):
        validated_data["role"] = Role.WORKER
        validated_data["is_approved"] = False
        return super().create(validated_data)


class SocietyAdminRegistrationSerializer(_BaseRegistrationSerializer):
    """Society administrator self-registration.

    Deliberately creates the account with NO society attached: the administrator
    then registers their society in Module 2.1, which is itself held pending
    until verified. This is what stops someone self-declaring authority over a
    society that already exists.
    """

    def create(self, validated_data):
        validated_data["role"] = Role.SOCIETY_ADMIN
        validated_data["society"] = None
        validated_data["is_approved"] = False
        return super().create(validated_data)


class StaffCreationSerializer(serializers.ModelSerializer):
    """Administrator-created guard or administrator accounts (Module 1.1).

    Created pre-approved and scoped to the creating administrator's society —
    an administrator cannot mint staff for a society they do not run. The
    society is taken from the request user, never from the payload.

    The administrator sets an initial password and passes it to the new staff
    member, who should change it once signed in.
    """

    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["phone_number", "first_name", "last_name", "email", "role", "password"]

    def validate_role(self, value):
        if value not in {Role.GUARD, Role.SOCIETY_ADMIN}:
            raise serializers.ValidationError(
                "Administrators may only create guard or society-administrator accounts. "
                "Residents and workers must register themselves."
            )
        return value

    def validate_phone_number(self, value):
        if User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("This phone number is already registered.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        creator = self.context["request"].user
        password = validated_data.pop("password")
        user = User.objects.create_user(
            password=password,
            society=creator.society,  # never trust a society from the payload
            is_approved=True,
            approved_by=creator,
            **validated_data,
        )
        return user


# ---------------------------------------------------------------------------
# OTP (Module 1.4)
# ---------------------------------------------------------------------------


class OtpRequestSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=13, validators=[phone_validator])
    purpose = serializers.ChoiceField(
        choices=OtpPurpose.choices, default=OtpPurpose.REGISTRATION
    )


class OtpVerifySerializer(serializers.Serializer):
    """The registration code, plus the device it is being redeemed on.

    ``device`` is optional but strongly wanted: without it the session lands
    under ``unknown-device`` and Module 1.5's "sign out my other phone" list
    cannot tell two installations apart.
    """

    phone_number = serializers.CharField(max_length=13, validators=[phone_validator])
    code = serializers.CharField(min_length=6, max_length=6)
    device = serializers.DictField(required=False)


class PasswordResetSerializer(serializers.Serializer):
    """Set a new password against a code texted to the account's phone.

    No current password is asked for — the user is here precisely because they
    do not have it. The code is the proof, which is why it is scoped to
    ``password_reset`` and cannot be a leftover registration code.
    """

    phone_number = serializers.CharField(max_length=13, validators=[phone_validator])
    code = serializers.CharField(min_length=6, max_length=6)
    new_password = serializers.CharField(write_only=True, min_length=8)
    device = serializers.DictField(required=False)

    def validate_new_password(self, value):
        # No user instance to compare against: this runs before the code is
        # checked, and looking the account up by the phone number in an
        # unauthenticated payload — just to enrich a validation message — would
        # leak whether that number is registered.
        validate_password(value)
        return value


class LogoutSerializer(serializers.Serializer):
    """The refresh token to blacklist at sign-out."""

    refresh = serializers.CharField()


class MessageResponseSerializer(serializers.Serializer):
    """Generic ``{"message": "..."}`` response, declared so it appears in the
    OpenAPI schema the Flutter client is built against."""

    message = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    """A signed-in session, declared for the OpenAPI schema.

    Password sign-in, the registration OTP and a completed password reset all
    resolve to this exact shape, so the Flutter client has one success path to
    handle rather than three.
    """

    access = serializers.CharField(help_text="Bearer token, valid 60 minutes.")
    refresh = serializers.CharField(
        help_text="Valid 30 days. Rotated on every use; the spent one is blacklisted."
    )
    user = UserSerializer()


class OtpVerifyResponseSerializer(TokenPairSerializer):
    verified = serializers.BooleanField()


class PasswordChangeSerializer(serializers.Serializer):
    """Changing a password you already know, from inside the app."""

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Your current password is incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value, self.context["request"].user)
        return value


# ---------------------------------------------------------------------------
# Device sessions (Module 1.5)
# ---------------------------------------------------------------------------


class DeviceSessionSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = DeviceSession
        fields = [
            "id",
            "device_id",
            "device_name",
            "platform",
            "ip_address",
            "last_seen_at",
            "revoked_at",
            "revoked_reason",
            "created_at",
            "is_current",
        ]
        read_only_fields = fields

    def get_is_current(self, obj) -> bool:
        """Flags the session making this request, so the UI can label it."""
        request = self.context.get("request")
        current_device = request.headers.get("X-Device-Id") if request else None
        return bool(current_device) and obj.device_id == current_device


class DeviceInfoSerializer(serializers.Serializer):
    """Optional device block accepted at login, used to open a session."""

    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    device_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    platform = serializers.CharField(max_length=20, required=False, allow_blank=True)
    fcm_token = serializers.CharField(max_length=255, required=False, allow_blank=True)
