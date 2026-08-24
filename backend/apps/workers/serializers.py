"""
Module 3 — Worker Onboarding & KYC: serializers.

-------------------------------------------------------------------------------
NO SERIALIZER HERE MAY EXPOSE A FULL AADHAAR NUMBER
-------------------------------------------------------------------------------
The model does not store one (see ``apps/workers/models.py``), so the only way
one could leak is if a serializer echoed back something a client posted.
``KycManualEntrySerializer`` therefore accepts an Aadhaar number as a
**write-only** field, hands it to the service layer to be hashed, and never
returns it. Everything read-facing exposes ``masked_aadhaar`` instead.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import ConsentPurpose, ConsentRecord, KycDocument, ServiceType, WorkerProfile
from .ocr import is_valid_aadhaar, normalise_aadhaar


class ServiceTypeSerializer(serializers.ModelSerializer):
    """The catalogue a worker picks from, and Module 4/5 filter on.

    Defined here rather than in the modules that consume it: ``ServiceType`` is
    Module 3's model, and one shared projection is what stops the hiring and
    booking APIs from drifting into two different shapes for the same row.
    """

    class Meta:
        model = ServiceType
        fields = ["id", "name", "slug", "icon", "description"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# 3.1 Worker profile
# ---------------------------------------------------------------------------


class WorkerProfileSerializer(serializers.ModelSerializer):
    """The worker's own profile, as they see it."""

    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    is_approved = serializers.BooleanField(source="user.is_approved", read_only=True)
    service_types = ServiceTypeSerializer(many=True, read_only=True)

    # Scores are DecimalFields, which DRF renders as strings by default — the
    # wrong shape for a number the client does arithmetic on. See the same fix
    # in apps/hiring/serializers.py.
    trust_score = serializers.FloatField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)

    is_searchable = serializers.BooleanField(read_only=True)
    kyc_status = serializers.SerializerMethodField()

    class Meta:
        model = WorkerProfile
        fields = [
            "id",
            "full_name",
            "phone_number",
            "photo",
            "service_types",
            "years_of_experience",
            "bio",
            "languages_spoken",
            "expected_monthly_rate",
            "is_available",
            "available_from",
            "available_until",
            "trust_score",
            "average_rating",
            "completed_engagements",
            "is_approved",
            "is_searchable",
            "kyc_status",
            "reviewed_at",
            "rejection_reason",
            "created_at",
        ]
        read_only_fields = fields

    def get_kyc_status(self, obj) -> str | None:
        kyc = obj.latest_kyc
        return kyc.status if kyc else None


class WorkerProfileWriteSerializer(serializers.ModelSerializer):
    """Module 3.1 — what the worker enters about themselves.

    Deliberately excludes ``trust_score``, ``average_rating`` and
    ``completed_engagements``: those are computed by Modules 9 and 4, and a
    worker who could PATCH their own trust score would make the whole rating
    system meaningless.
    """

    service_types = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=ServiceType.objects.filter(is_active=True),
        required=False,
    )

    class Meta:
        model = WorkerProfile
        # ``expected_monthly_rate`` is absent on purpose. The platform quotes one
        # rate (apps.core.pricing), so there is nothing here for a worker to set;
        # the column keeps its value from the model default and stays readable
        # through WorkerProfileSerializer.
        fields = [
            "photo",
            "service_types",
            "years_of_experience",
            "bio",
            "languages_spoken",
            "is_available",
            "available_from",
            "available_until",
        ]

    def validate(self, attrs):
        start = attrs.get("available_from", getattr(self.instance, "available_from", None))
        end = attrs.get("available_until", getattr(self.instance, "available_until", None))

        if (start is None) != (end is None):
            raise serializers.ValidationError(
                {"available_from": "Give both a start and an end time, or neither."}
            )
        if start and end and start >= end:
            raise serializers.ValidationError(
                {"available_until": "The end time must be after the start time."}
            )
        return attrs


# ---------------------------------------------------------------------------
# 3.2 / 3.3 KYC
# ---------------------------------------------------------------------------


class KycDocumentSerializer(serializers.ModelSerializer):
    """A KYC attempt and everything the pipeline made of it.

    ``masked_aadhaar`` is the only Aadhaar representation exposed anywhere.
    """

    masked_aadhaar = serializers.CharField(read_only=True)
    needs_manual_confirmation = serializers.BooleanField(read_only=True)

    class Meta:
        model = KycDocument
        fields = [
            "id",
            "status",
            "error_message",
            "extracted_name",
            "extracted_dob",
            "extracted_gender",
            "masked_aadhaar",
            "aadhaar_checksum_valid",
            "extracted_age",
            "is_minor",
            "ocr_engine",
            "mean_confidence",
            "low_confidence_fields",
            "needs_manual_confirmation",
            "cross_check",
            "has_mismatch",
            "ocr_summary",
            "processed_at",
            "created_at",
        ]
        read_only_fields = fields


