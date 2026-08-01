"""
Module 10 — Notifications: Django Admin.

Read-only. A notification is a record of what someone was told and when; editing
one after the fact would make it useless as evidence in exactly the situations
it gets consulted — a worker saying they were never informed of a gate refusal,
or a resident saying they never saw a payment reminder.

The delivery columns are the operationally useful part: a run of FAILED pushes
means a credential problem, and a run of SKIPPED ones usually means nobody has
registered a device.
"""

from django.contrib import admin

from .models import Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Module 10.3 — the notification centre, from the operator's side."""

    list_display = (
        "created_at",
        "recipient",
        "category",
        "title",
        "push_state",
        "sms_state",
        "is_read",
    )
    list_filter = ("category", "push_state", "sms_state", "society")
    search_fields = (
        "title",
        "body",
        "delivery_note",
        "recipient__first_name",
        "recipient__last_name",
        "recipient__phone_number",
    )
    date_hierarchy = "created_at"
    list_select_related = ("recipient",)
    raw_id_fields = ("recipient", "society")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields] + ["is_read"]

    def has_add_permission(self, request):
        # Notifications come from the events that caused them.
        return False

    @admin.display(boolean=True, description="Read")
    def is_read(self, obj):
        return obj.is_read


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    """Module 10.4 — explicit mutes. A missing row means "not muted"."""

    list_display = ("user", "category", "muted", "updated_at")
    list_filter = ("category", "muted")
    search_fields = ("user__first_name", "user__last_name", "user__phone_number")
    raw_id_fields = ("user",)
