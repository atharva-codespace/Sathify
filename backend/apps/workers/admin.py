"""
Module 3 — Worker Onboarding & KYC: Django Admin.

Module 3.5 specifies the review as a Django Admin screen where an administrator
sees the uploaded photo and the OCR-extracted fields **side by side** and
approves or rejects with a reason. That is what ``WorkerProfileAdmin.review_panel``
renders: profile photo next to Aadhaar scan, extracted fields next to the
cross-check verdicts, and every reason approval is blocked.

The panel is read-only and the extracted fields are not editable here. The OCR
output is evidence of what the document said; if it was misread, the worker
corrects it through the app (``KycConfirmView``) so the correction is attributed
to them. An administrator silently retyping someone's Aadhaar details would
destroy exactly the audit trail this module exists to create.
"""

from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html, format_html_join

from .models import ConsentRecord, KycDocument, KycStatus, ServiceType, WorkerProfile
from .services import approval_blockers, approve_worker, duplicate_warning, reject_worker
from .services import WorkerError


@admin.register(ServiceType)
class ServiceTypeAdmin(admin.ModelAdmin):
    """The catalogue workers pick from, and Modules 4 and 5 filter on.

    Nothing is seeded: what a society offers is an administrative decision
    (modspec 2.5), so the rows are entered here.
    """

    list_display = ("name", "slug", "is_active", "worker_count")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_worker_count=Count("workers"))

    @admin.display(description="Workers", ordering="_worker_count")
    def worker_count(self, obj):
        return obj._worker_count


class KycDocumentInline(admin.TabularInline):
    """Every attempt, newest first — a re-upload after a poor scan is history."""

    model = KycDocument
    extra = 0
    can_delete = False
    fields = (
        "created_at",
        "status",
        "extracted_name",
        "extracted_dob",
        "masked_aadhaar",
        "aadhaar_checksum_valid",
        "extracted_age",
        "is_minor",
        "ocr_engine",
        "mean_confidence",
    )
    readonly_fields = fields

    def masked_aadhaar(self, obj):
        return obj.masked_aadhaar

    def has_add_permission(self, request, obj=None):
        # Documents arrive from the worker's app, never from here.
        return False


