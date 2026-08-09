"""Module 5 — One-Day Service Booking: serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.hiring.serializers import WorkerSearchResultSerializer
from apps.workers.serializers import ServiceTypeSerializer

from .models import Booking, BookingOffer, DayAvailability, ServiceCategory


class ServiceCategorySerializer(serializers.ModelSerializer):
    """Module 5.1 — the catalogue, with the guidance shown before booking."""

    service_type = ServiceTypeSerializer(read_only=True)
    price_guidance = serializers.CharField(read_only=True)

    class Meta:
        model = ServiceCategory
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "icon",
            "service_type",
            "expected_duration_minutes",
            "price_min",
            "price_max",
            "price_guidance",
            "bypasses_notice_period",
        ]
        read_only_fields = fields


class DayAvailabilitySerializer(serializers.ModelSerializer):
    """A worker's answer for one date (Module 5.3)."""

    class Meta:
        model = DayAvailability
        fields = ["id", "date", "is_available", "start_time", "end_time", "note"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        start = attrs.get("start_time")
        end = attrs.get("end_time")

        # Both or neither: half a window is ambiguous, and `covers()` would
        # silently treat it as no restriction at all.
        if (start is None) != (end is None):
            raise serializers.ValidationError(
                {"start_time": "Give both a start and an end time, or neither."}
            )
        if start and end and start >= end:
            raise serializers.ValidationError(
                {"end_time": "The end time must be after the start time."}
            )
        return attrs


class MatchedWorkerSerializer(WorkerSearchResultSerializer):
    """A worker who can take a specific booking.

    Extends Module 4's search row rather than defining a parallel shape, so the
    Flutter client can render one worker card in both flows.
    """

    class Meta(WorkerSearchResultSerializer.Meta):
        pass


class BookingSerializer(serializers.ModelSerializer):
    """Read projection, for both sides of a booking."""

    # Worker fields are method fields because an emergency booking has no worker
    # until somebody claims it (Module 5.5). A dotted source over a null FK is
    # silently dropped from the payload, which leaves a client unable to tell
    # "nobody yet" from "the server did not send it".
    worker_name = serializers.SerializerMethodField()
    worker_photo = serializers.SerializerMethodField()
    worker_phone = serializers.SerializerMethodField()
    resident_name = serializers.CharField(source="resident.user.get_full_name", read_only=True)
    resident_flat = serializers.CharField(source="resident.flat.__str__", read_only=True)
    category = ServiceCategorySerializer(read_only=True)

    # Deadline-aware, so an un-swept stale row still reads as expired.
    status = serializers.CharField(source="effective_status", read_only=True)
    is_actionable = serializers.BooleanField(read_only=True)
    can_be_cancelled = serializers.BooleanField(read_only=True)

    # Module 6.6 — whether "Mark as done" would be accepted right now.
    #
    # Sent rather than re-derived on the client. The app used to decide this for
    # itself from the visit date, which disagreed with the server's rule and
    # produced both halves of the emergency-booking bug: a button that was
    # offered when the server would refuse it, and hidden when the server would
    # have allowed it. There is one rule, it lives on the model, and this is it.
    can_mark_done = serializers.BooleanField(
        source="can_be_completed", read_only=True
    )
    is_emergency = serializers.BooleanField(read_only=True)
    seconds_left_to_claim = serializers.IntegerField(read_only=True)

    end_time = serializers.TimeField(read_only=True)
    scheduled_start = serializers.DateTimeField(read_only=True)
    is_paid = serializers.SerializerMethodField()
    settlement = serializers.SerializerMethodField()

    def get_worker_name(self, obj) -> str:
        return obj.worker.user.get_full_name() if obj.worker_id else ""

    def get_worker_photo(self, obj) -> str:
        if not obj.worker_id or not obj.worker.photo:
            return ""
        request = self.context.get("request")
        url = obj.worker.photo.url
        return request.build_absolute_uri(url) if request is not None else url

    def get_worker_phone(self, obj) -> str:
        return obj.worker.user.phone_number if obj.worker_id else ""

    def get_settlement(self, obj) -> str:
        """How the worker's fee is paid: ``cash`` or ``app``.

        Stated on every booking rather than inferred by the client from the
        category, because getting it wrong in either direction is a payment bug:
        an app charge on a cash job double-charges the household, and a cash
        label on an app job leaves the worker unpaid.
        """
        return "cash" if obj.is_emergency else "app"

    def get_is_paid(self, obj) -> bool:
        """Whether a settled payment exists for this booking.

        Lazily imported: Module 8 already references Module 5's ``Booking`` by
        string FK precisely to avoid the reverse import this would otherwise
        create at module load time.
        """
        from apps.payments.models import PaymentStatus

        return obj.payments.filter(status=PaymentStatus.PAID).exists()

    class Meta:
        model = Booking
        fields = [
            "id",
            "resident",
            "resident_name",
            "resident_flat",
            "worker",
            "worker_name",
            "worker_photo",
            "worker_phone",
            "category",
            "scheduled_date",
            "start_time",
            "end_time",
            "scheduled_start",
            "expected_duration_minutes",
            "quoted_price",
            "notes",
            "status",
            "is_actionable",
            "can_be_cancelled",
            "can_mark_done",
            "is_emergency",
            "settlement",
            "emergency_surcharge_paise",
            "broadcast_at",
            "offer_expires_at",
            "seconds_left_to_claim",
            "is_paid",
            "confirmed_at",
            "declined_at",
            "response_note",
            "completed_at",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "cancellation_fee",
            "created_at",
        ]
        read_only_fields = fields


class BookingCreateSerializer(serializers.ModelSerializer):
    """Module 5.2 — the resident proposes a one-day job.

    Field-shape validation only. Whether the booking is actually *placeable* —
    notice period, the worker's opt-in for that date, and slot conflicts — is
    decided in ``services.create_booking`` under a row lock, because those
    checks are worthless unless they run in the same transaction as the insert.
    """

    class Meta:
        model = Booking
        fields = [
            "worker",
            "category",
            "scheduled_date",
            "start_time",
            "expected_duration_minutes",
            "quoted_price",
            "notes",
        ]
        extra_kwargs = {
            "expected_duration_minutes": {"required": False},
            "quoted_price": {"required": False},
        }

    def validate_category(self, category):
        if not category.is_active:
            raise serializers.ValidationError("That service is not currently offered.")
        return category

    def validate_expected_duration_minutes(self, minutes):
        if minutes is not None and not 15 <= minutes <= 720:
            raise serializers.ValidationError(
                "A booking must run between 15 minutes and 12 hours."
            )
        return minutes

    def validate(self, attrs):
        request = self.context["request"]
        worker = attrs["worker"]
        category = attrs["category"]

        # Society isolation: the worker id comes from the client, so it is not
        # enough that matching only ever surfaced same-society workers.
        if worker.user.society_id != request.user.society_id:
            raise serializers.ValidationError(
                {"worker": "That worker belongs to another society."}
            )

        if not worker.is_searchable:
            raise serializers.ValidationError(
                {"worker": "This worker is not currently accepting bookings."}
            )

        if category.service_type_id and not worker.service_types.filter(
            pk=category.service_type_id
        ).exists():
            raise serializers.ValidationError(
                {"worker": "This worker does not offer that service."}
            )

        # Fall back to the catalogue's guidance when the resident accepted it
        # as-is, so the client never has to echo values it did not change.
        attrs.setdefault("expected_duration_minutes", category.expected_duration_minutes)
        if not attrs.get("quoted_price"):
            attrs["quoted_price"] = category.price_min

        if attrs["quoted_price"] <= 0:
            raise serializers.ValidationError(
                {"quoted_price": "Enter the agreed price for this job."}
            )

        return attrs


class BookingRespondSerializer(serializers.Serializer):
    """The worker's answer (Module 5.4)."""

    confirm = serializers.BooleanField()
    note = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )


