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
  the roles are mutually exclusive by design. Four belong to a society; the
  fifth, ``SUPERADMIN``, belongs to the platform and to no society at all.
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
    """The four user classes defined in SRS 2.2, plus the platform operator.

    ``SUPERADMIN`` is deliberately *not* "a society admin with more rows". Every
    permission class below reads ``role``, so widening ``SOCIETY_ADMIN`` to reach
    across societies would widen it for every society's own managing committee
    at the same time. It is a separate role with a separate ``society`` (always
    null) precisely so that the cross-society read path has exactly one entry
    point that can be audited — see ``apps.core.platform``.
    """

    RESIDENT = "resident", _("Resident")
    WORKER = "worker", _("Domestic Worker")
    GUARD = "guard", _("Security Guard")
    SOCIETY_ADMIN = "society_admin", _("Society Administrator")
    SUPERADMIN = "superadmin", _("Platform Operator")


#: Roles that belong to Sathify rather than to any one society. Their ``society``
#: is null, so every society-scoped queryset naturally returns nothing for them
#: rather than silently leaking one arbitrary society's rows.
PLATFORM_ROLES = frozenset({Role.SUPERADMIN})


class SuperadminLevel(models.TextChoices):
    """What a platform operator may do, beyond reading.

    Split because the two dangerous capabilities are dangerous in different
    directions and are rarely needed by the same person on the same day.
    Nobody holds both by default.
    """

    #: Read everything, and impersonate a society admin to fix their data.
    SUPPORT = "support", _("Support — read and impersonate")
    #: Read everything, refund, and confirm a settlement by hand.
    FINANCE = "finance", _("Finance — read, refund, settle")


# Indian mobile numbers: 10 digits beginning 6-9, optionally +91 prefixed.
phone_validator = RegexValidator(
    regex=r"^(\+91)?[6-9]\d{9}$",
    message=_("Enter a valid Indian mobile number, e.g. 9876543210."),
)


class UserManager(BaseUserManager):
    """Manager keyed on phone number instead of username.

    Users authenticate with a phone number and a password they choose at
    registration. The OTP is a separate mechanism with a narrower job: proving
    the phone number is real, once at sign-up, and re-proving it when somebody
    has forgotten their password. It is not the credential.

    ``password=None`` stores an unusable hash rather than an empty one, so an
    account created without a password cannot be signed into at all until one is
    set. That is the correct failure mode for a half-created account.
    """

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
        # Platform staff are pre-approved by definition.
        #
        # This used to default to SOCIETY_ADMIN, which quietly made every
        # `createsuperuser` an administrator *of no society* — a role whose
        # every queryset filters on `society_id`, and whose society is null.
        # Such an account reads as authorised by the permission classes and
        # then sees nothing, which is the most confusing of the two possible
        # failures. A platform operator now gets the role that describes them.
        extra_fields.setdefault("role", Role.SUPERADMIN)
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
        constraints = [
            # Module 1.3 — roles are mutually exclusive AND exhaustive.
            #
            # Exclusivity comes free from `role` being a single column: there is
            # no way to hold two. Exhaustiveness does not, and that is the half
            # worth enforcing in the database. `role` has no default, so any
            # code path that forgets to set it — a data import, a shell script,
            # a future serializer — would otherwise write `role=""`. Such a user
            # is invisible to every permission class in permissions.py, which
            # sounds safe but is not: it is an account in a state no part of the
            # system reasons about. Failing the write is better than storing it.
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values),
                name="user_role_is_a_known_role",
            ),
            # A platform operator belongs to no society, and a society-scoped
            # user must belong to one. Enforced here because the alternative —
            # a superadmin carrying a society_id — would make every scoped
            # queryset silently return that one society's rows and look like it
            # was working.
            models.CheckConstraint(
                condition=(
                    models.Q(role=Role.SUPERADMIN, society__isnull=True)
                    | ~models.Q(role=Role.SUPERADMIN)
                ),
                name="superadmin_belongs_to_no_society",
            ),
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

    @property
    def is_superadmin(self) -> bool:
        return self.role == Role.SUPERADMIN

    @property
    def superadmin_level(self) -> str | None:
        """Support or Finance, or None for anyone who is not platform staff.

        Read through a property rather than by reaching for the profile, so a
        missing profile is ``None`` (deny) instead of ``RelatedObjectDoesNotExist``
        raised from inside a permission check.
        """
        if not self.is_superadmin:
            return None
        profile = getattr(self, "superadmin_profile", None)
        return profile.level if profile else None

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
    """Why a code was issued.

    Only two reasons exist, and both are about proving control of a phone
    number rather than signing in. Ordinary sign-in uses a password; issuing a
    code for it would put a second, weaker credential path beside the first.

    Codes are scoped to their purpose, so one texted to verify a new account
    cannot be redeemed to reset that account's password. Without that scoping a
    registration code — which a stranger can trigger for any number — would be
    worth a password reset.
    """

    REGISTRATION = "registration", _("Phone verification at registration")
    PASSWORD_RESET = "password_reset", _("Password reset for a forgotten password")


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
    #: Wrong guesses allowed against one code before it is dead. Five, strictly:
    #: at 10^6 possibilities this leaves a 1-in-200,000 chance of a blind hit.
    MAX_ATTEMPTS = 5
    #: How long a code lives. Two minutes is short on purpose — it is the single
    #: biggest lever on the window an intercepted SMS is useful in, and an OTP
    #: that arrives is typed within seconds.
    VALIDITY_MINUTES = 2
    #: Resend cooldown. Half the validity window, so a user whose SMS is slow
    #: can request exactly one replacement before the first code lapses.
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


