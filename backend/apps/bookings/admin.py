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

from .models import Booking, BookingOffer, DayAvailability, ServiceCategory


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
        # Module 5.5. Read-only for the same reason the cancellation fee is:
        # this is what a household was actually charged for a broadcast, and an
        # operator editing it after the fact would break the trail Module 8
        # reconciles against.
        "emergency_surcharge_paise",
        "broadcast_at",
    )
    inlines = ()

    @admin.display(description="Starts at")
    def scheduled_start(self, obj):
        return obj.scheduled_start


@admin.register(BookingOffer)
class BookingOfferAdmin(admin.ModelAdmin):
    """Module 5.5 — who a broadcast was actually put to, and what they said.

    Entirely read-only. This is the answer to "six workers were free, so why did
    my request lapse?", and it is only worth having if nobody can tidy it up
    afterwards. Editing ``state`` by hand could also invent a second accepted
    offer, which is the one thing the whole flow is built to prevent.
    """

    list_display = ("booking", "worker", "state", "rank", "responded_at", "created_at")
    list_filter = ("state", "created_at")
    search_fields = (
        "booking__id",
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
    )
    list_select_related = ("booking__category", "worker__user")
    raw_id_fields = ("booking", "worker")
    readonly_fields = (
        "booking", "worker", "state", "rank", "responded_at", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False
