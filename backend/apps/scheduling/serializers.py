"""Module 6 — Scheduling & Task Management: serializers."""

from __future__ import annotations

from rest_framework import serializers

from .models import DEFAULT_GRACE_MINUTES, Reminder, TaskTiming


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
