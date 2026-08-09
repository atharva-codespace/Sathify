"""Module 6 — Scheduling & Task Management: serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.core.files import PhotoTooLarge, validate_photo
from apps.payments.models import format_paise

from .models import (
    DEFAULT_GRACE_MINUTES,
    LeaveRequest,
    Reminder,
    TaskCompletion,
    TaskTiming,
)


class ScheduleItemSerializer(serializers.Serializer):
    """One expected visit, from either an engagement or a booking.

    A plain ``Serializer`` over the ``ScheduleItem`` dataclass rather than a
    ``ModelSerializer``, because a schedule item is not a row — it is derived on
    read (see ``schedule.py``). ``source``/``source_id`` are what the client uses
    to navigate back to whichever record produced it.
    """

    source = serializers.CharField(read_only=True)
    source_id = serializers.IntegerField(read_only=True)
    date = serializers.DateField(read_only=True)
    start_time = serializers.TimeField(read_only=True)
    end_time = serializers.TimeField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)

    title = serializers.CharField(read_only=True)
    worker_id = serializers.IntegerField(read_only=True)
    worker_name = serializers.CharField(read_only=True)
    resident_id = serializers.IntegerField(read_only=True)
    resident_name = serializers.CharField(read_only=True)
    flat_label = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)

    is_recurring = serializers.BooleanField(read_only=True)
    is_confirmed = serializers.BooleanField(read_only=True)

    expected_arrival = serializers.TimeField(read_only=True, allow_null=True)
    grace_minutes = serializers.IntegerField(read_only=True)
    task_notes = serializers.CharField(read_only=True)

    # --- 6.5 urgent leave ---------------------------------------------------
    on_leave = serializers.BooleanField(read_only=True)
    leave_status = serializers.CharField(read_only=True)
    leave_request_id = serializers.IntegerField(read_only=True)
    cover_worker_name = serializers.CharField(read_only=True)
    is_cover = serializers.BooleanField(read_only=True)
    covering_for_name = serializers.CharField(read_only=True)

    # --- 6.6 progress through the day's work --------------------------------
    visit_status = serializers.CharField(read_only=True)
    checked_in_at = serializers.DateTimeField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    exit_confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    is_complete = serializers.BooleanField(read_only=True)
    has_left = serializers.BooleanField(read_only=True)
    completion_note = serializers.CharField(read_only=True)
    completion_photo_url = serializers.CharField(read_only=True)

    #: The server's own answer to "may this worker mark this visit done now".
    #: The client renders the button from this and nothing else — see the field
    #: comment on ``ScheduleItem.can_mark_done`` for what happened when it did
    #: not.
    can_mark_done = serializers.BooleanField(read_only=True)
    #: ``app`` or ``cash``. Emergencies are settled hand to hand.
    settlement = serializers.CharField(read_only=True)

    # --- 6.7 what the day is worth, and when the next one is ----------------
    pay_paise = serializers.IntegerField(read_only=True)
    pay_state = serializers.CharField(read_only=True)
    pay_display = serializers.SerializerMethodField()
    minutes_to_next = serializers.IntegerField(read_only=True)
    next_visit_at = serializers.DateTimeField(read_only=True, allow_null=True)


    def get_pay_display(self, obj) -> str:
        return format_paise(obj.pay_paise)


class ScheduleConflictPairSerializer(serializers.Serializer):
    """Two items on the same day whose windows collide."""

    first = ScheduleItemSerializer(read_only=True)
    second = ScheduleItemSerializer(read_only=True)


# ---------------------------------------------------------------------------
# 6.2 Task timing
# ---------------------------------------------------------------------------


class TaskTimingSerializer(serializers.Serializer):
    """The expectations in force for an engagement.

    Always answers, whether or not the resident has customised anything —
    ``is_customised`` distinguishes "the resident chose this" from "these are
    the engagement's own times". The client shows the same fields either way, so
    a null-shaped response would only push the fallback logic into Dart.
    """

    expected_arrival = serializers.TimeField(read_only=True)
    arrival_grace_minutes = serializers.IntegerField(read_only=True)
    expected_departure = serializers.TimeField(read_only=True)
    departure_grace_minutes = serializers.IntegerField(read_only=True)
    task_notes = serializers.CharField(read_only=True)
    reminders_enabled = serializers.BooleanField(read_only=True)
    reminder_lead_minutes = serializers.IntegerField(read_only=True)
    is_customised = serializers.BooleanField(read_only=True)


class TaskTimingWriteSerializer(serializers.ModelSerializer):
    """Module 6.2 — what the resident sets."""

    class Meta:
        model = TaskTiming
        fields = [
            "expected_arrival",
            "arrival_grace_minutes",
            "expected_departure",
            "departure_grace_minutes",
            "task_notes",
            "reminders_enabled",
            "reminder_lead_minutes",
        ]
        extra_kwargs = {
            "arrival_grace_minutes": {"required": False},
            "departure_grace_minutes": {"required": False},
        }

    def validate_arrival_grace_minutes(self, value):
        if value > 120:
            raise serializers.ValidationError(
                "A grace period over two hours makes the arrival time meaningless."
            )
        return value

    def validate_reminder_lead_minutes(self, value):
        if not 5 <= value <= 1440:
            raise serializers.ValidationError(
                "Remind between 5 minutes and 24 hours ahead."
            )
        return value

    def validate(self, attrs):
        arrival = attrs.get(
            "expected_arrival", getattr(self.instance, "expected_arrival", None)
        )
        departure = attrs.get(
            "expected_departure", getattr(self.instance, "expected_departure", None)
        )

        if arrival and departure and departure <= arrival:
            raise serializers.ValidationError(
                {"expected_departure": "Departure must be after arrival."}
            )
        return attrs


# ---------------------------------------------------------------------------
# 6.3 Conflict detection
# ---------------------------------------------------------------------------


class ConflictQuerySerializer(serializers.Serializer):
    """Validates a pre-flight conflict check."""

    worker = serializers.IntegerField()
    date = serializers.DateField()
    start_time = serializers.TimeField()
    duration_minutes = serializers.IntegerField(min_value=15, max_value=720)
    exclude_booking = serializers.IntegerField(required=False, allow_null=True)


class ConflictReportSerializer(serializers.Serializer):
    """What a proposed visit would collide with.

    Returns the colliding items, not just a flag: modspec 6.3 allows a conflict
    to be flagged for manual resolution as well as rejected, and nobody can
    resolve what they cannot see.
    """

    has_conflict = serializers.BooleanField(read_only=True)
    summary = serializers.CharField(read_only=True)
    clashes = ScheduleItemSerializer(many=True, read_only=True)


# ---------------------------------------------------------------------------
# 6.4 Reminders
# ---------------------------------------------------------------------------


class ReminderSerializer(serializers.ModelSerializer):
    recipient_name = serializers.CharField(
        source="recipient.get_full_name", read_only=True
    )
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = Reminder
        fields = [
            "id",
            "kind",
            "kind_display",
            "recipient",
            "recipient_name",
            "engagement",
            "booking",
            "title",
            "body",
            "event_at",
            "send_after",
            "status",
            "sent_at",
            "failure_reason",
        ]
        read_only_fields = fields


class ReminderDeliverySerializer(serializers.Serializer):
    """Module 10 reports back what happened to a reminder it took."""

    delivered = serializers.BooleanField()
    failure_reason = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=200
    )


# ---------------------------------------------------------------------------
# 6.5 Urgent leave
# ---------------------------------------------------------------------------


class LeaveRequestSerializer(serializers.ModelSerializer):
    """One day of leave and everything that has happened to it.

    Amounts are exposed in paise, like every other money field on the platform,
    with a formatted copy alongside — the app should never do currency
    arithmetic, and a formatted string it can print is what stops it trying.
    """

    worker_name = serializers.CharField(source="worker.user.get_full_name", read_only=True)
    resident_name = serializers.CharField(
        source="engagement.resident.user.get_full_name", read_only=True
    )
    flat_label = serializers.CharField(source="engagement.resident.flat", read_only=True)
    replacement_name = serializers.SerializerMethodField()
    start_time = serializers.TimeField(source="engagement.start_time", read_only=True)

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    summary = serializers.CharField(read_only=True)
    needs_resident_response = serializers.BooleanField(read_only=True)
    can_withdraw = serializers.BooleanField(read_only=True)
    is_covered = serializers.BooleanField(read_only=True)
    is_settled = serializers.BooleanField(read_only=True)

    replacement_display = serializers.SerializerMethodField()

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "engagement",
            "leave_date",
            "reason",
            "status",
            "status_display",
            "summary",
            "worker",
            "worker_name",
            "resident_name",
            "flat_label",
            "start_time",
            "replacement",
            "replacement_name",
            "replacement_confirmed_at",
            "resident_responded_at",
            "day_rate_paise",
            "forgone_paise",
            "replacement_paise",
            "replacement_display",
            "settled_at",
            "needs_resident_response",
            "can_withdraw",
            "is_covered",
            "is_settled",
            "created_at",
        ]
        read_only_fields = fields

    def get_replacement_name(self, obj) -> str:
        return obj.replacement.user.get_full_name() if obj.replacement_id else ""

    def get_replacement_display(self, obj) -> str:
        return format_paise(obj.replacement_paise)


class LeaveCreateSerializer(serializers.Serializer):
    """Module 6.5 — a worker asks for a day off.

    ``reason`` is optional and stays optional. A worker should not have to
    describe a private emergency to a form in order to be believed, and a
    required field here would mostly collect fiction.
    """

    engagement = serializers.IntegerField()
    leave_date = serializers.DateField()
    reason = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=200
    )


class LeaveResponseSerializer(serializers.Serializer):
    """The household's answer: do you need somebody else that day?

    Note what is *not* here: there is no way to refuse the leave. The worker is
    not asking permission — see ``LeaveRequest``.
    """

    needs_replacement = serializers.BooleanField()


class ReplacementAssignSerializer(serializers.Serializer):
    """Who is covering."""

    replacement = serializers.IntegerField()


class ReplacementCandidateSerializer(serializers.Serializer):
    """A worker who could cover, with the score that put them there.

    The breakdown travels with the suggestion because Module 4.3 established
    that a ranking a resident cannot account for is a ranking they will not
    trust — and this one is being read in a hurry.
    """

    worker_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    photo_url = serializers.CharField(read_only=True, allow_blank=True)
    trust_score = serializers.FloatField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    rating_count = serializers.IntegerField(read_only=True)
    match_score = serializers.FloatField(read_only=True)
    match_percentage = serializers.IntegerField(read_only=True)
    components = serializers.ListField(read_only=True)


# ---------------------------------------------------------------------------
# 6.6 Task completion
# ---------------------------------------------------------------------------


class TaskCompletionSerializer(serializers.ModelSerializer):
    """One day's work, marked done by the worker."""

    worker_name = serializers.CharField(source="worker.user.get_full_name", read_only=True)
    photo_url = serializers.ImageField(source="photo", read_only=True)

    class Meta:
        model = TaskCompletion
        fields = [
            "id",
            "engagement",
            "booking",
            "worker",
            "worker_name",
            "visit_date",
            "completed_at",
            "note",
            "photo_url",
            "created_at",
        ]
        read_only_fields = fields


class MarkTaskCompleteSerializer(serializers.Serializer):
    """Module 6.6 — the worker says the day's work is done.

    Exactly one of ``engagement`` or ``booking``. ``visit_date`` defaults to
    today, because the overwhelmingly common case is somebody pressing the
    button as they finish — but it is settable, so a worker who finishes at
    00:10 can mark yesterday's visit rather than tomorrow's.

    ``photo`` is optional and stays optional. Requiring proof would turn a flat
    battery into an unpaid day.
    """

    engagement = serializers.IntegerField(required=False)
    booking = serializers.IntegerField(required=False)
    visit_date = serializers.DateField(required=False)
    note = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=300
    )
    photo = serializers.ImageField(required=False, allow_null=True)

    def validate_photo(self, uploaded):
        # Bounded because the instance is 512 MB and Django buffers an
        # upload in memory before it reaches storage.
        if uploaded is None:
            return uploaded
        try:
            return validate_photo(uploaded)
        except PhotoTooLarge as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        if bool(attrs.get("engagement")) == bool(attrs.get("booking")):
            raise serializers.ValidationError(
                "Send exactly one of engagement or booking."
            )
        return attrs