@admin.register(WorkerProfile)
class WorkerProfileAdmin(admin.ModelAdmin):
    """Module 3.5 — the verification and activation gate."""

    list_display = (
        "__str__",
        "society",
        "is_approved",
        "kyc_state",
        "has_photo",
        "trust_score",
        "created_at",
    )
    list_filter = ("user__is_approved", "is_available", "user__society", "service_types")
    search_fields = (
        "user__first_name",
        "user__last_name",
        "user__phone_number",
    )
    date_hierarchy = "created_at"
    list_select_related = ("user", "user__society")
    filter_horizontal = ("service_types",)
    inlines = [KycDocumentInline]

    readonly_fields = (
        "review_panel",
        "created_at",
        "updated_at",
        "reviewed_at",
        "reviewed_by",
        "trust_score",
        "average_rating",
        "completed_engagements",
    )
    fieldsets = (
        ("Review", {"fields": ("review_panel",)}),
        ("Worker", {"fields": ("user", "photo", "service_types", "bio")}),
        (
            "Details",
            {
                "fields": (
                    "years_of_experience",
                    "languages_spoken",
                    "expected_monthly_rate",
                    "is_available",
                    "available_from",
                    "available_until",
                )
            },
        ),
        (
            "Computed by other modules",
            {
                "fields": ("trust_score", "average_rating", "completed_engagements"),
                "description": "Written by Modules 4 and 9. Shown for context only.",
            },
        ),
        (
            "Review trail",
            {"fields": ("reviewed_at", "reviewed_by", "rejection_reason")},
        ),
    )

    @admin.display(boolean=True, description="Approved", ordering="user__is_approved")
    def is_approved(self, obj):
        return obj.user.is_approved

    @admin.display(description="Society", ordering="user__society")
    def society(self, obj):
        return obj.user.society

    @admin.display(boolean=True, description="Photo")
    def has_photo(self, obj):
        return bool(obj.photo)

    @admin.display(description="KYC")
    def kyc_state(self, obj):
        kyc = obj.latest_kyc
        if kyc is None:
            return "—"
        if kyc.is_minor:
            return "UNDER 18"
        return kyc.get_status_display()

    # --- The side-by-side review panel (Module 3.5) ------------------------

    @admin.display(description="Side-by-side review")
    def review_panel(self, obj):
        if obj.pk is None:
            return "Save the worker first."

        kyc = obj.latest_kyc
        blockers = approval_blockers(obj)
        duplicate = duplicate_warning(obj)

        return format_html(
            '<div style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start">'
            "{images}{fields}</div>{verdicts}",
            images=self._images_block(obj, kyc),
            fields=self._fields_block(kyc),
            verdicts=self._verdicts_block(kyc, blockers, duplicate),
        )

    def _images_block(self, obj, kyc):
        """Profile photo and Aadhaar scan, the two things being compared."""
        photo = (
            format_html(
                '<img src="{}" style="max-width:220px;border:1px solid #ccc;'
                'border-radius:6px" alt="Profile photo">',
                obj.photo.url,
            )
            if obj.photo
            else format_html('<em style="color:#b00">No profile photo</em>')
        )

        document = (
            format_html(
                '<a href="{0}" target="_blank" rel="noopener">'
                '<img src="{0}" style="max-width:320px;border:1px solid #ccc;'
                'border-radius:6px" alt="Aadhaar document"></a>',
                kyc.document_image.url,
            )
            if kyc and kyc.document_image
            else format_html('<em style="color:#b00">No document uploaded</em>')
        )

        return format_html(
            '<div><h3 style="margin:0 0 6px">Photo</h3>{}</div>'
            '<div><h3 style="margin:0 0 6px">Aadhaar document</h3>{}'
            '<div style="font-size:11px;color:#666">Click to open full size</div></div>',
            photo,
            document,
        )

    def _fields_block(self, kyc):
        """What OCR read, with the confidence-flagged fields marked."""
        if kyc is None:
            return format_html('<div><em>Nothing extracted yet.</em></div>')

        low = set(kyc.low_confidence_fields or [])

        def cell(key, label, value):
            if key in low:
                # The threshold flagged this as too poorly read to trust, which
                # is exactly what the administrator's eye is here for.
                return format_html(
                    "<tr><th style='text-align:left;padding:2px 12px 2px 0'>{}</th>"
                    "<td style='background:#fff3cd;padding:2px 6px'>{} "
                    "<span style='color:#856404;font-size:11px'>(low confidence)</span>"
                    "</td></tr>",
                    label,
                    value or "—",
                )
            return format_html(
                "<tr><th style='text-align:left;padding:2px 12px 2px 0'>{}</th>"
                "<td style='padding:2px 6px'>{}</td></tr>",
                label,
                value or "—",
            )

        rows = format_html_join(
            "",
            "{}",
            (
                (cell("name", "Name", kyc.extracted_name),),
                (cell("dob", "Date of birth", kyc.extracted_dob),),
                (cell("gender", "Gender", kyc.extracted_gender),),
                (cell("aadhaar", "Aadhaar", kyc.masked_aadhaar),),
                (cell("", "Age", kyc.extracted_age),),
                (cell("", "OCR engine", kyc.ocr_engine),),
                (cell("", "Mean confidence", f"{kyc.mean_confidence:.2f}"),),
            ),
        )

        return format_html(
            '<div><h3 style="margin:0 0 6px">Extracted fields</h3>'
            "<table>{}</table></div>",
            rows,
        )

    def _verdicts_block(self, kyc, blockers, duplicate):
        """Checksum, age gate, cross-check, duplicates, and what blocks approval."""
        parts = []

        if kyc is not None:
            parts.append(
                self._badge(
                    "Aadhaar checksum",
                    "valid" if kyc.aadhaar_checksum_valid else "FAILED",
                    kyc.aadhaar_checksum_valid,
                )
            )
            parts.append(
                self._badge(
                    "Age gate",
                    "under 18 — automatic rejection" if kyc.is_minor else "over 18",
                    not kyc.is_minor,
                )
            )
            if kyc.cross_check:
                parts.append(
                    self._badge(
                        "Form cross-check",
                        "mismatch against what the worker typed"
                        if kyc.has_mismatch
                        else "matches the registration form",
                        not kyc.has_mismatch,
                    )
                )
            if kyc.status == KycStatus.FAILED:
                parts.append(
                    self._badge("OCR", kyc.error_message or "failed", False)
                )

        if duplicate is not None:
            parts.append(
                self._badge(
                    "Duplicate Aadhaar",
                    f"already registered as {duplicate.worker.user.get_full_name()} "
                    f"({duplicate.worker.user.society or 'unknown society'}) — "
                    "the same person moving societies looks identical to a "
                    "double registration, so check before deciding",
                    False,
                )
            )

        if blockers:
            items = format_html_join(
                "", "<li>{}</li>", ((b,) for b in blockers)
            )
            parts.append(
                format_html(
                    '<div style="margin-top:10px;padding:10px;background:#f8d7da;'
                    'border-radius:6px"><strong>Cannot approve yet:</strong>'
                    "<ul style='margin:6px 0 0 18px'>{}</ul></div>",
                    items,
                )
            )
        else:
            parts.append(
                format_html(
                    '<div style="margin-top:10px;padding:10px;background:#d4edda;'
                    'border-radius:6px"><strong>Ready to approve.</strong> Use the '
                    "action on the worker list, or the buttons below.</div>"
                )
            )

        return format_html(
            '<div style="margin-top:18px;max-width:760px">{}</div>',
            format_html_join("", "{}", ((p,) for p in parts)),
        )

    @staticmethod
    def _badge(label, text, ok):
        return format_html(
            '<div style="margin:4px 0"><strong>{}:</strong> '
            '<span style="color:{}">{}</span></div>',
            label,
            "#155724" if ok else "#b00",
            text,
        )

    # --- Actions -----------------------------------------------------------

    @admin.action(description="Approve selected workers")
    def approve_workers(self, request, queryset):
        approved = 0
        for worker in queryset.select_related("user"):
            try:
                approve_worker(worker, reviewed_by=request.user)
                approved += 1
            except WorkerError as exc:
                # Reported per worker rather than aborting the batch: an
                # administrator working a queue should not lose nine approvals
                # because the tenth was incomplete.
                self.message_user(request, f"{worker}: {exc}", messages.WARNING)

        if approved:
            self.message_user(request, f"{approved} worker(s) approved.", messages.SUCCESS)

    @admin.action(description="Reject selected workers")
    def reject_workers(self, request, queryset):
        """Rejects with a placeholder reason.

        A useful rejection reason is specific to the worker, so the real flow is
        the API's decide endpoint where the administrator types one. This exists
        for bulk clean-up.
        """
        rejected = 0
        for worker in queryset.select_related("user"):
            reject_worker(
                worker,
                reason="Rejected from the admin. Please contact your society office.",
                reviewed_by=request.user,
            )
            rejected += 1

        self.message_user(request, f"{rejected} worker(s) rejected.", messages.WARNING)

    actions = ["approve_workers", "reject_workers"]


