"""
Module 7 — Attendance & Gate Verification: Django Admin.

``AttendanceEvent`` is read-only here, deliberately. It is the audit trail the
SRS's three-year retention requirement applies to (SRS 5.5), the record Module 8
bills from, and the evidence Module 9 scores on. An administrator quietly
editing a gate log would undermine all three at once — a wrong entry is
corrected by recording a superseding one, never by rewriting history.

Gate passes change only through their own actions, so revoking a card leaves a
reason and a timestamp rather than an unexplained flag flip.
"""

from django.contrib import admin, messages

from .models import AttendanceEvent, Decision, GatePass, RegisterScan


@admin.register(GatePass)
class GatePassAdmin(admin.ModelAdmin):
    """Module 7.1. The code column is deliberately absent from the list view.

    A gate pass code opens a gate. Showing every code in a browsable table would
    put a whole society's access on one screenshot, so it is visible only on the
    individual record.
    """

    list_display = ("worker", "is_active", "usable", "issued_at", "rotation_count")
    list_filter = ("is_active",)
    search_fields = (
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
    )
    list_select_related = ("worker__user",)
    raw_id_fields = ("worker",)
    readonly_fields = ("code", "issued_at", "revoked_at", "rotation_count", "usable")

    @admin.display(boolean=True, description="Usable")
    def usable(self, obj):
        return obj.is_usable

    @admin.action(description="Revoke selected passes")
    def revoke_passes(self, request, queryset):
        revoked = sum(
            1
            for gate_pass in queryset
            if gate_pass.revoke(reason="Revoked by administrator")
        )
        self.message_user(request, f"{revoked} pass(es) revoked.", messages.WARNING)

    @admin.action(description="Reissue selected passes")
    def rotate_passes(self, request, queryset):
        count = 0
        for gate_pass in queryset:
            gate_pass.rotate(reason="Reissued by administrator")
            count += 1
        self.message_user(
            request,
            f"{count} pass(es) reissued. Old cards no longer work.",
            messages.INFO,
        )

    actions = ["revoke_passes", "rotate_passes"]


@admin.register(AttendanceEvent)
class AttendanceEventAdmin(admin.ModelAdmin):
    """Modules 7.2–7.6. Read-only: this is an audit trail, not a worksheet."""

    list_display = (
        "occurred_at",
        "worker",
        "direction",
        "decision",
        "method",
        "was_expected",
        "face_state",
        "gate",
        "was_offline",
    )
    list_filter = (
        "decision",
        "direction",
        "method",
        "was_expected",
        "was_offline",
        "society",
    )
    search_fields = (
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
        "decision_reason",
        "override_reason",
        "device_id",
    )
    date_hierarchy = "occurred_at"
    list_select_related = ("worker__user", "gate", "recorded_by", "overridden_by")
    raw_id_fields = ("worker", "engagement", "booking", "society")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields] + [
            "face_state",
            "sync_delay",
        ]

    def has_add_permission(self, request):
        # Events come from the gate, never from a form.
        return False

    def has_delete_permission(self, request, obj=None):
        # Three-year retention (SRS 5.5).
        return False

    @admin.display(description="Face check")
    def face_state(self, obj):
        if not obj.face_checked:
            return "not checked"
        score = "—" if obj.face_match_score is None else f"{obj.face_match_score:.2f}"
        return f"{'matched' if obj.face_verified else 'NOT matched'} ({score})"

    @admin.display(description="Sync delay")
    def sync_delay(self, obj):
        """How long this sat in an offline queue.

        Worth watching: a gate whose events consistently arrive hours late has a
        connectivity problem that will eventually cost someone their pay.
        """
        seconds = obj.sync_delay_seconds
        return f"{seconds:.0f}s" if seconds < 60 else f"{seconds / 60:.0f} min"

    @admin.action(description="Allow pending face checks")
    def allow_pending(self, request, queryset):
        """Bulk-resolve pending face checks.

        A real resolution belongs at the gate, with a reason from the guard who
        looked at the person. This exists for clearing a backlog left by an
        outage, and records that it was an administrator who did it.
        """
        resolved = sum(
            1
            for event in queryset.filter(decision=Decision.PENDING_REVIEW)
            if event.resolve(
                allow=True, by=request.user, reason="Cleared in bulk by administrator"
            )
        )
        self.message_user(request, f"{resolved} entry(ies) allowed.", messages.INFO)

    actions = ["allow_pending"]


@admin.register(RegisterScan)
class RegisterScanAdmin(admin.ModelAdmin):
    """Module 7.5 — paper registers awaiting transcription."""

    list_display = ("for_date", "gate", "uploaded_by", "transcribed", "created_at")
    list_filter = ("transcribed", "society", "for_date")
    date_hierarchy = "for_date"
    list_select_related = ("gate", "uploaded_by")
    raw_id_fields = ("gate", "uploaded_by", "transcribed_by", "society")
    readonly_fields = ("transcribed", "transcribed_at", "transcribed_by", "created_at")

    @admin.action(description="Mark as transcribed")
    def mark_transcribed(self, request, queryset):
        done = sum(1 for scan in queryset if scan.mark_transcribed(by=request.user))
        self.message_user(
            request, f"{done} register(s) marked transcribed.", messages.INFO
        )

    actions = ["mark_transcribed"]
