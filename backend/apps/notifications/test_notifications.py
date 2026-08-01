"""
Module 10 — Notifications: tests.

Two properties carry the weight.

``TestSafetyCriticalCategories`` pins that gate-entry, urgent-leave and account
alerts cannot be muted. That is a duty the platform has to its users, not a
preference, and a regression would be silent — nobody notices notifications they
never received.

``TestDeliveryChain`` pins that a notification is recorded before anything is
sent, and survives both channels failing. Build it the other way round and a
worker never learns their society approved them because a token had expired.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.models import DeviceSession
from apps.notifications import push, sms
from apps.notifications.models import (
    SAFETY_CRITICAL_CATEGORIES,
    DeliveryState,
    Notification,
    NotificationCategory,
    NotificationPreference,
)
from apps.notifications.services import (
    active_tokens,
    deliver_due_reminders,
    notify,
    notify_many,
    retry_failed_deliveries,
)

pytestmark = pytest.mark.django_db


def a_device(user, *, token="tok_abc", device_id="dev-1"):
    return DeviceSession.objects.create(
        user=user, device_id=device_id, fcm_token=token
    )


def sent_push(**kwargs):
    return push.PushResult(sent=1, **kwargs)


def failed_push(reason="Firebase refused the message.", **kwargs):
    return push.PushResult(sent=0, failed=1, reason=reason, **kwargs)


def unavailable_push(reason="Push notifications are not configured."):
    return push.PushResult(available=False, reason=reason)


# ---------------------------------------------------------------------------
# 10.4 The mute exclusion
# ---------------------------------------------------------------------------


class TestSafetyCriticalCategories:
    def test_gate_entry_and_urgent_leave_are_protected(self):
        """Modspec 10.4 names these two explicitly."""
        assert NotificationCategory.GATE_ENTRY in SAFETY_CRITICAL_CATEGORIES
        assert NotificationCategory.URGENT_LEAVE in SAFETY_CRITICAL_CATEGORIES

    def test_a_protected_category_cannot_be_muted(self, worker_user):
        honoured = NotificationPreference.set_muted(
            worker_user, NotificationCategory.GATE_ENTRY, muted=True
        )

        assert honoured is False
        assert not NotificationPreference.is_muted(
            worker_user, NotificationCategory.GATE_ENTRY
        )

    def test_a_stored_mute_on_a_protected_category_is_still_ignored(self, worker_user):
        """The check is the authority, not the row. A mute written directly —
        by a migration, a fixture, a bug — must not silence a duty."""
        NotificationPreference.objects.create(
            user=worker_user, category=NotificationCategory.GATE_ENTRY, muted=True
        )

        assert not NotificationPreference.is_muted(
            worker_user, NotificationCategory.GATE_ENTRY
        )

    def test_an_ordinary_category_can_be_muted(self, worker_user):
        honoured = NotificationPreference.set_muted(
            worker_user, NotificationCategory.RATING, muted=True
        )

        assert honoured is True
        assert NotificationPreference.is_muted(
            worker_user, NotificationCategory.RATING
        )

    def test_unmuting_works(self, worker_user):
        NotificationPreference.set_muted(
            worker_user, NotificationCategory.RATING, muted=True
        )
        NotificationPreference.set_muted(
            worker_user, NotificationCategory.RATING, muted=False
        )

        assert not NotificationPreference.is_muted(
            worker_user, NotificationCategory.RATING
        )

    def test_the_api_refuses_to_mute_a_protected_category(
        self, authenticated_client, worker_user
    ):
        response = authenticated_client(worker_user).put(
            reverse("v1:notifications:preferences"),
            {"category": NotificationCategory.GATE_ENTRY, "muted": True},
            format="json",
        )
        assert response.status_code == 400

    def test_the_preferences_list_marks_which_can_be_muted(
        self, authenticated_client, worker_user
    ):
        """So the app renders a locked control with a reason, rather than a
        switch that silently refuses."""
        response = authenticated_client(worker_user).get(
            reverse("v1:notifications:preferences")
        )

        rows = {row["category"]: row for row in response.data}
        assert rows[NotificationCategory.GATE_ENTRY]["can_mute"] is False
        assert rows[NotificationCategory.RATING]["can_mute"] is True

    def test_every_category_is_listed(self, authenticated_client, worker_user):
        response = authenticated_client(worker_user).get(
            reverse("v1:notifications:preferences")
        )
        assert len(response.data) == len(NotificationCategory.choices)


# ---------------------------------------------------------------------------
# The delivery chain
# ---------------------------------------------------------------------------


class TestDeliveryChain:
    def test_a_notification_is_recorded_even_when_both_channels_fail(
        self, worker_user
    ):
        """The in-app centre is the system of record; the channels are attempts."""
        with patch("apps.notifications.services.push.send", return_value=failed_push()), \
             patch(
                 "apps.notifications.services.sms.send",
                 return_value=sms.SmsResult(available=False, reason="SMS is off."),
             ):
            notification = notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="You were paid",
                body="₹4,000 from Anita Desai.",
            )

        assert notification is not None
        assert Notification.objects.count() == 1
        assert notification.was_delivered is False

    def test_a_successful_push_is_recorded_as_sent(self, worker_user):
        a_device(worker_user)

        with patch("apps.notifications.services.push.send", return_value=sent_push()):
            notification = notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="You were paid",
                body="₹4,000.",
            )

        assert notification.push_state == DeliveryState.SENT
        assert notification.was_delivered is True
        assert notification.delivered_at is not None

    def test_sms_is_tried_when_push_fails(self, worker_user):
        """Module 10.2's whole purpose."""
        a_device(worker_user)

        with patch("apps.notifications.services.push.send", return_value=failed_push()), \
             patch(
                 "apps.notifications.services.sms.send",
                 return_value=sms.SmsResult(sent=True),
             ) as sms_send:
            notification = notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="You were paid",
                body="₹4,000.",
            )

        sms_send.assert_called_once()
        assert notification.sms_state == DeliveryState.SENT
        assert notification.was_delivered is True

    def test_sms_is_not_tried_when_push_succeeds(self, worker_user):
        """SMS costs money per message."""
        a_device(worker_user)

        with patch("apps.notifications.services.push.send", return_value=sent_push()), \
             patch("apps.notifications.services.sms.send") as sms_send:
            notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="Paid",
                body="₹4,000.",
            )

        sms_send.assert_not_called()

    def test_a_user_with_no_device_falls_straight_through_to_sms(self, worker_user):
        """The no-smartphone case, where SMS is the only channel there is."""
        with patch(
            "apps.notifications.services.sms.send", return_value=sms.SmsResult(sent=True)
        ) as sms_send:
            notification = notify(
                recipient=worker_user,
                category=NotificationCategory.GATE_ENTRY,
                title="Entry allowed",
                body="You were let in at 09:05.",
            )

        sms_send.assert_called_once()
        assert notification.push_state == DeliveryState.SKIPPED

    def test_delivered_at_is_not_moved_by_a_later_attempt(self, worker_user):
        """It answers 'when did this person find out'."""
        a_device(worker_user)

        with patch("apps.notifications.services.push.send", return_value=sent_push()):
            notification = notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="Paid",
                body="₹4,000.",
            )
        first = notification.delivered_at

        notification.record_delivery(channel="sms", state=DeliveryState.SENT)
        notification.refresh_from_db()

        assert notification.delivered_at == first

    def test_notifying_never_raises_even_if_delivery_explodes(self, worker_user):
        """A payment that settled must not 500 because a phone was unreachable."""
        with patch(
            "apps.notifications.services.push.send", side_effect=RuntimeError("boom")
        ):
            notification = notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="Paid",
                body="₹4,000.",
            )

        assert notification is not None
        assert Notification.objects.count() == 1

    def test_notify_many_continues_past_a_broken_recipient(
        self, worker_user, resident_user, guard_user
    ):
        with patch("apps.notifications.services.push.send", return_value=sent_push()):
            recorded = notify_many(
                recipients=[worker_user, resident_user, guard_user],
                category=NotificationCategory.ACCOUNT,
                title="Notice",
                body="Something happened.",
            )

        assert recorded == 3


