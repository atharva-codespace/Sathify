"""
Module 8 — Payments & Payouts: tests.

Two groups carry more weight than the rest.

``TestSignatureVerification`` is the trust boundary. Every path to PAID goes
through an HMAC check, and if that check can be bypassed then any resident can
mark their own payments settled — which means a worker is recorded as having
been paid when they were not.

``TestMoneyArithmetic`` pins that money stays in integer paise. A float creeping
into this module drifts by fractions of a paisa per row and eventually fails to
reconcile against Razorpay, which counts in paise and is right to.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.attendance.models import AttendanceEvent, Decision, Direction, VerificationMethod
from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.hiring.models import Engagement
from apps.payments import gateway
from apps.payments.models import (
    DisputeStatus,
    Payment,
    PaymentDispute,
    PaymentKind,
    PaymentStatus,
    ReplacementSplit,
    WebhookEvent,
    format_paise,
    rupees_to_paise,
)
from apps.payments.services import salary_basis
from apps.payments.summary import build_monthly_summary, render_csv, render_pdf
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db

TEST_KEY_SECRET = "test_secret_do_not_use"
TEST_WEBHOOK_SECRET = "test_webhook_secret"

RAZORPAY_TEST_SETTINGS = {
    "KEY_ID": "rzp_test_abc123",
    "KEY_SECRET": TEST_KEY_SECRET,
    "WEBHOOK_SECRET": TEST_WEBHOOK_SECRET,
    "TEST_MODE": True,
    "CURRENCY": "INR",
}


@pytest.fixture(autouse=True)
def razorpay_settings(settings):
    """Give every test a configured, test-mode gateway.

    Applied through pytest-django's ``settings`` fixture rather than
    ``@override_settings``, which only decorates Django ``TestCase`` subclasses.
    A fresh dict per test so one mutating it cannot leak into the next.
    """
    settings.RAZORPAY_SETTINGS = dict(RAZORPAY_TEST_SETTINGS)
    return settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def maid_service(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def worker(worker_user, maid_service):
    profile = WorkerProfile.objects.create(
        user=worker_user, photo="workers/photos/test.jpg"
    )
    profile.service_types.add(maid_service)
    return profile


@pytest.fixture
def engagement(society, resident, worker, maid_service):
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 2, 4],
        start_time=dt.time(9, 0),
        expected_duration_minutes=90,
        monthly_rate=4000,
    )


@pytest.fixture
def booking(society, resident, worker):
    return Booking.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        category=ServiceCategory.objects.get(slug="deep-cleaning"),
        scheduled_date=timezone.localdate() + dt.timedelta(days=3),
        start_time=dt.time(10, 0),
        quoted_price=2000,
        status=BookingStatus.CONFIRMED,
    )


def make_payment(society, resident, worker, **kwargs):
    kwargs.setdefault("kind", PaymentKind.BOOKING)
    kwargs.setdefault("amount_paise", 200000)
    return Payment.objects.create(
        society=society, resident=resident, worker=worker, **kwargs
    )


def checkout_signature(order_id: str, payment_id: str) -> str:
    """The signature Razorpay Checkout would produce."""
    return hmac.new(
        TEST_KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


def webhook_signature(raw_body: bytes) -> str:
    return hmac.new(TEST_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


class TestMoneyArithmetic:
    def test_rupees_convert_to_paise(self):
        assert rupees_to_paise(4000) == 400000
        assert rupees_to_paise(1) == 100

    def test_a_float_amount_is_rejected_rather_than_rounded(self):
        """A float here drifts by fractions of a paisa and never reconciles."""
        with pytest.raises(TypeError):
            rupees_to_paise(40.5)

    def test_a_bool_is_not_an_amount(self):
        """bool is a subclass of int; True would otherwise become one paisa."""
        with pytest.raises(TypeError):
            rupees_to_paise(True)

    def test_paise_format_for_display(self):
        assert format_paise(450050) == "₹4,500.50"
        assert format_paise(100) == "₹1.00"
        assert format_paise(5) == "₹0.05"
        assert format_paise(0) == "₹0.00"

    def test_the_tip_is_part_of_the_same_charge(self, society, resident, worker):
        """Module 8.4 — one authorisation, not a second transaction."""
        payment = make_payment(
            society, resident, worker, amount_paise=200000, tip_paise=5000
        )
        assert payment.total_paise == 205000

    def test_a_partial_refund_leaves_the_payment_settled(
        self, society, resident, worker
    ):
        """It happened, and the ledger has to keep saying so."""
        payment = make_payment(society, resident, worker, amount_paise=200000)
        payment.mark_paid(razorpay_payment_id="pay_1")

        payment.mark_refunded(amount_paise=50000)

        assert payment.status == PaymentStatus.PAID
        assert payment.net_paise == 150000

    def test_a_full_refund_changes_the_status(self, society, resident, worker):
        payment = make_payment(society, resident, worker, amount_paise=200000)
        payment.mark_paid(razorpay_payment_id="pay_1")

        payment.mark_refunded()

        assert payment.status == PaymentStatus.REFUNDED
        assert payment.net_paise == 0

    def test_a_refund_cannot_exceed_the_payment(self, society, resident, worker):
        payment = make_payment(society, resident, worker, amount_paise=200000)
        payment.mark_paid(razorpay_payment_id="pay_1")

        payment.mark_refunded(amount_paise=999999)

        assert payment.refunded_paise == payment.total_paise
        assert payment.net_paise == 0


class TestReceiptNumbers:
    def test_every_payment_gets_one(self, society, resident, worker):
        payment = make_payment(society, resident, worker)
        assert payment.receipt_number.startswith("SATH-")

    def test_they_are_unique(self, society, resident, worker):
        numbers = {
            make_payment(society, resident, worker).receipt_number for _ in range(20)
        }
        assert len(numbers) == 20


# ---------------------------------------------------------------------------
# 8.1 Signature verification — the trust boundary
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    def test_a_genuine_checkout_signature_verifies(self):
        signature = checkout_signature("order_x", "pay_y")

        assert gateway.verify_checkout_signature(
            order_id="order_x", payment_id="pay_y", signature=signature
        )

    def test_a_forged_checkout_signature_is_rejected(self):
        assert not gateway.verify_checkout_signature(
            order_id="order_x", payment_id="pay_y", signature="0" * 64
        )

    def test_a_signature_for_a_different_order_is_rejected(self):
        """Otherwise one paid order's signature would settle every other."""
        signature = checkout_signature("order_a", "pay_y")

        assert not gateway.verify_checkout_signature(
            order_id="order_b", payment_id="pay_y", signature=signature
        )

    def test_an_empty_signature_is_rejected(self):
        assert not gateway.verify_checkout_signature(
            order_id="order_x", payment_id="pay_y", signature=""
        )

    def test_a_genuine_webhook_signature_verifies(self):
        body = b'{"event":"payment.captured"}'

        assert gateway.verify_webhook_signature(
            raw_body=body, signature=webhook_signature(body)
        )

    def test_a_tampered_webhook_body_is_rejected(self):
        """The signature covers the body, so altering it invalidates the message."""
        original = b'{"event":"payment.captured","amount":100}'
        signature = webhook_signature(original)
        tampered = b'{"event":"payment.captured","amount":999999}'

        assert not gateway.verify_webhook_signature(
            raw_body=tampered, signature=signature
        )

    def test_the_webhook_secret_is_not_the_api_key_secret(self):
        """They are separate secrets; using one for the other must not verify."""
        body = b'{"event":"payment.captured"}'
        wrong = hmac.new(TEST_KEY_SECRET.encode(), body, hashlib.sha256).hexdigest()

        assert not gateway.verify_webhook_signature(raw_body=body, signature=wrong)

    @override_settings(
        RAZORPAY_SETTINGS={**RAZORPAY_TEST_SETTINGS, "KEY_SECRET": ""}
    )
    def test_nothing_verifies_without_a_configured_secret(self):
        """An unconfigured server must not accept everything."""
        assert not gateway.verify_checkout_signature(
            order_id="o", payment_id="p", signature="anything"
        )


