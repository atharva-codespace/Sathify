"""Module 4 — Discovery & Hiring: Django Admin.

Read-mostly on purpose. Hire requests and engagements are records of what two
people agreed, so an administrator editing terms after the fact would leave the
resident and worker looking at different arrangements with no trace of the
change. Support work happens through the lifecycle actions below, which write
the same audit fields the API does.
"""

from django.contrib import admin, messages
from django.utils import timezone

from .models import Engagement, EngagementEndReason, EngagementStatus, HireRequest


@admin.register(HireRequest)
class HireRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "resident",
        "worker",
        "service_type",
        "status",
        "monthly_rate",
        "expires_at",
        "created_at",
    )
    list_filter = ("status", "service_type", "society", "created_at")
    search_fields = (
        "resident__user__first_name",
        "resident__user__last_name",
        "resident__user__phone_number",
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
    )
    date_hierarchy = "created_at"
    list_select_related = ("resident__user", "worker__user", "service_type")
    autocomplete_fields = ("resident", "society", "service_type")
    # Module 3 has not registered a WorkerProfile admin yet, and autocomplete
    # requires one. A raw_id widget needs no such registration, so this module
    # does not have to reach into another module's admin to work.
    raw_id_fields = ("worker",)
    readonly_fields = ("created_at", "updated_at", "responded_at", "response_hours")

    @admin.display(description="Responded in (hours)")
    def response_hours(self, obj):
        hours = obj.response_hours
        return "—" if hours is None else f"{hours:.1f}"

    @admin.action(description="Expire lapsed requests")
    def expire_lapsed(self, request, queryset):
        """Manual trigger for the sweep that normally happens on read.

        Useful when reconciling the response-rate statistics, which count a
        lapsed request against the worker only once it has actually been swept.
        """
        swept = queryset.expire_lapsed()
        self.message_user(request, f"{swept} request(s) marked expired.", messages.INFO)

    actions = ["expire_lapsed"]


@admin.register(Engagement)
class EngagementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "resident",
        "worker",
        "service_type",
        "status",
        "monthly_rate",
        "started_on",
    )
    list_filter = ("status", "service_type", "society", "started_on")
    search_fields = (
        "resident__user__first_name",
        "resident__user__last_name",
        "resident__user__phone_number",
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
    )
    date_hierarchy = "started_on"
    list_select_related = ("resident__user", "worker__user", "service_type")
    autocomplete_fields = ("resident", "society", "hire_request", "service_type")
    raw_id_fields = ("worker",)  # see HireRequestAdmin
    readonly_fields = (
        "created_at",
        "updated_at",
        "paused_at",
        "resumed_at",
        "ended_at",
        "ended_by",
        "schedule_summary",
    )

    @admin.display(description="Schedule")
    def schedule_summary(self, obj):
        return f"{', '.join(obj.day_labels)} at {obj.start_time:%H:%M} ({obj.expected_duration_minutes} min)"

    @admin.action(description="Pause selected engagements")
    def pause_engagements(self, request, queryset):
        changed = sum(
            1 for e in queryset.filter(status=EngagementStatus.ACTIVE) if e.pause("Paused by administrator")
        )
        self.message_user(request, f"{changed} engagement(s) paused.", messages.INFO)

    @admin.action(description="Resume selected engagements")
    def resume_engagements(self, request, queryset):
        changed = sum(1 for e in queryset.filter(status=EngagementStatus.PAUSED) if e.resume())
        self.message_user(request, f"{changed} engagement(s) resumed.", messages.INFO)

    @admin.action(description="Terminate selected engagements")
    def terminate_engagements(self, request, queryset):
        changed = sum(
            1
            for e in queryset.exclude(status=EngagementStatus.TERMINATED)
            if e.terminate(
                reason=EngagementEndReason.ADMIN_ENDED,
                note=f"Terminated from admin on {timezone.now():%Y-%m-%d}",
                by=request.user,
            )
        )
        self.message_user(request, f"{changed} engagement(s) terminated.", messages.WARNING)

    actions = ["pause_engagements", "resume_engagements", "terminate_engagements"]