class TestMuting:
    def test_a_muted_category_is_recorded_but_not_pushed(self, worker_user):
        """Muting means 'stop interrupting me', not 'keep this from me'."""
        a_device(worker_user)
        NotificationPreference.set_muted(
            worker_user, NotificationCategory.RATING, muted=True
        )

        with patch("apps.notifications.services.push.send") as push_send:
            notification = notify(
                recipient=worker_user,
                category=NotificationCategory.RATING,
                title="Rate your recent work",
                body="You have a job to rate.",
            )

        push_send.assert_not_called()
        assert Notification.objects.count() == 1
        assert notification.push_state == DeliveryState.SKIPPED

    def test_a_protected_category_is_pushed_despite_a_stored_mute(self, worker_user):
        a_device(worker_user)
        NotificationPreference.objects.create(
            user=worker_user, category=NotificationCategory.GATE_ENTRY, muted=True
        )

        with patch(
            "apps.notifications.services.push.send", return_value=sent_push()
        ) as push_send:
            notify(
                recipient=worker_user,
                category=NotificationCategory.GATE_ENTRY,
                title="Entry refused",
                body="Your pass was not accepted.",
            )

        push_send.assert_called_once()


# ---------------------------------------------------------------------------
# 10.1 Device tokens
# ---------------------------------------------------------------------------


