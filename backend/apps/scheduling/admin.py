"""
Module 6 — Scheduling & Task Management: Django Admin.

There is no calendar model to register: the schedule is derived on read from
engagements and bookings (see ``schedule.py``), so browsing it means browsing
those. What is registered here is the two things Module 6 genuinely stores —
the resident's timing expectations and the reminder queue.
"""

from django.contrib import admin, messages
from django.utils import timezone

from .models import LeaveRequest, LeaveStatus, Reminder, ReminderStatus, TaskTiming


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    """Module 6.5 — urgent leave and the cover arranged for it.

    Read-mostly. The settlement fields are frozen at settlement and editing them
    here would silently disagree with the ``Payment`` row they explain, so they
    are displayed and not editable.
    """

    list_display = (
        "leave_date",
        "worker",
        "status",
        "replacement",
        "replacement_paise",
        "settled_at",
    )
    list_filter = ("status", "leave_date", "society")
    date_hierarchy = "leave_date"
    search_fields = (
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
        "replacement__user__first_name",
        "replacement__user__last_name",
    )
    autocomplete_fields = ("engagement", "worker", "replacement")
    readonly_fields = (
        "day_rate_paise",
        "forgone_paise",
        "replacement_paise",
        "settled_at",
        "replacement_confirmed_at",
        "resident_responded_at",
        "created_at",
        "updated_at",
    )

    @admin.action(description="Close as unfilled and settle")
    def close_as_unfilled(self, request, queryset):
        """For days nobody covered. Settles so the row stops looking pending."""
        from .services import settle_leave

        closed = 0
        for leave in queryset.exclude(status=LeaveStatus.REPLACEMENT_CONFIRMED):
            leave.status = LeaveStatus.UNFILLED
            leave.save(update_fields=["status", "updated_at"])
            settle_leave(leave)
            closed += 1

        self.message_user(
            request, f"Closed {closed} leave request(s) as unfilled.", messages.SUCCESS
        )

    actions = ["close_as_unfilled"]


@admin.register(TaskTiming)
class TaskTimingAdmin(admin.ModelAdmin):
    """Module 6.2 — what a resident expects, per engagement."""

    list_display = (
        "engagement",
        "expected_arrival",
        "arrival_grace_minutes",
        "expected_departure",
        "reminders_enabled",
        "updated_at",
    )
    list_filter = ("reminders_enabled",)
    search_fields = (
        "engagement__worker__user__first_name",
        "engagement__worker__user__last_name",
        "engagement__resident__user__first_name",
        "engagement__resident__user__last_name",
        "task_notes",
    )
    raw_id_fields = ("engagement", "updated_by")
    readonly_fields = ("created_at", "updated_at", "effective_window")

    @admin.display(description="In force")
    def effective_window(self, obj):
        """The times actually applied, including the engagement fallbacks.

        Both fields are nullable and fall back to the engagement's own times, so
        the stored row alone does not tell an administrator what is in effect.
        """
        if obj.pk is None:
            return "—"
        return (
            f"{obj.arrival:%H:%M} (+{obj.arrival_grace_minutes} min grace) "
            f"→ {obj.departure:%H:%M}"
        )


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    """Module 6.4 — the queue Module 10 drains.

    Read-mostly: a reminder is a record of a job, and editing its timestamps by
    hand would make the delivery trail meaningless. The actions below are for
    operating the queue, not for rewriting it.
    """

    list_display = (
        "id",
        "recipient",
        "kind",
        "title",
        "send_after",
        "event_at",
        "status",
        "sent_at",
    )
    list_filter = ("status", "kind", "society")
    search_fields = (
        "recipient__first_name",
        "recipient__last_name",
        "recipient__phone_number",
        "title",
    )
    date_hierarchy = "send_after"
    list_select_related = ("recipient",)
    raw_id_fields = ("recipient", "engagement", "booking", "society")
    readonly_fields = ("created_at", "updated_at", "sent_at", "is_stale")

    @admin.display(boolean=True, description="Stale")
    def is_stale(self, obj):
        return obj.is_stale

    @admin.action(description="Cancel stale reminders (event already passed)")
    def cancel_stale(self, request, queryset):
        """A reminder about a visit that already happened is worse than none."""
        cancelled = queryset.filter(
            status=ReminderStatus.SCHEDULED, event_at__lte=timezone.now()
        ).update(status=ReminderStatus.CANCELLED, updated_at=timezone.now())

        self.message_user(request, f"{cancelled} stale reminder(s) cancelled.", messages.INFO)

    @admin.action(description="Re-queue failed reminders")
    def requeue_failed(self, request, queryset):
        requeued = queryset.filter(status=ReminderStatus.FAILED).update(
            status=ReminderStatus.SCHEDULED,
            failure_reason="",
            updated_at=timezone.now(),
        )
        self.message_user(request, f"{requeued} reminder(s) re-queued.", messages.INFO)

    actions = ["cancel_stale", "requeue_failed"]