@admin.register(KycDocument)
class KycDocumentAdmin(admin.ModelAdmin):
    """Read-only. Documents arrive from the worker's app and are never edited."""

    list_display = (
        "id",
        "worker",
        "status",
        "masked_aadhaar",
        "aadhaar_checksum_valid",
        "extracted_age",
        "is_minor",
        "ocr_engine",
        "created_at",
    )
    list_filter = ("status", "is_minor", "aadhaar_checksum_valid", "has_mismatch")
    search_fields = (
        "worker__user__first_name",
        "worker__user__last_name",
        "worker__user__phone_number",
        "extracted_name",
    )
    date_hierarchy = "created_at"
    list_select_related = ("worker__user",)
    raw_id_fields = ("worker",)

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields] + ["masked_aadhaar"]

    @admin.display(description="Aadhaar")
    def masked_aadhaar(self, obj):
        return obj.masked_aadhaar

    def has_add_permission(self, request):
        return False


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    """Module 3.6 — the DPDP audit trail. Append-only by design.

    Consent records are evidence of what someone agreed to and when. Editing one
    after the fact would make the trail worthless, so everything is read-only and
    withdrawal happens through the API, which timestamps it.
    """

    list_display = ("user", "purpose", "granted", "granted_at", "withdrawn_at", "policy_version")
    list_filter = ("purpose", "granted", "policy_version")
    search_fields = ("user__first_name", "user__last_name", "user__phone_number")
    date_hierarchy = "granted_at"
    list_select_related = ("user",)
    raw_id_fields = ("user",)

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