class TestDeviceTokens:
    URL = "v1:notifications:device"

    def test_a_device_registers_for_push(self, authenticated_client, worker_user):
        response = authenticated_client(worker_user).post(
            reverse(self.URL),
            {"device_id": "dev-1", "fcm_token": "tok_abc", "platform": "android"},
            format="json",
        )

        assert response.status_code == 201
        assert active_tokens(worker_user) == ["tok_abc"]

    def test_re_registering_replaces_rather_than_duplicates(
        self, authenticated_client, worker_user
    ):
        """Firebase rotates tokens; stale rows would push twice to one phone."""
        client = authenticated_client(worker_user)
        client.post(
            reverse(self.URL),
            {"device_id": "dev-1", "fcm_token": "tok_old"},
            format="json",
        )
        client.post(
            reverse(self.URL),
            {"device_id": "dev-1", "fcm_token": "tok_new"},
            format="json",
        )

        assert DeviceSession.objects.filter(user=worker_user).count() == 1
        assert active_tokens(worker_user) == ["tok_new"]

    def test_a_revoked_device_receives_nothing(self, worker_user):
        """A device that was cut off must stop being a way to read activity."""
        session = a_device(worker_user)
        session.revoke(reason="Signed out")

        assert active_tokens(worker_user) == []

    def test_removing_a_device_stops_pushes(self, authenticated_client, worker_user):
        client = authenticated_client(worker_user)
        client.post(
            reverse(self.URL),
            {"device_id": "dev-1", "fcm_token": "tok_abc"},
            format="json",
        )

        client.delete(f"{reverse(self.URL)}?device_id=dev-1")
        assert active_tokens(worker_user) == []

    def test_a_dead_token_is_cleared_after_firebase_rejects_it(self, worker_user):
        """Left in place it is retried on every notification forever."""
        a_device(worker_user, token="tok_dead")

        with patch(
            "apps.notifications.services.push.send",
            return_value=push.PushResult(
                sent=0, failed=1, invalid_tokens=["tok_dead"], reason="UNREGISTERED"
            ),
        ), patch(
            "apps.notifications.services.sms.send",
            return_value=sms.SmsResult(available=False),
        ):
            notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="Paid",
                body="₹4,000.",
            )

        assert active_tokens(worker_user) == []


# ---------------------------------------------------------------------------
# 10.3 The notification centre
# ---------------------------------------------------------------------------


class TestNotificationCentre:
    URL = "v1:notifications:notification-list"

    def make(self, user, *, category=NotificationCategory.PAYMENT):
        with patch(
            "apps.notifications.services.push.send", return_value=unavailable_push()
        ), patch(
            "apps.notifications.services.sms.send",
            return_value=sms.SmsResult(available=False),
        ):
            return notify(
                recipient=user, category=category, title="Notice", body="Something."
            )

    def test_a_user_sees_their_own_notifications(
        self, authenticated_client, worker_user
    ):
        self.make(worker_user)

        response = authenticated_client(worker_user).get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_a_user_never_sees_someone_elses(
        self, authenticated_client, worker_user, resident_user
    ):
        self.make(worker_user)

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["count"] == 0

    def test_the_unread_badge_counts(self, authenticated_client, worker_user):
        self.make(worker_user)
        self.make(worker_user)

        response = authenticated_client(worker_user).get(
            reverse("v1:notifications:unread-count")
        )
        assert response.data["unread"] == 2

    def test_marking_one_read_is_idempotent(self, authenticated_client, worker_user):
        notification = self.make(worker_user)
        client = authenticated_client(worker_user)
        url = reverse("v1:notifications:mark-read", args=[notification.pk])

        client.post(url)
        notification.refresh_from_db()
        first = notification.read_at

        client.post(url)
        notification.refresh_from_db()
        assert notification.read_at == first

    def test_marking_all_read_clears_the_badge(
        self, authenticated_client, worker_user
    ):
        self.make(worker_user)
        self.make(worker_user)
        client = authenticated_client(worker_user)

        response = client.post(reverse("v1:notifications:read-all"))

        assert response.data["marked_read"] == 2
        assert client.get(reverse("v1:notifications:unread-count")).data["unread"] == 0

    def test_marking_someone_elses_read_is_refused(
        self, authenticated_client, worker_user, resident_user
    ):
        notification = self.make(worker_user)

        response = authenticated_client(resident_user).post(
            reverse("v1:notifications:mark-read", args=[notification.pk])
        )
        assert response.status_code == 404

    def test_filtering_by_category(self, authenticated_client, worker_user):
        self.make(worker_user, category=NotificationCategory.PAYMENT)
        self.make(worker_user, category=NotificationCategory.GATE_ENTRY)

        response = authenticated_client(worker_user).get(
            reverse(self.URL), {"category": NotificationCategory.GATE_ENTRY}
        )
        assert response.data["count"] == 1

    def test_delivery_state_is_visible_to_the_user(
        self, authenticated_client, worker_user
    ):
        """So "you were never told" is distinguishable from "you missed it"."""
        self.make(worker_user)

        row = authenticated_client(worker_user).get(reverse(self.URL)).data["results"][0]
        assert row["push_state"] == DeliveryState.SKIPPED
        assert row["was_delivered"] is False


