"""Module 10 — Notifications: serializers."""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    SAFETY_CRITICAL_CATEGORIES,
    Notification,
    NotificationCategory,
)


class NotificationSerializer(serializers.ModelSerializer):
    """Module 10.3 — one entry in the notification centre."""

    category_display = serializers.CharField(source="get_category_display", read_only=True)
    is_read = serializers.BooleanField(read_only=True)
    is_safety_critical = serializers.BooleanField(read_only=True)
    was_delivered = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "category",
            "category_display",
            "title",
            "body",
            "data",
            "is_read",
            "read_at",
            "is_safety_critical",
            "was_delivered",
            "created_at",
            # Delivery state is exposed so a user can tell "you were never told"
            # apart from "you missed it" — and so support can answer that
            # question without database access.
            "push_state",
            "sms_state",
        ]
        read_only_fields = fields


class DeviceTokenSerializer(serializers.Serializer):
    """Module 10.1 — the app registering for push.

    ``device_id`` identifies the installation so a re-registration replaces the
    old token rather than accumulating one row per app launch.
    """

    device_id = serializers.CharField(max_length=64)
    fcm_token = serializers.CharField(max_length=255)
    device_name = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=120
    )
    platform = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=20
    )


class NotificationPreferenceSerializer(serializers.Serializer):
    """One category and whether the user has switched it off (Module 10.4)."""

    category = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    muted = serializers.BooleanField(read_only=True)

    #: Safety-critical categories cannot be muted. Exposed so the app can render
    #: the control as locked with a reason, rather than offering a switch that
    #: silently refuses.
    can_mute = serializers.BooleanField(read_only=True)


class SetPreferenceSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=NotificationCategory.choices)
    muted = serializers.BooleanField()

    def validate(self, attrs):
        if attrs["muted"] and attrs["category"] in SAFETY_CRITICAL_CATEGORIES:
            raise serializers.ValidationError(
                {
                    "category": (
                        "This kind of alert cannot be switched off — it covers "
                        "gate entry, urgent leave and your account status."
                    )
                }
            )
        return attrs


class UnreadCountSerializer(serializers.Serializer):
    """Drives the badge on the app's home screens."""

    unread = serializers.IntegerField(read_only=True)
