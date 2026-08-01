"""
Module 1 — Identity & Access Management: the user model.

Defined during scaffolding rather than later because Django resolves
AUTH_USER_MODEL when the very first migration is created, and changing it
afterwards requires rebuilding the migration history.

Design notes
------------
* Login is by PHONE NUMBER, not username or email. Many domestic workers have
  no reliable email address (Module 1.4), and a phone number is the identifier
  every user in this market already knows.
* ``role`` drives all role-based access control. A user holds exactly one role;
  the four roles are mutually exclusive by design.
* ``society`` is the multi-tenancy anchor read by ``SocietyScopedQuerysetMixin``.
* ``is_approved`` gates platform access behind administrator approval
  (SRS 3.1, 3.2). Registration alone grants nothing.
"""

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    """The four user classes defined in SRS 2.2."""

    RESIDENT = "resident", _("Resident")
    WORKER = "worker", _("Domestic Worker")
    GUARD = "guard", _("Security Guard")
    SOCIETY_ADMIN = "society_admin", _("Society Administrator")


# Indian mobile numbers: 10 digits beginning 6-9, optionally +91 prefixed.
phone_validator = RegexValidator(
    regex=r"^(\+91)?[6-9]\d{9}$",
    message=_("Enter a valid Indian mobile number, e.g. 9876543210."),
)