class TestLiveKeyGuardRail:
    def test_a_test_key_in_test_mode_is_fine(self):
        gateway.assert_not_live()  # does not raise

    @override_settings(
        RAZORPAY_SETTINGS={**RAZORPAY_TEST_SETTINGS, "KEY_ID": "rzp_live_real"}
    )
    def test_a_live_key_in_test_mode_is_refused(self):
        """Somebody about to charge a real card by accident."""
        with pytest.raises(gateway.LiveKeyRefused):
            gateway.assert_not_live()

    @override_settings(
        RAZORPAY_SETTINGS={
            **RAZORPAY_TEST_SETTINGS,
            "KEY_ID": "rzp_live_real",
            "TEST_MODE": False,
        }
    )
    def test_a_live_key_with_test_mode_off_is_allowed(self):
        """Deliberate is different from accidental."""
        gateway.assert_not_live()


# ---------------------------------------------------------------------------
# 8.1 Settlement
# ---------------------------------------------------------------------------


class TestSettlement:
    def confirm_url(self, payment):
        return reverse("v1:payments:confirm", args=[payment.pk])

    def test_a_signed_confirmation_settles_the_payment(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        payment = make_payment(
            society, resident, worker, razorpay_order_id="order_x"
        )

        response = authenticated_client(resident_user).post(
            self.confirm_url(payment),
            {
                "razorpay_payment_id": "pay_y",
                "razorpay_signature": checkout_signature("order_x", "pay_y"),
            },
            format="json",
        )

        assert response.status_code == 200
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID
        assert payment.paid_at is not None

    def test_an_unsigned_confirmation_is_refused(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        """Without this, any resident marks their own payments as paid."""
        payment = make_payment(
            society, resident, worker, razorpay_order_id="order_x"
        )

        response = authenticated_client(resident_user).post(
            self.confirm_url(payment),
            {"razorpay_payment_id": "pay_y", "razorpay_signature": "0" * 64},
            format="json",
        )

        assert response.status_code == 400
        payment.refresh_from_db()
        assert payment.status != PaymentStatus.PAID

    def test_confirming_twice_is_idempotent(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        """paid_at decides which month's summary this lands in."""
        payment = make_payment(
            society, resident, worker, razorpay_order_id="order_x"
        )
        client = authenticated_client(resident_user)
        payload = {
            "razorpay_payment_id": "pay_y",
            "razorpay_signature": checkout_signature("order_x", "pay_y"),
        }

        client.post(self.confirm_url(payment), payload, format="json")
        payment.refresh_from_db()
        first_paid_at = payment.paid_at

        client.post(self.confirm_url(payment), payload, format="json")
        payment.refresh_from_db()

        assert payment.paid_at == first_paid_at

    def test_a_settled_payment_cannot_be_marked_failed(
        self, society, resident, worker
    ):
        payment = make_payment(society, resident, worker)
        payment.mark_paid(razorpay_payment_id="pay_1")

        assert payment.mark_failed(reason="late failure webhook") is False
        assert payment.status == PaymentStatus.PAID

    def test_another_resident_cannot_confirm_someone_elses_payment(
        self, authenticated_client, society, resident, worker, django_user_model
    ):
        payment = make_payment(
            society, resident, worker, razorpay_order_id="order_x"
        )
        tower = Tower.objects.create(society=society, name="B", floors=2)
        other_flat = Flat.objects.create(tower=tower, number="101", floor=1)
        other = django_user_model.objects.create_user(
            phone_number="9800000051", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=True,
        )
        Resident.objects.create(user=other, flat=other_flat, is_primary=True)

        response = authenticated_client(other).post(
            self.confirm_url(payment),
            {
                "razorpay_payment_id": "pay_y",
                "razorpay_signature": checkout_signature("order_x", "pay_y"),
            },
            format="json",
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 8.1 Webhooks
# ---------------------------------------------------------------------------


class TestWebhooks:
    URL = "v1:payments:webhook"

    def send(self, api_client, payload: dict, *, event_id="evt_1", signature=None):
        body = json.dumps(payload).encode()
        return api_client.post(
            reverse(self.URL),
            data=body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature or webhook_signature(body),
            HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def captured(self, order_id="order_x", payment_id="pay_y"):
        return {
            "event": "payment.captured",
            "payload": {
                "payment": {"entity": {"id": payment_id, "order_id": order_id}}
            },
        }

    def test_a_signed_webhook_settles_the_payment(
        self, api_client, society, resident, worker
    ):
        payment = make_payment(society, resident, worker, razorpay_order_id="order_x")

        response = self.send(api_client, self.captured())

        assert response.status_code == 200
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID

    def test_an_unsigned_webhook_is_rejected(
        self, api_client, society, resident, worker
    ):
        payment = make_payment(society, resident, worker, razorpay_order_id="order_x")

        response = self.send(api_client, self.captured(), signature="0" * 64)

        assert response.status_code == 401
        payment.refresh_from_db()
        assert payment.status != PaymentStatus.PAID

    def test_an_invalid_signature_is_still_recorded(self, api_client):
        """A run of these is someone probing; an operator should see it."""
        self.send(api_client, self.captured(), signature="0" * 64, event_id="evt_bad")

        event = WebhookEvent.objects.get(event_id="evt_bad")
        assert event.signature_valid is False

    def test_a_replayed_webhook_does_not_settle_twice(
        self, api_client, society, resident, worker
    ):
        """Razorpay retries until it gets a 2xx."""
        payment = make_payment(society, resident, worker, razorpay_order_id="order_x")

        self.send(api_client, self.captured(), event_id="evt_1")
        payment.refresh_from_db()
        first_paid_at = payment.paid_at

        second = self.send(api_client, self.captured(), event_id="evt_1")

        assert second.status_code == 200
        payment.refresh_from_db()
        assert payment.paid_at == first_paid_at
        assert WebhookEvent.objects.filter(event_id="evt_1").count() == 1

    def test_a_failure_webhook_records_the_reason(
        self, api_client, society, resident, worker
    ):
        payment = make_payment(society, resident, worker, razorpay_order_id="order_x")

        self.send(
            api_client,
            {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_y",
                            "order_id": "order_x",
                            "error_description": "Card declined",
                        }
                    }
                },
            },
        )

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.FAILED
        assert payment.failure_reason == "Card declined"

    def test_a_webhook_for_an_unknown_order_is_recorded_not_dropped(self, api_client):
        response = self.send(api_client, self.captured(order_id="order_nonexistent"))

        assert response.status_code == 200
        event = WebhookEvent.objects.get()
        assert event.processed is True
        assert "No matching payment" in event.process_error

    def test_the_raw_body_is_what_gets_verified(
        self, api_client, society, resident, worker
    ):
        """Re-serialising the parsed JSON would reject every genuine webhook."""
        make_payment(society, resident, worker, razorpay_order_id="order_x")

        # Deliberately unusual spacing: a signature over these exact bytes must
        # verify, which it only can if the raw body is used.
        body = json.dumps(self.captured(), separators=(", ", " : ")).encode()
        response = api_client.post(
            reverse(self.URL),
            data=body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=webhook_signature(body),
            HTTP_X_RAZORPAY_EVENT_ID="evt_spacing",
        )

        assert response.status_code == 200

    def test_the_webhook_needs_no_authentication(self, api_client, society, resident, worker):
        """Razorpay's servers have no session — the signature replaces it."""
        make_payment(society, resident, worker, razorpay_order_id="order_x")
        response = self.send(api_client, self.captured())
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Attendance-driven salary basis
# ---------------------------------------------------------------------------


class TestSalaryBasis:
    def attend(self, engagement, day: dt.date):
        AttendanceEvent.objects.create(
            society=engagement.society,
            worker=engagement.worker,
            engagement=engagement,
            direction=Direction.ENTRY,
            method=VerificationMethod.QR,
            decision=Decision.ALLOWED,
            occurred_at=timezone.make_aware(dt.datetime.combine(day, dt.time(9, 5))),
        )

    def test_full_attendance_suggests_the_full_rate(self, engagement):
        # Monday 3 Aug 2026 through the following Sunday: Mon/Wed/Fri.
        start, end = dt.date(2026, 8, 3), dt.date(2026, 8, 9)
        for day in (dt.date(2026, 8, 3), dt.date(2026, 8, 5), dt.date(2026, 8, 7)):
            self.attend(engagement, day)

        basis = salary_basis(engagement, period_start=start, period_end=end)

        assert basis.expected_visits == 3
        assert basis.attended_visits == 3
        assert basis.suggested_paise == basis.full_rate_paise
        assert basis.is_full

    def test_partial_attendance_pro_rates(self, engagement):
        start, end = dt.date(2026, 8, 3), dt.date(2026, 8, 9)
        self.attend(engagement, dt.date(2026, 8, 3))
        self.attend(engagement, dt.date(2026, 8, 5))

        basis = salary_basis(engagement, period_start=start, period_end=end)

        assert basis.expected_visits == 3
        assert basis.attended_visits == 2
        assert basis.suggested_paise == 400000 * 2 // 3
        assert not basis.is_full

    def test_two_entries_in_one_day_count_once(self, engagement):
        """Payroll counts days, not gate passes."""
        start, end = dt.date(2026, 8, 3), dt.date(2026, 8, 9)
        self.attend(engagement, dt.date(2026, 8, 3))
        self.attend(engagement, dt.date(2026, 8, 3))

        basis = salary_basis(engagement, period_start=start, period_end=end)
        assert basis.attended_visits == 1

    def test_a_denied_entry_is_not_attendance(self, engagement):
        start, end = dt.date(2026, 8, 3), dt.date(2026, 8, 9)
        AttendanceEvent.objects.create(
            society=engagement.society,
            worker=engagement.worker,
            engagement=engagement,
            direction=Direction.ENTRY,
            method=VerificationMethod.QR,
            decision=Decision.DENIED,
            decision_reason="Pass revoked",
            occurred_at=timezone.make_aware(
                dt.datetime.combine(dt.date(2026, 8, 3), dt.time(9, 5))
            ),
        )

        basis = salary_basis(engagement, period_start=start, period_end=end)
        assert basis.attended_visits == 0

    def test_no_scheduled_visits_suggests_the_full_rate(self, engagement):
        """Suggesting zero would be as wrong as suggesting everything."""
        engagement.days_of_week = [6]  # Sunday only
        engagement.save(update_fields=["days_of_week"])

        # A Monday-to-Saturday window contains no Sunday.
        basis = salary_basis(
            engagement,
            period_start=dt.date(2026, 8, 3),
            period_end=dt.date(2026, 8, 8),
        )

        assert basis.expected_visits == 0
        assert basis.suggested_paise == basis.full_rate_paise

    def test_the_basis_explains_itself(self, engagement):
        """A number a resident cannot account for is one they will not trust."""
        basis = salary_basis(
            engagement,
            period_start=dt.date(2026, 8, 3),
            period_end=dt.date(2026, 8, 9),
        )
        assert basis.explain().strip()


# ---------------------------------------------------------------------------
# 8.1 / 8.4 Creating payments
# ---------------------------------------------------------------------------


class TestCreatingPayments:
    def test_resident_pays_a_month_of_salary(
        self, authenticated_client, resident_user, engagement
    ):
        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-engagement"),
            {
                "engagement": engagement.pk,
                "period_start": "2026-08-03",
                "period_end": "2026-08-09",
                "amount_paise": 400000,
            },
            format="json",
        )

        assert response.status_code == 201
        payment = Payment.objects.get()
        assert payment.kind == PaymentKind.ENGAGEMENT_SALARY
        assert payment.amount_paise == 400000

    def test_the_amount_defaults_to_the_attendance_suggestion(
        self, authenticated_client, resident_user, engagement
    ):
        """Omitting the amount accepts what attendance implies."""
        AttendanceEvent.objects.create(
            society=engagement.society,
            worker=engagement.worker,
            engagement=engagement,
            direction=Direction.ENTRY,
            method=VerificationMethod.QR,
            decision=Decision.ALLOWED,
            occurred_at=timezone.make_aware(
                dt.datetime.combine(dt.date(2026, 8, 3), dt.time(9, 5))
            ),
        )

        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-engagement"),
            {
                "engagement": engagement.pk,
                "period_start": "2026-08-03",
                "period_end": "2026-08-09",
            },
            format="json",
        )

        assert response.status_code == 201
        # One of three scheduled visits attended.
        assert response.data["basis"]["attended_visits"] == 1
        assert Payment.objects.get().amount_paise == 400000 // 3

    def test_a_month_with_no_attendance_is_refused_rather_than_billed_at_zero(
        self, authenticated_client, resident_user, engagement
    ):
        """The suggestion pro-rates to nothing, and a zero payment is not a
        payment. A resident who wants to pay anyway says so explicitly."""
        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-engagement"),
            {
                "engagement": engagement.pk,
                "period_start": "2026-08-03",
                "period_end": "2026-08-09",
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "nothing_to_pay"
        assert not Payment.objects.exists()

    def test_an_explicit_amount_overrides_the_suggestion(
        self, authenticated_client, resident_user, engagement
    ):
        """Attendance informs the figure; the resident decides it."""
        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-engagement"),
            {
                "engagement": engagement.pk,
                "period_start": "2026-08-03",
                "period_end": "2026-08-09",
                "amount_paise": 400000,
            },
            format="json",
        )

        assert response.status_code == 201
        assert Payment.objects.get().amount_paise == 400000

    def test_a_tip_rides_on_the_same_charge(
        self, authenticated_client, resident_user, booking
    ):
        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-booking"),
            {"booking": booking.pk, "tip_paise": 5000},
            format="json",
        )

        assert response.status_code == 201
        payment = Payment.objects.get()
        assert payment.amount_paise == 200000  # ₹2000 quoted
        assert payment.tip_paise == 5000
        assert payment.total_paise == 205000

    def test_a_booking_price_crosses_from_rupees_to_paise_exactly_once(
        self, authenticated_client, resident_user, booking
    ):
        authenticated_client(resident_user).post(
            reverse("v1:payments:pay-booking"), {"booking": booking.pk}, format="json"
        )

        assert Payment.objects.get().amount_paise == booking.quoted_price * 100

    def test_a_non_primary_resident_cannot_pay(
        self, authenticated_client, resident_user, resident, booking
    ):
        resident.is_primary = False
        resident.save(update_fields=["is_primary"])

        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-booking"), {"booking": booking.pk}, format="json"
        )
        assert response.status_code == 403

    def test_a_worker_cannot_create_a_payment(
        self, authenticated_client, worker_user, booking
    ):
        response = authenticated_client(worker_user).post(
            reverse("v1:payments:pay-booking"), {"booking": booking.pk}, format="json"
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 8.2 Ledger visibility
# ---------------------------------------------------------------------------


class TestLedger:
    URL = "v1:payments:payment-list"

    def test_both_parties_see_the_payment(
        self, authenticated_client, resident_user, worker_user, society, resident, worker
    ):
        make_payment(society, resident, worker)

        assert authenticated_client(resident_user).get(reverse(self.URL)).data["count"] == 1
        assert authenticated_client(worker_user).get(reverse(self.URL)).data["count"] == 1

    def test_an_unrelated_worker_sees_nothing(
        self, authenticated_client, society, resident, worker, django_user_model
    ):
        make_payment(society, resident, worker)
        other = django_user_model.objects.create_user(
            phone_number="9800000052", password="test-pass-12345",
            role=Role.WORKER, society=society, is_approved=True,
        )
        WorkerProfile.objects.create(user=other, photo="p.jpg")

        response = authenticated_client(other).get(reverse(self.URL))
        assert response.data["count"] == 0

    def test_a_guard_sees_no_payments(
        self, authenticated_client, guard_user, society, resident, worker
    ):
        """A guard has no business reading anyone's wages."""
        make_payment(society, resident, worker)

        response = authenticated_client(guard_user).get(reverse(self.URL))
        assert response.status_code == 403

    def test_an_administrator_sees_their_society(
        self, authenticated_client, admin_user, society, resident, worker
    ):
        make_payment(society, resident, worker)

        response = authenticated_client(admin_user).get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_the_ledger_never_exposes_a_signature(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        payment = make_payment(society, resident, worker)
        payment.mark_paid(razorpay_payment_id="pay_1", signature="secret_signature")

        body = authenticated_client(resident_user).get(reverse(self.URL)).json()
        assert "secret_signature" not in json.dumps(body)


# ---------------------------------------------------------------------------
# 8.3 Summaries and receipts
# ---------------------------------------------------------------------------


class TestSummaries:
    def settle(self, society, resident, worker, *, amount, tip=0, when=None):
        payment = make_payment(
            society, resident, worker, amount_paise=amount, tip_paise=tip
        )
        payment.mark_paid(razorpay_payment_id="pay_x")
        if when:
            Payment.objects.filter(pk=payment.pk).update(paid_at=when)
        return payment

    def test_the_summary_totals_settled_payments(
        self, society, resident, worker
    ):
        self.settle(society, resident, worker, amount=200000)
        self.settle(society, resident, worker, amount=150000, tip=5000)

        today = timezone.localdate()
        summary = build_monthly_summary(worker, year=today.year, month=today.month)

        assert summary.payment_count == 2
        assert summary.total_paise == 355000
        assert summary.tips_paise == 5000

    def test_each_line_addresses_its_payment(self, society, resident, worker):
        """The receipt number is for humans; the id is what opens the receipt."""
        payment = self.settle(society, resident, worker, amount=200000)

        today = timezone.localdate()
        summary = build_monthly_summary(worker, year=today.year, month=today.month)

        assert summary.lines[0].payment_id == str(payment.pk)
        assert summary.as_dict()["lines"][0]["payment_id"] == str(payment.pk)

    def test_unsettled_payments_are_excluded(self, society, resident, worker):
        """A summary is about money that actually arrived."""
        make_payment(society, resident, worker, amount_paise=999999)

        today = timezone.localdate()
        summary = build_monthly_summary(worker, year=today.year, month=today.month)

        assert summary.payment_count == 0

    def test_another_month_is_excluded(self, society, resident, worker):
        self.settle(
            society, resident, worker, amount=200000,
            when=timezone.now() - dt.timedelta(days=90),
        )

        today = timezone.localdate()
        summary = build_monthly_summary(worker, year=today.year, month=today.month)
        assert summary.payment_count == 0

    def test_csv_renders_with_the_total(self, society, resident, worker):
        self.settle(society, resident, worker, amount=200000)

        today = timezone.localdate()
        csv_text = render_csv(
            build_monthly_summary(worker, year=today.year, month=today.month)
        )

        assert "Sathify" in csv_text
        assert "₹2,000.00" in csv_text

    def test_pdf_renders(self, society, resident, worker):
        self.settle(society, resident, worker, amount=200000)

        today = timezone.localdate()
        pdf = render_pdf(
            build_monthly_summary(worker, year=today.year, month=today.month)
        )

        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 500

    def test_an_empty_month_still_renders(self, worker):
        """A worker with no income that month still needs the statement."""
        pdf = render_pdf(build_monthly_summary(worker, year=2020, month=1))
        assert pdf.startswith(b"%PDF")

    def test_worker_reads_their_own_summary(
        self, authenticated_client, worker_user, society, resident, worker
    ):
        self.settle(society, resident, worker, amount=200000)

        response = authenticated_client(worker_user).get(
            reverse("v1:payments:summary")
        )

        assert response.status_code == 200
        assert response.data["total_paise"] == 200000

    def test_a_resident_cannot_read_a_workers_income_statement(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        self.settle(society, resident, worker, amount=200000)

        response = authenticated_client(resident_user).get(
            reverse("v1:payments:summary")
        )
        assert response.status_code == 404

    def test_a_receipt_is_available_to_both_parties(
        self, authenticated_client, resident_user, worker_user, society, resident, worker
    ):
        payment = self.settle(society, resident, worker, amount=200000)
        url = reverse("v1:payments:receipt", args=[payment.pk])

        assert authenticated_client(resident_user).get(url).status_code == 200
        worker_receipt = authenticated_client(worker_user).get(url)
        assert worker_receipt.data["total_display"] == "₹2,000.00"


# ---------------------------------------------------------------------------
# 8.5 Replacement split
# ---------------------------------------------------------------------------


class TestReplacementSplit:
    def url(self, engagement):
        return reverse("v1:payments:replacement-split", args=[engagement.pk])

    def test_the_default_pays_the_replacement_in_full(
        self, authenticated_client, resident_user, engagement
    ):
        """Taking a share off the person who did the work needs prior agreement."""
        response = authenticated_client(resident_user).get(self.url(engagement))

        assert response.data["replacement_share_percent"] == 100
        assert response.data["is_customised"] is False

    def test_the_resident_sets_a_rule(
        self, authenticated_client, resident_user, engagement
    ):
        response = authenticated_client(resident_user).put(
            self.url(engagement),
            {"replacement_share_percent": 70, "note": "Regular worker arranges cover."},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["replacement_share_percent"] == 70
        assert response.data["original_share_percent"] == 30

    def test_the_shares_always_total_one_hundred(self, engagement):
        split = ReplacementSplit.objects.create(
            engagement=engagement, replacement_share_percent=65
        )
        assert split.replacement_share_percent + split.original_share_percent == 100

    def test_splitting_a_day_rate_loses_no_paise(self, engagement):
        """Rounding against the person who turned up is the wrong default."""
        split = ReplacementSplit.objects.create(
            engagement=engagement, replacement_share_percent=33
        )
        replacement, original = split.split(10000)

        assert replacement + original == 10000
        assert replacement == 3300

    def test_a_remainder_goes_to_the_replacement(self, engagement):
        split = ReplacementSplit.objects.create(
            engagement=engagement, replacement_share_percent=33
        )
        replacement, original = split.split(101)

        assert replacement + original == 101
        assert replacement == 33
        assert original == 68

    def test_a_share_over_one_hundred_is_rejected(
        self, authenticated_client, resident_user, engagement
    ):
        response = authenticated_client(resident_user).put(
            self.url(engagement), {"replacement_share_percent": 140}, format="json"
        )
        assert response.status_code == 400

    def test_the_worker_cannot_set_the_rule(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).put(
            self.url(engagement), {"replacement_share_percent": 100}, format="json"
        )
        assert response.status_code == 403

    def test_the_worker_can_read_it(
        self, authenticated_client, worker_user, engagement
    ):
        """They are entitled to know how they would be paid for covering."""
        response = authenticated_client(worker_user).get(self.url(engagement))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 8.6 Disputes
# ---------------------------------------------------------------------------


class TestDisputes:
    def raise_url(self, payment):
        return reverse("v1:payments:raise-dispute", args=[payment.pk])

    def test_a_worker_raises_a_dispute(
        self, authenticated_client, worker_user, society, resident, worker
    ):
        payment = make_payment(society, resident, worker)

        response = authenticated_client(worker_user).post(
            self.raise_url(payment),
            {"reason": "not_paid", "description": "I never received this payment."},
            format="json",
        )

        assert response.status_code == 201
        assert PaymentDispute.objects.count() == 1

    def test_a_description_must_say_something(
        self, authenticated_client, worker_user, society, resident, worker
    ):
        """An administrator cannot mediate 'it's wrong'."""
        payment = make_payment(society, resident, worker)

        response = authenticated_client(worker_user).post(
            self.raise_url(payment),
            {"reason": "wrong_amount", "description": "bad"},
            format="json",
        )
        assert response.status_code == 400

    def test_raising_twice_is_refused(
        self, authenticated_client, worker_user, society, resident, worker
    ):
        """Otherwise tapping the button floods the administrator's queue."""
        payment = make_payment(society, resident, worker)
        client = authenticated_client(worker_user)
        payload = {"reason": "not_paid", "description": "I never received this payment."}

        assert client.post(self.raise_url(payment), payload, format="json").status_code == 201
        second = client.post(self.raise_url(payment), payload, format="json")
        assert second.status_code == 409

    def test_the_other_party_is_told_about_it(
        self, authenticated_client, resident_user, worker_user, society, resident, worker
    ):
        """Being disputed without being told would be worse than useless."""
        payment = make_payment(society, resident, worker)
        authenticated_client(worker_user).post(
            self.raise_url(payment),
            {"reason": "not_paid", "description": "I never received this payment."},
            format="json",
        )

        response = authenticated_client(resident_user).get(
            reverse("v1:payments:dispute-list")
        )
        assert response.data["count"] == 1

    def test_an_administrator_resolves_it(
        self, authenticated_client, admin_user, worker_user, society, resident, worker
    ):
        payment = make_payment(society, resident, worker)
        authenticated_client(worker_user).post(
            self.raise_url(payment),
            {"reason": "not_paid", "description": "I never received this payment."},
            format="json",
        )
        dispute = PaymentDispute.objects.get()

        response = authenticated_client(admin_user).post(
            reverse("v1:payments:dispute-resolve", args=[dispute.pk]),
            {"upheld": True, "resolution": "Paid in cash on the 5th; confirmed."},
            format="json",
        )

        assert response.status_code == 200
        dispute.refresh_from_db()
        assert dispute.status == DisputeStatus.RESOLVED
        assert dispute.resolved_by == admin_user

    def test_resolving_twice_is_refused(
        self, authenticated_client, admin_user, worker_user, society, resident, worker
    ):
        payment = make_payment(society, resident, worker)
        authenticated_client(worker_user).post(
            self.raise_url(payment),
            {"reason": "not_paid", "description": "I never received this payment."},
            format="json",
        )
        dispute = PaymentDispute.objects.get()
        client = authenticated_client(admin_user)
        url = reverse("v1:payments:dispute-resolve", args=[dispute.pk])
        payload = {"upheld": True, "resolution": "Confirmed paid."}

        assert client.post(url, payload, format="json").status_code == 200
        assert client.post(url, payload, format="json").status_code == 409

    def test_a_resolved_dispute_frees_the_constraint(
        self, authenticated_client, admin_user, worker_user, society, resident, worker
    ):
        """A second, genuinely new dispute must still be possible later."""
        payment = make_payment(society, resident, worker)
        client = authenticated_client(worker_user)
        payload = {"reason": "not_paid", "description": "I never received this payment."}
        client.post(self.raise_url(payment), payload, format="json")

        dispute = PaymentDispute.objects.get()
        authenticated_client(admin_user).post(
            reverse("v1:payments:dispute-resolve", args=[dispute.pk]),
            {"upheld": False, "resolution": "Records show it was paid."},
            format="json",
        )

        second = client.post(self.raise_url(payment), payload, format="json")
        assert second.status_code == 201
