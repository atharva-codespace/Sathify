"""Module 11 — Admin, Reporting & Complaints: serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.core.files import PhotoTooLarge, validate_photo

from .models import (
    Complaint,
    ComplaintCategory,
    ComplaintStatus,
    ComplaintUpdate,
    UnmetDemand,
)

#: The statuses an administrator may close a complaint with. ``WITHDRAWN`` is
#: absent on purpose — only the person who raised it can withdraw it, through
#: its own endpoint.
ADMIN_CLOSING_STATUSES = [ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED]


class ComplaintUpdateSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = ComplaintUpdate
        fields = [
            "id",
            "note",
            "author_name",
            "old_status",
            "new_status",
            "is_system",
            "is_internal",
            "created_at",
        ]
        read_only_fields = fields

    def get_author_name(self, obj) -> str:
        if obj.is_system or obj.author is None:
            return "Sathify"
        return obj.author.get_full_name() or obj.author.phone_number


class ComplaintSerializer(serializers.ModelSerializer):
    """One complaint, as either side sees it.

    The SLA fields are exposed to everybody, not just administrators. A person
    who raised a complaint is entitled to know what response time was promised
    and whether it was met — publishing the deadline is what makes it a
    commitment rather than an internal metric.
    """

    category_display = serializers.CharField(source="get_category_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)

    raised_by_name = serializers.SerializerMethodField()
    about = serializers.CharField(source="subject_label", read_only=True)

    is_open = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    hours_remaining = serializers.FloatField(read_only=True)
    age_active_hours = serializers.FloatField(read_only=True)

    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Complaint
        fields = [
            "id",
            "reference",
            "category",
            "category_display",
            "subject",
            "description",
            "photo_url",
            "priority",
            "priority_display",
            "status",
            "status_display",
            # The raiser's user id, not just their name. Only they may withdraw
            # a complaint, and the app needs to compare ids to decide whether to
            # offer the button — matching on display name would offer it to the
            # wrong person the first time two residents share a name.
            "raised_by",
            "raised_by_name",
            "about",
            "against_worker",
            "against_resident",
            "sla_due_at",
            "escalated_at",
            "first_response_at",
            "resolution",
            "resolved_at",
            "is_open",
            "is_overdue",
            "hours_remaining",
            "age_active_hours",
            "payment_dispute",
            "created_at",
        ]
        read_only_fields = fields

    def get_raised_by_name(self, obj) -> str:
        return obj.raised_by.get_full_name() or obj.raised_by.phone_number

    def get_photo_url(self, obj) -> str | None:
        if not obj.photo:
            return None
        request = self.context.get("request")
        url = obj.photo.url
        return request.build_absolute_uri(url) if request else url


class ComplaintDetailSerializer(ComplaintSerializer):
    """A complaint with its history.

    Internal notes are filtered out for anyone who is not an administrator —
    the whole point of marking a note internal is that the other party does not
    read it.
    """

    updates = serializers.SerializerMethodField()

    class Meta(ComplaintSerializer.Meta):
        fields = ComplaintSerializer.Meta.fields + ["updates"]
        read_only_fields = fields

    def get_updates(self, obj) -> list:
        entries = obj.updates.all()
        request = self.context.get("request")
        is_admin = bool(
            request and getattr(request.user, "is_society_admin", False)
        )
        if not is_admin:
            entries = [entry for entry in entries if not entry.is_internal]
        return ComplaintUpdateSerializer(entries, many=True).data


class RaiseComplaintSerializer(serializers.Serializer):
    """Module 11.3 — what a resident or worker submits.

    Priority is deliberately not accepted from the client. It is derived from
    the category (``services.default_priority_for``), because a field labelled
    "how urgent is this?" makes everything urgent inside a week.
    """

    category = serializers.ChoiceField(choices=ComplaintCategory.choices)
    subject = serializers.CharField(max_length=150)
    description = serializers.CharField(max_length=2000)

    against_worker = serializers.IntegerField(required=False, allow_null=True)
    against_resident = serializers.IntegerField(required=False, allow_null=True)

    photo = serializers.ImageField(required=False, allow_null=True)

    def validate_photo(self, uploaded):
        if uploaded is None:
            return uploaded
        try:
            return validate_photo(uploaded)
        except PhotoTooLarge as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        if attrs.get("against_worker") and attrs.get("against_resident"):
            raise serializers.ValidationError(
                {
                    "against_worker": (
                        "A complaint is about one party. Raise separate "
                        "complaints if both are involved."
                    )
                }
            )
        return attrs


class AddUpdateSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=2000)
    is_internal = serializers.BooleanField(
        default=False,
        help_text="Administrators only. Hidden from the person who raised it.",
    )


class CloseComplaintSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ADMIN_CLOSING_STATUSES)
    resolution = serializers.CharField(
        max_length=2000,
        help_text="Required for both outcomes. A rejection with no reason is "
        "the one most likely to be disputed.",
    )


class UnmetDemandSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = UnmetDemand
        fields = [
            "id",
            "kind",
            "kind_display",
            "service_label",
            "requested_date",
            "requested_time",
            "detail",
            "created_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# 11.1 Directory
# ---------------------------------------------------------------------------


class DirectoryWorkerSerializer(serializers.Serializer):
    """One row of the worker directory.

    A plain serializer rather than a ModelSerializer: this reads across
    ``WorkerProfile``, its user, and Module 9's counters, and spelling the
    shape out here keeps the directory's contract independent of Module 3's
    profile model as that evolves.
    """

    id = serializers.IntegerField(read_only=True)
    full_name = serializers.SerializerMethodField()
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    is_approved = serializers.BooleanField(source="user.is_approved", read_only=True)
    is_available = serializers.BooleanField(read_only=True)
    services = serializers.SerializerMethodField()
    years_of_experience = serializers.IntegerField(read_only=True)
    trust_score = serializers.FloatField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    rating_count = serializers.IntegerField(read_only=True)
    completed_engagements = serializers.IntegerField(read_only=True)
    open_complaints = serializers.IntegerField(read_only=True, default=0)
    joined_at = serializers.DateTimeField(source="created_at", read_only=True)

    def get_full_name(self, obj) -> str:
        return obj.user.get_full_name() or obj.user.phone_number

    def get_services(self, obj) -> list[str]:
        return [service.name for service in obj.service_types.all()]


class DirectoryResidentSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    full_name = serializers.SerializerMethodField()
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    is_approved = serializers.BooleanField(source="user.is_approved", read_only=True)
    flat = serializers.SerializerMethodField()
    relationship = serializers.CharField(read_only=True)
    is_primary = serializers.BooleanField(read_only=True)
    trust_score = serializers.FloatField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    rating_count = serializers.IntegerField(read_only=True)
    open_complaints = serializers.IntegerField(read_only=True, default=0)
    joined_at = serializers.DateTimeField(source="created_at", read_only=True)

    def get_full_name(self, obj) -> str:
        return obj.user.get_full_name() or obj.user.phone_number

    def get_flat(self, obj) -> str:
        return str(obj.flat)


# ---------------------------------------------------------------------------
# 11.2 / 11.4 — response shapes, declared for the schema
# ---------------------------------------------------------------------------


class ReportQuerySerializer(serializers.Serializer):
    start = serializers.DateField()
    end = serializers.DateField()

    def validate(self, attrs):
        if attrs["end"] < attrs["start"]:
            raise serializers.ValidationError(
                {"end": "The end of the period cannot be before its start."}
            )
        return attrs


class ReportSerializer(serializers.Serializer):
    """The JSON form of a report — identical to what the files render from."""

    title = serializers.CharField(read_only=True)
    society_name = serializers.CharField(read_only=True)
    period_start = serializers.DateField(read_only=True)
    period_end = serializers.DateField(read_only=True)
    period_label = serializers.CharField(read_only=True)
    columns = serializers.ListField(child=serializers.CharField(), read_only=True)
    rows = serializers.ListField(read_only=True)
    summary = serializers.ListField(read_only=True)
    row_count = serializers.IntegerField(read_only=True)


class DashboardSerializer(serializers.Serializer):
    """Module 11.4. Panels are declared loosely on purpose.

    Each panel is a self-describing dict assembled in :mod:`analytics`, and
    mirroring those structures field-by-field here would mean editing two files
    for every change with nothing checking they agree.
    """

    period_start = serializers.DateField(read_only=True)
    period_end = serializers.DateField(read_only=True)
    sentiment = serializers.DictField(read_only=True)
    trust = serializers.DictField(read_only=True)
    complaints = serializers.DictField(read_only=True)
    unmet_demand = serializers.DictField(read_only=True)
    availability = serializers.DictField(read_only=True)