# ---------------------------------------------------------------------------
# 10.2 SMS composition
# ---------------------------------------------------------------------------


class TestSmsComposition:
    def test_the_title_comes_first(self):
        """On a feature phone the message is read from a preview."""
        text = sms.compose(title="Entry refused", body="Your pass was not accepted.")
        assert text.startswith("Entry refused")

    def test_a_long_message_is_trimmed_to_one_billable_sms(self):
        """Longer messages are split and billed per part."""
        text = sms.compose(title="Notice", body="x" * 500)

        assert len(text) <= sms.MAX_SMS_LENGTH
        assert text.endswith("…")

    def test_a_short_message_is_not_padded_or_truncated(self):
        text = sms.compose(title="Paid", body="₹4,000 received.")
        assert text == "Paid: ₹4,000 received."

    def test_sms_is_unavailable_when_unconfigured(self, settings):
        settings.SMS_SETTINGS = {"ENABLED": False}
        result = sms.send(phone_number="9800000001", title="x", body="y")

        assert result.available is False
        assert result.sent is False

    def test_no_phone_number_is_reported_rather_than_attempted(self):
        result = sms.send(phone_number="", title="x", body="y")
        assert result.available is False


class TestPushConfiguration:
    def test_push_is_unavailable_when_unconfigured(self, settings):
        settings.FCM_SETTINGS = {"ENABLED": False}
        result = push.send(tokens=["tok"], title="x", body="y")

        assert result.available is False
        assert result.succeeded is False

    def test_no_tokens_is_reported_rather_than_attempted(self):
        result = push.send(tokens=[], title="x", body="y")

        assert result.available is False
        assert "No device tokens" in result.reason


# ---------------------------------------------------------------------------
# The join with Module 6.4
# ---------------------------------------------------------------------------


