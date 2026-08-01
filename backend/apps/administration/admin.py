"""
Module 11.1 — the worker and resident database view.

The modspec asks for this to be "built directly on Django Admin customizations
rather than a separate internal tool", and that is the right call for a
back-office directory: search, filters and pagination come free, and nobody has
to maintain a second admin UI.

-------------------------------------------------------------------------------
WHY PROXY MODELS RATHER THAN EDITING THE EXISTING SCREENS
-------------------------------------------------------------------------------
Modules 3.5 and 2.3 already register ``WorkerProfile`` and ``Resident`` in the
admin — as *review* screens, one pending record at a time, with approve and
reject actions and the KYC document side by side.

A directory is a different job: everybody at once, filterable, read-across.
Overloading one ModelAdmin with both purposes would make the review screen
noisier and the directory narrower. The proxies in ``models.py`` let the same
tables be registered twice with different columns, filters and permissions, and
add no table of their own.

-------------------------------------------------------------------------------
EVERY SCREEN HERE IS SOCIETY-SCOPED
-------------------------------------------------------------------------------
These are the screens a society administrator would be given staff access to,
so each one carries ``SocietyScopedAdminMixin``. Without it a staff user at one
society would read every other society's workers, residents and complaints —
see the note in ``apps/core/admin.py``.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import SocietyScopedAdminMixin

from .models import (
    Complaint,
    ComplaintUpdate,
    ResidentDirectory,
    UnmetDemand,
    WorkerDirectory,
)


@admin.register(WorkerDirectory)
class WorkerDirectoryAdmin(SocietyScopedAdminMixin, admin.ModelAdmin):
    """11.1 — every worker in the society, searchable and filterable."""

    society_lookup = "user__society"

    list_display = (
        "full_name",
        "phone_number",
        "services",
        "approval",
        "is_available",
        "trust_score",
        "average_rating",
        "rating_count",
        "completed_engagements",
    )
    list_filter = (
        "is_available",
        "user__is_approved",
        "service_types",
        "created_at",
    )
    search_fields = (
        "user__first_name",
        "user__last_name",
        "user__phone_number",
    )
    ordering = ("-trust_score",)
    list_select_related = ("user",)
    list_per_page = 50

    # A directory is for reading. Editing a worker's profile belongs on Module
    # 3.5's review screen, where the documents that justify a change are on the
    # same page.
    readonly_fields = ("trust_score", "average_rating", "rating_count", "completed_engagements")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .prefetch_related("service_types")
        )

    def has_add_permission(self, request):
        """Workers arrive by registering, never by being typed in here.

        A hand-created profile would have no user account, no consent record and
        no KYC attempt — an unverifiable worker who would nonetheless appear in
        search results.
        """
        return False

    @admin.display(description="Name", ordering="user__first_name")
    def full_name(self, obj):
        return obj.user.get_full_name() or obj.user.phone_number

    @admin.display(description="Phone", ordering="user__phone_number")
    def phone_number(self, obj):
        return obj.user.phone_number

    @admin.display(description="Services")
    def services(self, obj):
        names = [service.name for service in obj.service_types.all()]
        return ", ".join(names) if names else "—"

    @admin.display(description="Approved", boolean=True, ordering="user__is_approved")
    def approval(self, obj):
        return obj.user.is_approved


@admin.register(ResidentDirectory)
class ResidentDirectoryAdmin(SocietyScopedAdminMixin, admin.ModelAdmin):
    """11.1 — every resident in the society."""

    society_lookup = "flat__tower__society"

    list_display = (
        "full_name",
        "phone_number",
        "flat",
        "relationship",
        "is_primary",
        "approval",
        "trust_score",
        "rating_count",
    )
    list_filter = ("relationship", "is_primary", "user__is_approved", "flat__tower")
    search_fields = (
        "user__first_name",
        "user__last_name",
        "user__phone_number",
        "flat__number",
    )
    ordering = ("flat__tower__name", "flat__number")
    list_select_related = ("user", "flat__tower")
    list_per_page = 50
    readonly_fields = ("trust_score", "average_rating", "rating_count")

    def has_add_permission(self, request):
        return False

    @admin.display(description="Name", ordering="user__first_name")
    def full_name(self, obj):
        return obj.user.get_full_name() or obj.user.phone_number

    @admin.display(description="Phone", ordering="user__phone_number")
    def phone_number(self, obj):
        return obj.user.phone_number

    @admin.display(description="Approved", boolean=True, ordering="user__is_approved")
    def approval(self, obj):
        return obj.user.is_approved


class ComplaintUpdateInline(admin.TabularInline):
    """The history, inline and read-only.

    Deliberately not editable: the trail is the point. New entries are added
    through the API's ``updates/`` endpoint, which stamps the author.
    """

    model = ComplaintUpdate
    extra = 0
    can_delete = False
    readonly_fields = (
        "created_at",
        "author",
        "note",
        "old_status",
        "new_status",
        "is_system",
        "is_internal",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Complaint)
class ComplaintAdmin(SocietyScopedAdminMixin, admin.ModelAdmin):
    """11.3 — the complaint queue, for an administrator working at a desk."""

    list_display = (
        "reference",
        "category",
        "subject",
        "about",
        "priority",
        "status",
        "sla_state",
        "created_at",
    )
    list_filter = ("status", "priority", "category", "created_at")
    search_fields = ("reference", "subject", "description")
    date_hierarchy = "created_at"
    ordering = ("status", "sla_due_at")
    list_select_related = (
        "raised_by",
        "against_worker__user",
        "against_resident__user",
    )
    inlines = [ComplaintUpdateInline]

    # The deadline, the escalation and the reference are all set by the system.
    # Letting them be edited by hand would make the SLA statistics in 11.4
    # describe whatever somebody last typed rather than what actually happened.
    readonly_fields = (
        "reference",
        "raised_by",
        "sla_due_at",
        "escalated_at",
        "first_response_at",
        "resolved_at",
        "resolved_by",
        "payment_dispute",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "reference",
                    "society",
                    "raised_by",
                    ("category", "priority", "status"),
                    "subject",
                    "description",
                    "photo",
                )
            },
        ),
        ("Who it is about", {"fields": ("against_worker", "against_resident")}),
        (
            "Response",
            {
                "fields": (
                    "assigned_to",
                    "resolution",
                    "resolved_by",
                    "resolved_at",
                )
            },
        ),
        (
            "SLA",
            {
                "fields": (
                    "sla_due_at",
                    "first_response_at",
                    "escalated_at",
                    "payment_dispute",
                )
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        """Complaints are raised by the people they affect, through the app.

        A complaint typed in here would have no raiser to notify and no history
        entry explaining where it came from.
        """
        return False

    @admin.display(description="About")
    def about(self, obj):
        return obj.subject_label

    @admin.display(description="SLA")
    def sla_state(self, obj):
        if not obj.is_open:
            return format_html('<span style="color:#666">closed</span>')
        remaining = obj.hours_remaining
        if remaining < 0:
            return format_html(
                '<span style="color:#c62828;font-weight:600">{} h over</span>',
                f"{abs(remaining):.0f}",
            )
        return format_html('<span style="color:#2e7d32">{} h left</span>', f"{remaining:.0f}")


@admin.register(UnmetDemand)
class UnmetDemandAdmin(SocietyScopedAdminMixin, admin.ModelAdmin):
    """11.4 — demand nobody could fill, as a recruiting brief."""

    list_display = ("created_at", "kind", "service_label", "requested_date", "detail")
    list_filter = ("kind", "created_at")
    search_fields = ("service_label", "detail")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        """A log, not a record to curate. Read-only for everybody."""
        return False