class KycUploadSerializer(serializers.Serializer):
    """Module 3.2 / 3.6 — the Aadhaar upload, with consent captured alongside it.

    Consent is part of *this* request rather than a separate earlier call
    because India's DPDP Act 2023 requires it at the point of collection. A
    worker who does not tick it does not get their document processed, and the
    request is refused before the file is stored.
    """

    document = serializers.FileField()
    consent = serializers.BooleanField(
        help_text="Explicit consent to process this Aadhaar document (Module 3.6)."
    )

    # Optional: what the worker typed at registration, enabling the Stage 8
    # cross-check between the form and the card.
    form_name = serializers.CharField(required=False, allow_blank=True, default="")
    form_dob = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_consent(self, value):
        if not value:
            raise serializers.ValidationError(
                "We cannot process your Aadhaar document without your consent."
            )
        return value

    def validate_document(self, uploaded):
        from django.conf import settings

        ocr_settings = getattr(settings, "OCR_SETTINGS", {})
        max_bytes = ocr_settings.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)

        if uploaded.size > max_bytes:
            raise serializers.ValidationError(
                f"That file is too large. The limit is {max_bytes // (1024 * 1024)} MB."
            )
        return uploaded

    def form_data(self) -> dict[str, str]:
        """What Stage 8 cross-checks the card against. Empty means skip Stage 8."""
        data = {
            "name": self.validated_data.get("form_name", ""),
            "dob": self.validated_data.get("form_dob", ""),
        }
        return {k: v for k, v in data.items() if v}


class KycManualEntrySerializer(serializers.Serializer):
    """Module 3.2 — the worker confirms or corrects the pre-filled fields.

    Also the manual-entry fallback SRS 2.5 requires: when OCR fails outright,
    this is how a worker still completes onboarding by typing the fields in.
    """

    name = serializers.CharField(required=False, allow_blank=True, default="", max_length=120)
    dob = serializers.CharField(required=False, allow_blank=True, default="", max_length=20)
    gender = serializers.CharField(required=False, allow_blank=True, default="", max_length=20)

    #: Write-only, always. It is hashed by the service layer and never returned.
    aadhaar_number = serializers.CharField(
        required=False, allow_blank=True, default="", write_only=True, max_length=20
    )

    def validate_aadhaar_number(self, value):
        if not value:
            return value

        digits = normalise_aadhaar(value)
        if len(digits) != 12:
            raise serializers.ValidationError("An Aadhaar number has 12 digits.")
        if not is_valid_aadhaar(value):
            # The Verhoeff checksum catches a mistyped or misread digit before
            # it becomes a de-duplication key that matches nobody.
            raise serializers.ValidationError(
                "That Aadhaar number is not valid — please check the digits."
            )
        return value

    def validate(self, attrs):
        if not any(
            attrs.get(field)
            for field in ("name", "dob", "gender", "aadhaar_number")
        ):
            raise serializers.ValidationError(
                "Provide at least one field to confirm or correct."
            )
        return attrs


# ---------------------------------------------------------------------------
# 3.6 Consent
# ---------------------------------------------------------------------------


class ConsentRecordSerializer(serializers.ModelSerializer):
    purpose_display = serializers.CharField(source="get_purpose_display", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ConsentRecord
        fields = [
            "id",
            "purpose",
            "purpose_display",
            "granted",
            "is_active",
            "granted_at",
            "withdrawn_at",
            "policy_version",
        ]
        read_only_fields = fields


class ConsentGrantSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=ConsentPurpose.choices)
    policy_version = serializers.CharField(required=False, default="1.0", max_length=20)


# ---------------------------------------------------------------------------
# 3.5 Admin review
# ---------------------------------------------------------------------------


class DuplicateWorkerSerializer(serializers.Serializer):
    """Another worker holding the same Aadhaar hash. Response shape only."""

    worker_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    society = serializers.CharField(read_only=True)


class WorkerReviewSerializer(serializers.ModelSerializer):
    """What an administrator sees in the approval queue.

    Carries the latest KYC attempt, everything blocking approval, and any
    duplicate — so the decision can be made from one payload rather than by
    clicking through to three screens.
    """

    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    is_approved = serializers.BooleanField(source="user.is_approved", read_only=True)
    service_types = ServiceTypeSerializer(many=True, read_only=True)

    latest_kyc = serializers.SerializerMethodField()
    approval_blockers = serializers.SerializerMethodField()
    duplicate_of = serializers.SerializerMethodField()

    class Meta:
        model = WorkerProfile
        fields = [
            "id",
            "full_name",
            "phone_number",
            "photo",
            "service_types",
            "years_of_experience",
            "bio",
            "languages_spoken",
            "is_approved",
            "latest_kyc",
            "approval_blockers",
            "duplicate_of",
            "reviewed_at",
            "rejection_reason",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(KycDocumentSerializer(allow_null=True))
    def get_latest_kyc(self, obj):
        kyc = obj.latest_kyc
        return KycDocumentSerializer(kyc).data if kyc else None

    def get_approval_blockers(self, obj) -> list[str]:
        from .services import approval_blockers

        return approval_blockers(obj)

    @extend_schema_field(DuplicateWorkerSerializer(allow_null=True))
    def get_duplicate_of(self, obj):
        from .services import duplicate_warning

        duplicate = duplicate_warning(obj)
        if duplicate is None:
            return None
        return {
            "worker_id": duplicate.worker_id,
            "name": duplicate.worker.user.get_full_name(),
            "society": str(duplicate.worker.user.society) if duplicate.worker.user.society else "",
        }


class WorkerDecisionSerializer(serializers.Serializer):
    """Module 3.5 — approve, or reject with a reason the worker can act on."""

    approve = serializers.BooleanField()
    rejection_reason = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=500
    )

    def validate(self, attrs):
        if not attrs["approve"] and not attrs.get("rejection_reason", "").strip():
            raise serializers.ValidationError(
                {"rejection_reason": "Tell the worker what to correct."}
            )
        return attrs