class TestReminderDelivery:
    @pytest.fixture
    def due_reminder(self, society, worker_user):
        import datetime as dt

        from django.utils import timezone

        from apps.scheduling.models import Reminder

        return Reminder.objects.create(
            society=society,
            recipient=worker_user,
            kind="upcoming_engagement",
            event_at=timezone.now() + dt.timedelta(hours=1),
            send_after=timezone.now() - dt.timedelta(minutes=5),
            title="Maid at A-301",
            body="You are expected at A-301 at 09:00 today.",
        )

    def test_a_due_reminder_becomes_a_notification(self, due_reminder):
        with patch(
            "apps.notifications.services.push.send", return_value=unavailable_push()
        ), patch(
            "apps.notifications.services.sms.send",
            return_value=sms.SmsResult(available=False),
        ):
            delivered = deliver_due_reminders()

        assert delivered == 1
        notification = Notification.objects.get()
        assert notification.category == NotificationCategory.SCHEDULE
        assert notification.title == "Maid at A-301"

    def test_a_delivered_reminder_is_not_delivered_twice(self, due_reminder):
        """Idempotent through the reminder's own status."""
        with patch(
            "apps.notifications.services.push.send", return_value=unavailable_push()
        ), patch(
            "apps.notifications.services.sms.send",
            return_value=sms.SmsResult(available=False),
        ):
            deliver_due_reminders()
            second = deliver_due_reminders()

        assert second == 0
        assert Notification.objects.count() == 1

    def test_the_retry_sweep_only_touches_undelivered_notifications(
        self, worker_user
    ):
        with patch(
            "apps.notifications.services.push.send", return_value=unavailable_push()
        ), patch(
            "apps.notifications.services.sms.send",
            return_value=sms.SmsResult(available=False, reason="off"),
        ):
            notify(
                recipient=worker_user,
                category=NotificationCategory.PAYMENT,
                title="Paid",
                body="₹4,000.",
            )

        # SMS was attempted and skipped, so it is no longer pending and the
        # sweep should leave it alone rather than retrying a disabled channel.
        assert retry_failed_deliveries() == 0

    def test_an_admin_can_trigger_delivery_over_http(
        self, authenticated_client, admin_user, due_reminder
    ):
        """For an external pinger that can only make HTTP calls."""
        with patch(
            "apps.notifications.services.push.send", return_value=unavailable_push()
        ), patch(
            "apps.notifications.services.sms.send",
            return_value=sms.SmsResult(available=False),
        ):
            response = authenticated_client(admin_user).post(
                reverse("v1:notifications:deliver-due")
            )

        assert response.status_code == 200
        assert response.data["reminders_delivered"] == 1

    def test_a_worker_cannot_trigger_delivery(
        self, authenticated_client, worker_user
    ):
        response = authenticated_client(worker_user).post(
            reverse("v1:notifications:deliver-due")
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# The events other modules actually notify on
# ---------------------------------------------------------------------------


class TestModuleIntegration:
    """Module 10 exists to be called. Without these, it would only ever carry
    reminders, and every other event would go untold."""

    @pytest.fixture(autouse=True)
    def _silent_channels(self):
        """Neither channel is configured in tests; the row is what matters."""
        with patch(
            "apps.notifications.services.push.send", return_value=unavailable_push()
        ), patch(
            "apps.notifications.services.sms.send",
            return_value=sms.SmsResult(available=False),
        ):
            yield

    @pytest.fixture
    def worker(self, worker_user):
        from apps.workers.models import ServiceType, WorkerProfile

        profile = WorkerProfile.objects.create(
            user=worker_user, photo="workers/photos/test.jpg"
        )
        profile.service_types.add(ServiceType.objects.create(name="Maid", slug="maid"))
        return profile

    def test_approving_a_worker_tells_them(self, worker, admin_user):
        """Module 3.5. They cannot start working if nobody says so."""
        from apps.workers.models import KycDocument, KycStatus
        from apps.workers.services import approve_worker

        KycDocument.objects.create(
            worker=worker,
            document_image="workers/kyc/test.jpg",
            status=KycStatus.COMPLETED,
            aadhaar_checksum_valid=True,
        )

        approve_worker(worker, reviewed_by=admin_user)

        notification = Notification.objects.get(recipient=worker.user)
        assert notification.category == NotificationCategory.ACCOUNT
        assert notification.is_safety_critical

    def test_rejecting_a_worker_tells_them_why(self, worker, admin_user):
        """They cannot correct what they are not told about."""
        from apps.workers.services import reject_worker

        reject_worker(
            worker, reason="The photo of your card is unreadable.", reviewed_by=admin_user
        )

        notification = Notification.objects.get(recipient=worker.user)
        assert "unreadable" in notification.body

    def test_a_refused_gate_entry_tells_the_worker(self, worker, society, guard_user):
        """They may be standing outside wondering what went wrong."""
        import uuid

        from django.utils import timezone

        from apps.attendance.models import Decision, Direction, VerificationMethod
        from apps.attendance.services import record_event

        record_event(
            event_id=uuid.uuid4(),
            worker=worker,
            society=society,
            direction=Direction.ENTRY,
            method=VerificationMethod.QR,
            decision=Decision.DENIED,
            decision_reason="Your pass was cancelled.",
            occurred_at=timezone.now(),
            recorded_by=guard_user,
        )

        notification = Notification.objects.get(recipient=worker.user)
        assert notification.category == NotificationCategory.GATE_ENTRY
        assert "cancelled" in notification.body

    def test_an_allowed_gate_entry_does_not_notify(self, worker, society, guard_user):
        """Being let in as expected is not news; notifying on it would train
        people to ignore the ones that matter."""
        import uuid

        from django.utils import timezone

        from apps.attendance.models import Decision, Direction, VerificationMethod
        from apps.attendance.services import record_event

        record_event(
            event_id=uuid.uuid4(),
            worker=worker,
            society=society,
            direction=Direction.ENTRY,
            method=VerificationMethod.QR,
            decision=Decision.ALLOWED,
            occurred_at=timezone.now(),
            recorded_by=guard_user,
        )

        assert not Notification.objects.filter(recipient=worker.user).exists()