class UserManager(BaseUserManager):
    """Manager keyed on phone number instead of username."""

    use_in_migrations = True

    def _create_user(self, phone_number, password, **extra_fields):
        if not phone_number:
            raise ValueError("A phone number is required to create a user.")
        user = self.model(phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone_number, password, **extra_fields)

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # Platform staff are administrators and are pre-approved by definition.
        extra_fields.setdefault("role", Role.SOCIETY_ADMIN)
        extra_fields.setdefault("is_approved", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(phone_number, password, **extra_fields)


class User(AbstractUser):
    """The single authentication record shared across all four roles.

    Role-specific data lives in a profile model owned by the relevant module
    (``societies.Resident``, ``workers.WorkerProfile``, ``societies.SecurityGuard``)
    rather than being piled onto this table.
    """

    # AbstractUser's username is unused — the phone number identifies a user.
    username = None

    phone_number = models.CharField(
        _("phone number"),
        max_length=13,
        unique=True,
        validators=[phone_validator],
        help_text=_("Primary login identifier. Indian mobile number."),
    )
    email = models.EmailField(_("email address"), blank=True)

    role = models.CharField(
        _("role"),
        max_length=20,
        choices=Role.choices,
        db_index=True,
        help_text=_("Determines which endpoints and dashboards this user may access."),
    )

    society = models.ForeignKey(
        "societies.Society",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
        help_text=_(
            "Multi-tenancy anchor. Null only for platform staff and for a "
            "society administrator during initial society registration."
        ),
    )

    # --- Approval gate (SRS 3.1, 3.2) ---------------------------------------
    is_approved = models.BooleanField(
        _("approved"),
        default=False,
        db_index=True,
        help_text=_(
            "Set by a society administrator. An unapproved user can authenticate "
            "but cannot transact: workers stay out of search results and "
            "residents cannot hire."
        ),
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_users",
    )

    # --- Phone verification (Module 1.4) ------------------------------------
    is_phone_verified = models.BooleanField(default=False)

    preferred_language = models.CharField(
        max_length=5,
        default="en",
        choices=[("en", "English"), ("hi", "हिन्दी"), ("mr", "मराठी")],
        help_text=_("Drives Flutter UI language; multilingual support is MVP scope."),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []  # extra prompts for createsuperuser, beyond USERNAME_FIELD

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        indexes = [
            # Supports the commonest access pattern: "all pending workers at
            # this society" on the administrator's approval queue.
            models.Index(fields=["society", "role", "is_approved"]),
        ]

    def __str__(self):
        return f"{self.get_full_name() or self.phone_number} ({self.get_role_display()})"

    # --- Convenience role predicates ----------------------------------------
    # Used by DRF permission classes. Cheaper to read than comparing string
    # literals at every call site.
    @property
    def is_resident(self) -> bool:
        return self.role == Role.RESIDENT

    @property
    def is_worker(self) -> bool:
        return self.role == Role.WORKER

    @property
    def is_guard(self) -> bool:
        return self.role == Role.GUARD

    @property
    def is_society_admin(self) -> bool:
        return self.role == Role.SOCIETY_ADMIN

    def approve(self, approved_by=None):
        """Mark this user as approved. Idempotent."""
        if self.is_approved:
            return
        self.is_approved = True
        self.approved_at = timezone.now()
        self.approved_by = approved_by
        self.save(
            update_fields=["is_approved", "approved_at", "approved_by", "updated_at"]
        )


# ===========================================================================
# Module 1.4 — OTP & Phone Verification
# ===========================================================================


class OtpPurpose(models.TextChoices):
    REGISTRATION = "registration", _("Phone verification at registration")
    LOGIN = "login", _("Passwordless login")
    PASSWORD_RESET = "password_reset", _("Password reset")


class OtpCodeQuerySet(models.QuerySet):
    def active(self):
        """Codes that are still usable: unconsumed, unexpired, attempts left."""
        return self.filter(
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
            attempts__lt=OtpCode.MAX_ATTEMPTS,
        )


class OtpCode(models.Model):
    """A one-time passcode sent to a phone number.

    Security decisions worth keeping:

    * The code is stored HASHED, never in plaintext. A 6-digit code is
      low-entropy, so the database must not hand an attacker a working code if
      it leaks.
    * Attempts are capped (``MAX_ATTEMPTS``) so the 10^6 keyspace cannot be
      brute-forced against a live code.
    * Sends are rate-limited per phone number, both by a short resend cooldown
      and an hourly ceiling, so the endpoint cannot be used to spam somebody
      else's phone (or to burn through an SMS budget).
    * Keyed on phone number rather than user, because at registration the user
      record may not exist yet.
    """

    CODE_LENGTH = 6
    MAX_ATTEMPTS = 5
    VALIDITY_MINUTES = 10
    RESEND_COOLDOWN_SECONDS = 60
    MAX_SENDS_PER_HOUR = 5

    phone_number = models.CharField(max_length=13, db_index=True)
    code_hash = models.CharField(max_length=128)
    purpose = models.CharField(
        max_length=20, choices=OtpPurpose.choices, default=OtpPurpose.REGISTRATION
    )

    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = OtpCodeQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["phone_number", "purpose", "-created_at"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"OTP for {self.phone_number} ({self.purpose})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @classmethod
    def generate(cls, phone_number: str, purpose: str) -> tuple["OtpCode", str]:
        """Create a code and return ``(instance, plaintext_code)``.

        The plaintext is returned exactly once, for the SMS backend to deliver.
        It is never persisted and cannot be recovered afterwards.
        """
        # secrets, not random: this value guards account access.
        code = f"{secrets.randbelow(10 ** cls.CODE_LENGTH):0{cls.CODE_LENGTH}d}"

        # Supersede any outstanding codes so only the newest one works — two
        # valid codes at once would double an attacker's guessing surface.
        cls.objects.filter(
            phone_number=phone_number, purpose=purpose, consumed_at__isnull=True
        ).update(consumed_at=timezone.now())

        instance = cls.objects.create(
            phone_number=phone_number,
            code_hash=make_password(code),
            purpose=purpose,
            expires_at=timezone.now() + timedelta(minutes=cls.VALIDITY_MINUTES),
        )
        return instance, code

    def verify(self, code: str) -> bool:
        """Check ``code``, consuming this OTP on success.

        Returns False for an expired, already-used, or attempt-exhausted code.
        Every call counts as an attempt, so repeated guessing exhausts the cap.
        """
        if self.is_consumed or self.is_expired or self.attempts >= self.MAX_ATTEMPTS:
            return False

        self.attempts += 1
        if not check_password(code, self.code_hash):
            self.save(update_fields=["attempts"])
            return False

        self.consumed_at = timezone.now()
        self.save(update_fields=["attempts", "consumed_at"])
        return True


# ===========================================================================
# Module 1.5 — Session & Device Management
# ===========================================================================


class DeviceSession(models.Model):
    """One row per device a user is signed in on.

    Two requirements drive this:

    * A guard terminal at the gate should hold a single active session, so a
      device left signed in elsewhere cannot approve entries in parallel.
    * An administrator must be able to invalidate a lost or stolen phone
      *without* the user logging in first.

    Revocation works by blacklisting the refresh token this session was issued
    with, identified by its ``jti`` claim. Because the backend rotates refresh
    tokens, blacklisting the current one ends the session at its next refresh.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="sessions"
    )

    device_id = models.CharField(
        max_length=128,
        help_text=_("Stable client-generated identifier for this installation."),
    )
    device_name = models.CharField(max_length=120, blank=True)
    platform = models.CharField(max_length=20, blank=True)

    # Links this session to the JWT it was issued with (Module 1.2).
    refresh_token_jti = models.CharField(max_length=64, blank=True, db_index=True)

    # Populated by Module 10; kept here so a revoked device also stops
    # receiving push notifications.
    fcm_token = models.CharField(max_length=255, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "device_id"], name="unique_session_per_user_device"
            )
        ]
        indexes = [models.Index(fields=["user", "revoked_at"])]

    def __str__(self):
        state = "revoked" if self.is_revoked else "active"
        return f"{self.user.phone_number} on {self.device_name or self.device_id} ({state})"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def revoke(self, reason: str = "") -> None:
        """End this session and blacklist its refresh token. Idempotent."""
        if self.is_revoked:
            return

        # Local import: token_blacklist models import the user model, so a
        # module-level import here would be circular.
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        if self.refresh_token_jti:
            outstanding = OutstandingToken.objects.filter(
                jti=self.refresh_token_jti
            ).first()
            if outstanding:
                BlacklistedToken.objects.get_or_create(token=outstanding)

        self.revoked_at = timezone.now()
        self.revoked_reason = reason
        self.fcm_token = ""  # stop pushing to a device we just cut off
        self.save(update_fields=["revoked_at", "revoked_reason", "fcm_token"])