# ===========================================================================
# Platform operations — the Superadmin console
#
# Everything below exists because the console inverts this codebase's core
# invariant. `SocietyScopedModel` and `SocietyScopedQuerysetMixin` are built so
# that no request can read across societies; the console's whole purpose is to
# do exactly that. The response is not to loosen the invariant but to give it
# one documented exception with a name, a reason string and a log — so "who
# looked at this resident's record, and why?" is a query rather than an
# investigation.
# ===========================================================================


class SuperadminProfile(models.Model):
    """What a platform operator is allowed to do beyond reading.

    A separate row rather than a column on ``User`` so that granting Finance to
    somebody is an explicit act against an explicit table, and so revoking it
    leaves the account intact.
    """

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="superadmin_profile",
        limit_choices_to={"role": Role.SUPERADMIN},
    )
    level = models.CharField(max_length=20, choices=SuperadminLevel.choices)

    #: Read-wide is granted by the role. Write-narrow is granted here, and only
    #: Finance ever gets it: refunds and hand-confirmed settlements are the two
    #: console actions that move money without a gateway signature behind them.
    may_refund = models.BooleanField(default=False)
    may_settle_manually = models.BooleanField(default=False)

    granted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_superadmin_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("superadmin profile")
        verbose_name_plural = _("superadmin profiles")

    def __str__(self):
        return f"{self.user} ({self.get_level_display()})"

    def save(self, *args, **kwargs):
        # The money capabilities follow the level rather than being set
        # independently, so there is no way to hand somebody Support and then
        # tick "may refund" and forget it happened.
        self.may_refund = self.level == SuperadminLevel.FINANCE
        self.may_settle_manually = self.level == SuperadminLevel.FINANCE
        super().save(*args, **kwargs)


class ImpersonationGrant(models.Model):
    """A time-boxed, reason-gated session acting as a society's own admin.

    The console is read-wide and write-narrow: a Superadmin sees everything, but
    every mutation to a society's operational data happens *as* that society's
    administrator, through one of these. Two consequences worth stating, because
    they are the point rather than side effects:

    * The society's own audit trail records the change against a real
      administrator, not against an opaque platform actor. Their records stay
      answerable to them.
    * The grant expires on its own. An operator who forgets to close a session
      loses it anyway, which is the failure mode you want when the alternative
      is a permanent cross-tenant write capability sitting open in a browser tab.
    """

    DEFAULT_MINUTES = 30

    superadmin = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="impersonation_grants",
        limit_choices_to={"role": Role.SUPERADMIN},
    )
    target = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="impersonated_by_grants"
    )
    society = models.ForeignKey(
        "societies.Society", on_delete=models.CASCADE, related_name="impersonation_grants"
    )

    #: Never blank. A grant without a stated purpose is the thing this model
    #: exists to make impossible, so it is required at the database level too.
    reason = models.CharField(max_length=300)

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    reads = models.PositiveIntegerField(default=0)
    writes = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("impersonation grant")
        verbose_name_plural = _("impersonation grants")
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["society", "-started_at"])]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="impersonation_requires_a_reason",
            ),
        ]

    def __str__(self):
        return f"{self.superadmin} as {self.target} ({self.reason[:40]})"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = self.started_at + timedelta(minutes=self.DEFAULT_MINUTES)
        super().save(*args, **kwargs)

    @property
    def is_live(self) -> bool:
        return self.ended_at is None and self.expires_at > timezone.now()

    def end(self) -> bool:
        """Close the session. Idempotent."""
        if self.ended_at is not None:
            return False
        self.ended_at = timezone.now()
        self.save(update_fields=["ended_at"])
        return True


class PlatformAccessLog(models.Model):
    """One row per cross-society read that touched resident or worker PII.

    Written by ``apps.core.platform.PlatformScoped``, never by hand. The
    society FK is what makes §9.4d possible: a society can be shown when
    platform staff read its people's records, and why. Being watchable is the
    price of holding the bypass at all — a capability nobody can audit is one
    the committee has to take on trust, and this codebase does not ask them to.
    """

    superadmin = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True,
        related_name="platform_access_logs",
    )
    society = models.ForeignKey(
        "societies.Society", on_delete=models.CASCADE, null=True, blank=True,
        related_name="platform_access_logs",
        help_text=_("Null when the read genuinely spanned every society."),
    )

    model_label = models.CharField(max_length=100, db_index=True)
    action = models.CharField(max_length=40, default="read")
    reason = models.CharField(max_length=300, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("platform access log")
        verbose_name_plural = _("platform access logs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["society", "-created_at"]),
            models.Index(fields=["superadmin", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.superadmin} read {self.model_label} ({self.row_count} rows)"