class BookingCancelSerializer(serializers.Serializer):
    """Module 5.4 — cancellation, which may carry a fee."""

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )
    # The client shows the fee before confirming; requiring it back is what
    # proves the person saw it. A mismatch means the quote went stale while the
    # dialog was open — usually because a threshold was crossed.
    acknowledged_fee = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="The fee the user was shown. Omit to skip the check.",
    )


class CancellationQuoteSerializer(serializers.Serializer):
    """What cancelling now would cost. Response shape only — never written."""

    fee = serializers.IntegerField(read_only=True)
    tier = serializers.ChoiceField(
        choices=["free", "partial", "full"], read_only=True
    )
    rationale = serializers.CharField(read_only=True)
    is_free = serializers.BooleanField(read_only=True)


# ---------------------------------------------------------------------------
# 5.5 Emergency broadcast
# ---------------------------------------------------------------------------


class EmergencyRequestSerializer(serializers.Serializer):
    """What a resident sends to raise an emergency.

    Notably absent: ``worker``. That is the whole point of the flow — the
    resident is not choosing anybody, they are asking whoever is free. A client
    that sent one would be describing the directed flow, which already has its
    own endpoint.
    """

    category = serializers.PrimaryKeyRelatedField(
        queryset=ServiceCategory.objects.filter(is_active=True)
    )
    scheduled_date = serializers.DateField(required=False)
    start_time = serializers.TimeField(required=False)
    expected_duration_minutes = serializers.IntegerField(
        required=False, min_value=15, max_value=720
    )
    quoted_price = serializers.IntegerField(required=False, min_value=1)
    notes = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )

    def validate_category(self, category):
        if not category.bypasses_notice_period:
            raise serializers.ValidationError(
                "That service is not an emergency category."
            )
        return category


