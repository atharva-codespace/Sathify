"""
Module 9 — Ratings, Reviews & Trust Score: Django Admin.

``TrustScoreLog`` is strictly read-only. It exists so a disputed score can be
answered months later, and an editable audit trail answers nothing.

Ratings are editable only through the flag workflow. An administrator quietly
changing somebody's stars would corrupt every score computed from them, with no
record that it happened — which is the failure this module's log exists to
prevent.
"""

from django.contrib import admin, messages

from .models import (
    FlagStatus,
    Rating,
    ReviewFlag,
    ReviewSentiment,
    TrustScoreLog,
)
from .services import recompute_worker_trust, resolve_flag


class ReviewSentimentInline(admin.StackedInline):
    """Module 9.2 — the model's reading, alongside the text it read.

    Read-only: this is output, and correcting it by hand would leave the stored
    verdict disagreeing with the engine that supposedly produced it.
    """

    model = ReviewSentiment
    extra = 0
    can_delete = False
    fields = ("label", "polarity", "confidence", "themes", "detected_language", "engine")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    """Module 9.1. Read-mostly — see the module docstring."""

    list_display = (
        "created_at",
        "direction",
        "stars",
        "worker",
        "resident",
        "rater",
        "is_flagged",
        "is_withheld",
    )
    list_filter = ("direction", "stars", "is_flagged", "is_withheld", "society")
    search_fields = (
        "review",
        "worker__user__first_name",
        "worker__user__last_name",
        "resident__user__first_name",
        "resident__user__last_name",
    )
    date_hierarchy = "created_at"
    list_select_related = ("worker__user", "resident__user", "rater")
    raw_id_fields = ("worker", "resident", "rater", "engagement", "booking", "society")
    inlines = [ReviewSentimentInline]

    def get_readonly_fields(self, request, obj=None):
        # Everything except the two flag columns, which the flag workflow owns
        # and which an administrator may legitimately need to correct directly.
        return [
            field.name
            for field in self.model._meta.fields
            if field.name not in {"is_flagged", "is_withheld"}
        ]

    def has_add_permission(self, request):
        # Ratings come from the people who did the work.
        return False


@admin.register(TrustScoreLog)
class TrustScoreLogAdmin(admin.ModelAdmin):
    """Module 9.3 — the record that makes a score defensible. Never editable.

    Each row holds the breakdown **as it was**, which is the whole point: today's
    recomputation would give today's answer, not the number anyone acted on.
    """

    list_display = (
        "created_at",
        "subject_type",
        "subject",
        "previous_score",
        "new_score",
        "change",
        "trigger",
    )
    list_filter = ("subject_type", "society", "trigger")
    search_fields = (
        "worker__user__first_name",
        "worker__user__last_name",
        "resident__user__first_name",
        "resident__user__last_name",
        "trigger",
    )
    date_hierarchy = "created_at"
    list_select_related = ("worker__user", "resident__user")
    raw_id_fields = ("worker", "resident", "society")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields] + ["change"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Subject")
    def subject(self, obj):
        return obj.worker or obj.resident

    @admin.display(description="Change")
    def change(self, obj):
        delta = obj.delta
        return f"{'+' if delta >= 0 else ''}{delta}"


@admin.register(ReviewFlag)
class ReviewFlagAdmin(admin.ModelAdmin):
    """Module 9.4 — suspicious ratings awaiting a human.

    The actions below go through the service layer rather than editing the row,
    so dismissing a flag also restores the rating to scoring and recomputes the
    subject's score. Flipping the status by hand would clear the flag and leave
    the penalty in place.
    """

    list_display = ("created_at", "reason", "status", "rating", "detail")
    list_filter = ("status", "reason", "society")
    search_fields = ("detail", "review_note")
    date_hierarchy = "created_at"
    list_select_related = ("rating",)
    raw_id_fields = ("rating", "reviewed_by", "society")
    readonly_fields = ("reviewed_by", "reviewed_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        # Flags come from the detection heuristics, not from a form.
        return False

    @admin.action(description="Dismiss — the rating is genuine")
    def dismiss_flags(self, request, queryset):
        cleared = sum(
            1
            for flag in queryset.filter(status=FlagStatus.OPEN)
            if resolve_flag(
                flag, upheld=False, by=request.user, note="Dismissed from admin"
            )
        )
        self.message_user(
            request,
            f"{cleared} rating(s) restored and counted again.",
            messages.SUCCESS,
        )

    @admin.action(description="Uphold — keep the rating withheld")
    def uphold_flags(self, request, queryset):
        upheld = sum(
            1
            for flag in queryset.filter(status=FlagStatus.OPEN)
            if resolve_flag(
                flag, upheld=True, by=request.user, note="Upheld from admin"
            )
        )
        self.message_user(request, f"{upheld} rating(s) kept withheld.", messages.WARNING)

    actions = ["dismiss_flags", "uphold_flags"]
