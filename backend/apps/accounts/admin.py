"""
Module 1 — Django Admin registration.

Module 11 extends this into the full administrator control centre. What is here
now is the approval queue, which Modules 2 and 3 both depend on: a resident or
worker who is never approved can never transact.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import DeviceSession, OtpCode, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """User administration, keyed on phone number rather than username."""

    ordering = ["-date_joined"]
    list_display = [
        "phone_number",
        "get_full_name",
        "role",
        "society",
        "is_approved",
        "is_phone_verified",
        "date_joined",
    ]
    list_filter = ["role", "is_approved", "is_phone_verified", "society", "is_staff"]
    search_fields = ["phone_number", "first_name", "last_name", "email"]
    readonly_fields = ["date_joined", "last_login", "created_at", "updated_at", "approved_at"]

    fieldsets = (
        (None, {"fields": ("phone_number", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email", "preferred_language")}),
        (_("Role & society"), {"fields": ("role", "society")}),
        (
            _("Approval"),
            {
                "fields": ("is_approved", "approved_at", "approved_by", "is_phone_verified"),
                "description": _(
                    "An unapproved user can sign in but cannot transact: workers "
                    "stay out of search results and residents cannot hire."
                ),
            },
        ),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Dates"), {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone_number", "role", "society", "password1", "password2"),
            },
        ),
    )

    actions = ["approve_selected_users", "revoke_approval"]

    @admin.display(description=_("Approve selected users"))
    def approve_selected_users(self, request, queryset):
        approved = 0
        for user in queryset.filter(is_approved=False):
            user.approve(approved_by=request.user)
            approved += 1
        self.message_user(request, f"{approved} user(s) approved.")

    @admin.display(description=_("Revoke approval"))
    def revoke_approval(self, request, queryset):
        count = queryset.filter(is_approved=True).update(
            is_approved=False, approved_at=None, updated_at=timezone.now()
        )
        self.message_user(request, f"Approval revoked for {count} user(s).")

    def get_queryset(self, request):
        """Scope the list to the administrator's own society.

        Superusers are platform staff and see everything; a society
        administrator must not browse another society's users.
        """
        queryset = super().get_queryset(request).select_related("society")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(society_id=request.user.society_id)


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    """Read-mostly view supporting the lost-or-stolen-device workflow."""

    list_display = ["user", "device_name", "platform", "last_seen_at", "revoked_at"]
    list_filter = ["platform", "revoked_at"]
    search_fields = ["user__phone_number", "device_id", "device_name"]
    readonly_fields = ["refresh_token_jti", "created_at", "last_seen_at"]
    actions = ["revoke_sessions"]

    @admin.display(description=_("Revoke selected sessions"))
    def revoke_sessions(self, request, queryset):
        count = 0
        for session in queryset.filter(revoked_at__isnull=True):
            session.revoke(reason=f"Revoked from admin by {request.user.pk}")
            count += 1
        self.message_user(request, f"{count} session(s) revoked.")


@admin.register(OtpCode)
class OtpCodeAdmin(admin.ModelAdmin):
    """Diagnostics only.

    The code itself is stored hashed and is deliberately not displayable — an
    admin who could read live OTPs could take over any account.
    """

    list_display = ["phone_number", "purpose", "created_at", "expires_at", "attempts", "consumed_at"]
    list_filter = ["purpose", "created_at"]
    search_fields = ["phone_number"]
    readonly_fields = [f.name for f in OtpCode._meta.fields]

    def has_add_permission(self, request):
        return False
