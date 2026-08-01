"""
Module 12 — AI Layer: admin.

Both screens are read-only. The request log is evidence about how the four-tier
chain is behaving, and an editable audit trail is not evidence; the usage
counters are enforcement, and hand-editing one would hand a tier quota it does
not have.
"""

from django.contrib import admin

from .models import AiRequestLog, AiUsageCounter


@admin.register(AiRequestLog)
class AiRequestLogAdmin(admin.ModelAdmin):
    """How often the chain actually falls past Tier 1.

    The whole four-tier design is a bet on free ceilings holding, and provider
    dashboards cannot answer this — each only sees its own traffic.
    """

    list_display = (
        "created_at",
        "feature",
        "outcome",
        "tier",
        "fell_through",
        "latency_ms",
        "error",
    )
    list_filter = ("feature", "outcome", "tier", "created_at")
    search_fields = ("error",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)

    readonly_fields = (
        "feature",
        "outcome",
        "tier",
        "tiers_attempted",
        "latency_ms",
        "prompt_chars",
        "response_chars",
        "error",
        "user",
        "society",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Fell through", boolean=True)
    def fell_through(self, obj):
        return obj.fell_through


@admin.register(AiUsageCounter)
class AiUsageCounterAdmin(admin.ModelAdmin):
    """Live rate-limit state, chiefly for Tier 3's 50-a-day ceiling."""

    list_display = ("tier", "window", "bucket", "count")
    list_filter = ("tier", "window")
    ordering = ("-bucket", "tier")
    readonly_fields = ("tier", "window", "bucket", "count")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
