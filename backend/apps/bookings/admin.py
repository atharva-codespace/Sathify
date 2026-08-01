"""
Module 5 — One-Day Service Booking: Django Admin.

``ServiceCategory`` is fully editable: duration and price guidance are exactly
the sort of thing an operator tunes once real bookings come in.

``Booking`` is read-mostly, for the same reason Module 4's records are — it is
the record of what two people agreed. In particular ``cancellation_fee`` is
read-only: it is what was actually charged at the time, and silently editing it
would break the audit trail Module 8 settles against.
"""

from django.contrib import admin

from .models import Booking, DayAvailability, ServiceCategory


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "service_type",
        "expected_duration_minutes",
        "price_guidance",
        "bypasses_notice_period",
        "is_active",
    )
    list_filter = ("is_active", "bypasses_notice_period", "service_type")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("service_type",)

    @admin.display(description="Price guidance")
    def price_guidance(self, obj):
        return obj.price_guidance


@admin.register(DayAvailability)
class DayAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("worker", "date", "is_available", "start_time", "end_time")
    list_filter = ("is_available", "date")
    search_fields = (
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
    )
    date_hierarchy = "date"
    list_select_related = ("worker__user",)
    raw_id_fields = ("worker",)  # Module 3 has not registered a WorkerProfile admin.


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "category",
        "resident",
        "worker",
        "scheduled_date",
        "start_time",
        "status",
        "quoted_price",
        "cancellation_fee",
    )
    list_filter = ("status", "category", "society", "scheduled_date")
    search_fields = (
        "resident__user__first_name",
        "resident__user__last_name",
        "resident__user__phone_number",
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
    )
    date_hierarchy = "scheduled_date"
    list_select_related = ("resident__user", "worker__user", "category")
    autocomplete_fields = ("resident", "society", "category")
    raw_id_fields = ("worker",)
    readonly_fields = (
        "created_at",
        "updated_at",
        "confirmed_at",
        "declined_at",
        "completed_at",
        "cancelled_at",
        "cancelled_by",
        "cancellation_fee",
        "scheduled_start",
    )

    @admin.display(description="Starts at")
    def scheduled_start(self, obj):
        return obj.scheduled_start