class BookingOfferSerializer(serializers.ModelSerializer):
    """One emergency request as it appears on a worker's dashboard.

    Flattened deliberately. This is the payload a poll returns every few seconds
    while a request is live, so it carries what the card draws and nothing else
    — no nested category object, no resident profile.
    """

    booking_id = serializers.IntegerField(source="booking.pk", read_only=True)
    category_name = serializers.CharField(source="booking.category.name", read_only=True)
    category_icon = serializers.CharField(source="booking.category.icon", read_only=True)
    flat_label = serializers.CharField(source="booking.resident.flat.__str__", read_only=True)
    scheduled_date = serializers.DateField(source="booking.scheduled_date", read_only=True)
    start_time = serializers.TimeField(source="booking.start_time", read_only=True)
    duration_minutes = serializers.IntegerField(
        source="booking.expected_duration_minutes", read_only=True
    )
    quoted_price = serializers.IntegerField(source="booking.quoted_price", read_only=True)
    notes = serializers.CharField(source="booking.notes", read_only=True)
    booking_status = serializers.CharField(source="booking.effective_status", read_only=True)
    expires_at = serializers.DateTimeField(source="booking.offer_expires_at", read_only=True)
    seconds_left = serializers.IntegerField(
        source="booking.seconds_left_to_claim", read_only=True
    )

    class Meta:
        model = BookingOffer
        fields = [
            "id",
            "booking_id",
            "state",
            "rank",
            "responded_at",
            "category_name",
            "category_icon",
            "flat_label",
            "scheduled_date",
            "start_time",
            "duration_minutes",
            "quoted_price",
            "notes",
            "booking_status",
            "expires_at",
            "seconds_left",
        ]
        read_only_fields = fields


class MatchQuerySerializer(serializers.Serializer):
    """Validates the Module 5.3 matching query parameters."""

    category = serializers.PrimaryKeyRelatedField(
        queryset=ServiceCategory.objects.filter(is_active=True)
    )
    date = serializers.DateField()
    start_time = serializers.TimeField()
    duration_minutes = serializers.IntegerField(
        required=False, min_value=15, max_value=720
    )
