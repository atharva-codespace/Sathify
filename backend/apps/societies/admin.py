"""
Module 2 — Django Admin.

Society *verification* lives here rather than in the API on purpose: activating
a society is a platform-level trust decision, not something a society's own
administrator may perform on themselves.
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Flat, Gate, Resident, Society, SocietyStatus, Tower


class TowerInline(admin.TabularInline):
    model = Tower
    extra = 0
    fields = ["name", "floors"]


class GateInline(admin.TabularInline):
    model = Gate
    extra = 0
    fields = ["name", "is_active"]


@admin.register(Society)
class SocietyAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "pincode", "status", "mapped_flat_count", "created_at"]
    list_filter = ["status", "city", "state"]
    search_fields = ["name", "city", "pincode", "registration_number"]
    readonly_fields = ["verified_at", "created_at", "updated_at", "mapped_flat_count"]
    inlines = [TowerInline, GateInline]

    fieldsets = (
        (None, {"fields": ("name", "registration_number", "status", "verified_at")}),
        (_("Address"), {"fields": ("address_line", "city", "state", "pincode",
                                   "latitude", "longitude")}),
        (_("Scale"), {"fields": ("total_towers", "total_flats", "mapped_flat_count",
                                 "gate_count")}),
        (_("Operating rules"), {"fields": ("booking_notice_hours", "guard_shift_hours",
                                           "allow_resident_self_checkin")}),
        (_("Review"), {"fields": ("rejection_reason", "created_at", "updated_at")}),
    )

    actions = ["verify_and_activate", "suspend_societies"]

    @admin.display(description=_("Verify and activate selected societies"))
    def verify_and_activate(self, request, queryset):
        """Activating also approves the society's pending administrators.

        Without that second step a verified society would still have nobody
        able to operate in it.
        """
        activated = 0
        for society in queryset.exclude(status=SocietyStatus.ACTIVE):
            society.activate()
            activated += 1
        self.message_user(
            request, f"{activated} society(ies) activated, with their administrators approved."
        )

    @admin.display(description=_("Suspend selected societies"))
    def suspend_societies(self, request, queryset):
        count = queryset.filter(status=SocietyStatus.ACTIVE).update(
            status=SocietyStatus.SUSPENDED, updated_at=timezone.now()
        )
        self.message_user(request, f"{count} society(ies) suspended.")


class FlatInline(admin.TabularInline):
    model = Flat
    extra = 0
    fields = ["number", "floor"]


@admin.register(Tower)
class TowerAdmin(admin.ModelAdmin):
    list_display = ["name", "society", "floors", "flat_count"]
    list_filter = ["society"]
    search_fields = ["name", "society__name"]
    inlines = [FlatInline]

    @admin.display(description=_("Flats"))
    def flat_count(self, obj):
        return obj.flats.count()


@admin.register(Flat)
class FlatAdmin(admin.ModelAdmin):
    list_display = ["__str__", "tower", "floor", "resident_count"]
    list_filter = ["tower__society", "tower"]
    search_fields = ["number", "tower__name"]

    @admin.display(description=_("Residents"))
    def resident_count(self, obj):
        return obj.residents.count()


@admin.register(Gate)
class GateAdmin(admin.ModelAdmin):
    list_display = ["name", "society", "is_active"]
    list_filter = ["society", "is_active"]


@admin.register(Resident)
class ResidentAdmin(admin.ModelAdmin):
    """Module 2.3 — the resident approval queue."""

    list_display = ["user", "flat", "relationship", "is_primary", "is_approved", "created_at"]
    list_filter = ["relationship", "is_primary", "user__is_approved", "flat__tower__society"]
    search_fields = ["user__phone_number", "user__first_name", "user__last_name", "flat__number"]
    readonly_fields = ["reviewed_at", "reviewed_by", "created_at", "updated_at"]
    autocomplete_fields = ["flat"]

    actions = ["approve_residents"]

    @admin.display(boolean=True, description=_("Approved"))
    def is_approved(self, obj):
        return obj.user.is_approved

    @admin.display(description=_("Approve selected residents"))
    def approve_residents(self, request, queryset):
        approved = 0
        for resident in queryset.select_related("user"):
            if not resident.user.is_approved:
                resident.user.approve(approved_by=request.user)
                resident.reviewed_at = timezone.now()
                resident.reviewed_by = request.user
                resident.save(update_fields=["reviewed_at", "reviewed_by", "updated_at"])
                approved += 1
        self.message_user(request, f"{approved} resident(s) approved.")

    def get_queryset(self, request):
        """Scope to the administrator's own society."""
        queryset = super().get_queryset(request).select_related("user", "flat__tower")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(flat__tower__society_id=request.user.society_id)
