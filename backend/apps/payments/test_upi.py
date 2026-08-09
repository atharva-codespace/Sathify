"""
Module 8.9 — the Razorpay-hosted UPI QR.

The gateway call is stubbed rather than mocked at the HTTP library: these tests
are about what *we* do with the response — store the id so a webhook can find
the row again, reuse a live code, replace an expired one — not about Razorpay's
own behaviour.

The webhook half is the part that earns the rewrite. A QR credit arrives with
the QR id on a sibling entity, not on the payment, so the existing matcher could
never have found the row; a credit that resolves to nothing is a household who
paid and an app that says unpaid.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.payments import gateway, upi
from apps.payments.models import (
    Payment,
    PaymentKind,
    PaymentStatus,
    SettledVia,
)
from apps.payments.services import apply_webhook, record_webhook
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import WorkerProfile

pytestmark = pytest.mark.django_db

QR_ID = "qr_HMsVL8HOpbMcjU"
QR_IMAGE = "https://rzp.io/i/BWcUVrLp"


@pytest.fixture
def payment(society, resident_user, worker_user):
    tower = Tower.objects.create(society=society, name="A", floors=4)
    flat = Flat.objects.create(tower=tower, number="301", floor=3)
    resident = Resident.objects.create(user=resident_user, flat=flat, is_primary=True)
    worker = WorkerProfile.objects.create(
        user=worker_user, photo="workers/x.jpg", trust_score=70
    )
    return Payment.objects.create(
        society=society, resident=resident, worker=worker,
        kind=PaymentKind.BOOKING, amount_paise=45000, tip_paise=5000,
    )


@pytest.fixture
def razorpay(monkeypatch):
    """A stand-in for Razorpay's QR endpoint that records what we sent it."""
    calls: list[dict] = []

    def _create(*, amount_paise, reference, description="", notes=None):
        calls.append(
            {
                "amount_paise": amount_paise,
                "reference": reference,
                "description": description,
                "notes": notes or {},
            }
        )
        return {
            "id": f"{QR_ID}{len(calls)}",
            "image_url": QR_IMAGE,
            "payment_amount": amount_paise,
            "status": "active",
            "close_by": int(timezone.now().timestamp()) + gateway.QR_VALID_SECONDS,
        }

    def _no_network(**kwargs):  # pragma: no cover - guard, never expected
        raise AssertionError(
            "a test reached the real Razorpay API; stub the call you added"
        )

    monkeypatch.setattr(gateway, "create_qr_code", _create)
    # Guarded rather than left alone: the QR path now falls back to payment
    # links, so an unstubbed test would quietly open a real link in the
    # Razorpay account. One did, before this guard existed.
    monkeypatch.setattr(gateway, "create_payment_link", _no_network)
    monkeypatch.setattr(gateway, "is_configured", lambda: True)
    return calls


class TestOpeningAQr:
    def test_it_asks_razorpay_for_the_exact_total(self, payment, razorpay):
        upi.qr_for_payment(payment)

        assert razorpay[0]["amount_paise"] == payment.total_paise == 50000
        assert razorpay[0]["reference"] == str(payment.pk)
        # Echoed back on the webhook, and the fallback route home.
        assert razorpay[0]["notes"]["receipt_number"] == payment.receipt_number

    def test_the_qr_id_is_stored_so_the_webhook_can_find_the_row(
        self, payment, razorpay
    ):
        """The whole reason automatic settlement works. Razorpay puts the QR id
        on the webhook and nothing else identifies the payment."""
        qr = upi.qr_for_payment(payment)

        payment.refresh_from_db()
        assert payment.razorpay_qr_code_id == qr.qr_code_id
        assert payment.razorpay_qr_image_url == QR_IMAGE
        assert payment.qr_expires_at is not None

    def test_reopening_reuses_the_live_code(self, payment, razorpay):
        """A resident re-opening the sheet must not invalidate the code they
        already photographed — nor litter the Razorpay account."""
        first = upi.qr_for_payment(payment)
        second = upi.qr_for_payment(payment)

        assert first.qr_code_id == second.qr_code_id
        assert len(razorpay) == 1

    def test_an_expired_code_is_replaced_rather_than_handed_back(
        self, payment, razorpay
    ):
        """A dead code is discovered only after walking to another phone."""
        upi.qr_for_payment(payment)
        Payment.objects.filter(pk=payment.pk).update(
            qr_expires_at=timezone.now() - dt.timedelta(seconds=1)
        )
        payment.refresh_from_db()

        upi.qr_for_payment(payment)

        assert len(razorpay) == 2

    def test_a_code_about_to_lapse_is_also_replaced(self, payment, razorpay):
        upi.qr_for_payment(payment)
        Payment.objects.filter(pk=payment.pk).update(
            qr_expires_at=timezone.now() + dt.timedelta(seconds=20)
        )
        payment.refresh_from_db()

        upi.qr_for_payment(payment)
        assert len(razorpay) == 2

    def test_a_settled_payment_has_no_qr(self, payment, razorpay):
        payment.mark_paid(razorpay_payment_id="pay_x", signature="s")

        with pytest.raises(upi.UpiNotConfigured):
            upi.qr_for_payment(payment)

    def test_an_unconfigured_gateway_refuses(self, payment, monkeypatch):
        monkeypatch.setattr(gateway, "is_configured", lambda: False)

        with pytest.raises(upi.UpiNotConfigured):
            upi.qr_for_payment(payment)


class TestTheFallbackToAPaymentLink:
    """What happens on an account without the QR Codes API — which is most of
    them, since it is a per-account switch that is off by default.

    Confirmed on this project's own Razorpay account: `/v1/payments/qr_codes`
    returns 400 while `/v1/orders` and `/v1/payment_links` return 200. Without
    this path the payment screen has no code to show at all.
    """

    LINK_ID = "plink_TNlSDIghGciTLq"
    LINK_URL = "https://rzp.io/rzp/czeSYjUn"

    @pytest.fixture
    def no_qr_api(self, monkeypatch):
        """QR Codes refused, Payment Links available."""
        links: list[dict] = []

        def _refuse(**kwargs):
            raise gateway.GatewayError(
                "The requested URL was not found on the server."
            )

        def _link(*, amount_paise, reference, description="", notes=None):
            links.append({"amount_paise": amount_paise, "reference": reference})
            return {
                "id": self.LINK_ID,
                "short_url": self.LINK_URL,
                "amount": amount_paise,
                "reference_id": reference,
                "status": "created",
                "expire_by": int(timezone.now().timestamp())
                + gateway.QR_VALID_SECONDS,
            }

        monkeypatch.setattr(gateway, "is_configured", lambda: True)
        monkeypatch.setattr(gateway, "create_qr_code", _refuse)
        monkeypatch.setattr(gateway, "create_payment_link", _link)
        return links

    def test_a_refused_qr_api_falls_back_rather_than_failing(
        self, payment, no_qr_api
    ):
        qr = upi.qr_for_payment(payment)

        assert qr.kind == "payment_link"
        # No hosted image on this path — the client encodes the URL itself.
        assert qr.image_url == ""
        assert qr.payload == self.LINK_URL
        assert no_qr_api[0]["amount_paise"] == payment.total_paise

    def test_the_link_carries_this_payment_as_its_reference(
        self, payment, no_qr_api
    ):
        """What the `payment_link.paid` webhook is matched on. A link that
        resolved to nothing would strand a real payment."""
        upi.qr_for_payment(payment)

        assert no_qr_api[0]["reference"] == str(payment.pk)
        payment.refresh_from_db()
        assert payment.razorpay_payment_link_id == self.LINK_ID
        assert payment.razorpay_payment_link_url == self.LINK_URL

    def test_the_link_is_reused_while_it_is_live(self, payment, no_qr_api):
        upi.qr_for_payment(payment)
        upi.qr_for_payment(payment)

        assert len(no_qr_api) == 1

    def test_a_paid_link_settles_the_payment(self, payment, no_qr_api):
        """The point of routing the fallback through Razorpay too: it still
        settles itself, through a signed webhook, with nobody in the loop."""
        upi.qr_for_payment(payment)
        payment.refresh_from_db()

        event, _ = record_webhook(
            event_id="evt_link_1",
            event_type="payment_link.paid",
            payload={
                "event": "payment_link.paid",
                "payload": {
                    "payment_link": {
                        "entity": {
                            "id": self.LINK_ID,
                            "reference_id": str(payment.pk),
                            "status": "paid",
                        }
                    },
                    # A link creates its *own* order, so this id is one we have
                    # never seen — which is exactly why the ordinary matcher
                    # cannot be used here.
                    "order": {"entity": {"id": "order_neverSeenByUs"}},
                    "payment": {
                        "entity": {
                            "id": "pay_LinkPaid1",
                            "order_id": "order_neverSeenByUs",
                            "status": "captured",
                            "method": "upi",
                        }
                    },
                },
            },
            signature_valid=True,
        )
        apply_webhook(event)

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID
        assert payment.settled_via == SettledVia.WEBHOOK
        assert payment.razorpay_payment_id == "pay_LinkPaid1"

    def test_a_link_event_for_an_unknown_reference_matches_nothing(
        self, payment, no_qr_api
    ):
        event, _ = record_webhook(
            event_id="evt_link_stray",
            event_type="payment_link.paid",
            payload={
                "event": "payment_link.paid",
                "payload": {
                    "payment_link": {"entity": {"id": "plink_stranger"}},
                    "payment": {"entity": {"id": "pay_x"}},
                },
            },
            signature_valid=True,
        )

        assert apply_webhook(event) is None


class TestTheEndpoint:
    def test_it_serves_the_hosted_image(
        self, authenticated_client, resident_user, payment, razorpay
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:payments:payment-upi", args=[payment.pk])
        )

        assert response.status_code == 200
        assert response.data["image_url"] == QR_IMAGE
        assert response.data["amount_display"] == "₹500.00"
        # Tells the client not to claim success when the sheet closes.
        assert response.data["settles"] == "webhook"
        assert any(app["key"] == "famapp" for app in response.data["apps"])

    def test_an_unreachable_gateway_is_a_503_not_a_broken_image(
        self, authenticated_client, resident_user, payment, monkeypatch
    ):
        """And it must NOT try the fallback.

        "Razorpay is unreachable" means the next call fails identically, so
        falling through would spend a second 20-second timeout before showing
        the same error. Only a refusal of the QR API itself is worth retrying
        elsewhere.
        """
        monkeypatch.setattr(gateway, "is_configured", lambda: True)

        def _boom(**kwargs):
            raise gateway.GatewayUnavailable("Could not reach the payment provider.")

        def _must_not_run(**kwargs):
            raise AssertionError("a network failure must not fall back")

        monkeypatch.setattr(gateway, "create_qr_code", _boom)
        monkeypatch.setattr(gateway, "create_payment_link", _must_not_run)

        response = authenticated_client(resident_user).get(
            reverse("v1:payments:payment-upi", args=[payment.pk])
        )
        assert response.status_code == 503

    def test_another_household_cannot_open_one(
        self, authenticated_client, django_user_model, society, payment, razorpay
    ):
        from apps.accounts.models import Role

        outsider = django_user_model.objects.create_user(
            phone_number="9899999999", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=True,
        )

        response = authenticated_client(outsider).get(
            reverse("v1:payments:payment-upi", args=[payment.pk])
        )
        assert response.status_code == 404


def credited_payload(payment, *, qr_id=None, razorpay_payment_id="pay_QRcredit1"):
    """The shape Razorpay sends on `qr_code.credited`.

    Note where the QR id lives: on the ``qr_code`` entity, **not** the payment.
    That is exactly why this event needed its own resolver.
    """
    return {
        "event": "qr_code.credited",
        "payload": {
            "qr_code": {
                "entity": {
                    "id": qr_id or payment.razorpay_qr_code_id,
                    "entity": "qr_code",
                    "status": "closed",
                    "notes": {"reference": str(payment.pk)},
                }
            },
            "payment": {
                "entity": {
                    "id": razorpay_payment_id,
                    "amount": payment.total_paise,
                    "currency": "INR",
                    "status": "captured",
                    "method": "upi",
                    "vpa": "someone@okhdfcbank",
                    "notes": {"reference": str(payment.pk)},
                }
            },
        },
    }


class TestAutomaticSettlement:
    """The point of routing through Razorpay: no human in the loop."""

    def _deliver(self, payload, event_id="evt_qr_1"):
        event, _ = record_webhook(
            event_id=event_id,
            event_type=payload["event"],
            payload=payload,
            signature_valid=True,
        )
        return apply_webhook(event)

    def test_a_credited_qr_settles_the_payment(self, payment, razorpay):
        upi.qr_for_payment(payment)
        payment.refresh_from_db()

        self._deliver(credited_payload(payment))

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID
        assert payment.settled_via == SettledVia.WEBHOOK
        assert payment.razorpay_payment_id == "pay_QRcredit1"

    def test_it_is_matched_by_the_qr_id_not_an_order(self, payment, razorpay):
        """A QR payment has no order, so the old matcher had nothing to work
        with. This is the regression that would silently strand every scan."""
        upi.qr_for_payment(payment)
        payment.refresh_from_db()
        assert payment.razorpay_order_id == ""

        matched = self._deliver(credited_payload(payment))

        assert matched == payment

    def test_the_notes_reference_is_a_fallback_when_the_id_is_unknown(
        self, payment, razorpay
    ):
        """For a code opened before the id column existed, or restored from a
        backup — the reference travels on both entities."""
        upi.qr_for_payment(payment)
        payment.refresh_from_db()

        matched = self._deliver(credited_payload(payment, qr_id="qr_neverSeen"))

        assert matched == payment
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID

    def test_a_credit_for_nothing_we_know_is_recorded_not_swallowed(self, razorpay):
        payload = {
            "event": "qr_code.credited",
            "payload": {
                "qr_code": {"entity": {"id": "qr_stranger", "notes": {}}},
                "payment": {"entity": {"id": "pay_x", "notes": {}}},
            },
        }
        assert self._deliver(payload) is None

    def test_settling_by_qr_drives_the_emergency_broadcast(
        self, society, resident_user, django_user_model, razorpay
    ):
        """The reason a stranded QR payment mattered so much: the broadcast is
        triggered by settlement, so an unsettled surcharge reaches nobody."""
        from apps.accounts.models import Role
        from apps.bookings import emergency as emergency_service
        from apps.bookings.models import BookingOffer, BookingStatus, ServiceCategory

        tower = Tower.objects.create(society=society, name="B", floors=4)
        flat = Flat.objects.create(tower=tower, number="401", floor=4)
        resident = Resident.objects.create(
            user=resident_user, flat=flat, is_primary=True
        )
        for index in range(2):
            user = django_user_model.objects.create_user(
                phone_number=f"987700000{index}", password="test-pass-12345",
                role=Role.WORKER, society=society, first_name=f"M{index}",
                last_name="K", is_approved=True,
            )
            WorkerProfile.objects.create(
                user=user, photo="workers/x.jpg", is_available=True,
                trust_score=70, average_rating=4.3,
            )

        moment = timezone.localtime() + dt.timedelta(minutes=20)
        booking, surcharge = emergency_service.raise_emergency(
            resident=resident, society=society,
            category=ServiceCategory.objects.get(slug="emergency-assistance"),
            scheduled_date=moment.date(),
            start_time=moment.time().replace(microsecond=0),
        )
        upi.qr_for_payment(surcharge)
        surcharge.refresh_from_db()
        assert booking.status == BookingStatus.PAYMENT_PENDING

        self._deliver(credited_payload(surcharge), event_id="evt_qr_emergency")

        booking.refresh_from_db()
        assert booking.status == BookingStatus.BROADCAST
        assert BookingOffer.objects.filter(booking=booking).count() == 2

    def test_a_replayed_credit_does_not_settle_twice(self, payment, razorpay):
        upi.qr_for_payment(payment)
        payment.refresh_from_db()

        self._deliver(credited_payload(payment), event_id="evt_dup")
        first_paid_at = Payment.objects.get(pk=payment.pk).paid_at
        self._deliver(credited_payload(payment), event_id="evt_dup_2")

        payment.refresh_from_db()
        # paid_at decides which month's salary summary this lands in.
        assert payment.paid_at == first_paid_at


class TestTheSignedWebhookPath:
    """The secret wiring, end to end over HTTP.

    Everything above calls ``apply_webhook`` directly, which skips the part that
    actually decides whether automatic settlement happens at all: the HMAC check
    in ``RazorpayWebhookView``. With ``RAZORPAY_WEBHOOK_SECRET`` empty — which is
    how this deployment shipped — ``verify_webhook_signature`` returns False for
    every delivery, so a scanned QR would take the money and leave the payment
    unpaid. These go through the real endpoint with a real signature.
    """

    SECRET = "test-webhook-secret-value"

    @pytest.fixture(autouse=True)
    def _configured(self, settings):
        settings.RAZORPAY_SETTINGS = {
            **settings.RAZORPAY_SETTINGS,
            "WEBHOOK_SECRET": self.SECRET,
        }

    @pytest.fixture
    def qr_payment(self, payment):
        payment.razorpay_qr_code_id = QR_ID
        payment.razorpay_qr_image_url = QR_IMAGE
        payment.qr_expires_at = timezone.now() + dt.timedelta(minutes=25)
        payment.save()
        return payment

    def _deliver(self, api_client, payment, *, secret, event_id="evt_http_1"):
        """POST a credit exactly as Razorpay would, signed with ``secret``."""
        import hashlib
        import hmac
        import json

        body = json.dumps(credited_payload(payment)).encode("utf-8")
        signature = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

        return api_client.post(
            reverse("v1:payments:webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature,
            HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def test_a_correctly_signed_credit_settles_the_payment(
        self, api_client, qr_payment
    ):
        response = self._deliver(api_client, qr_payment, secret=self.SECRET)

        assert response.status_code == 200
        qr_payment.refresh_from_db()
        assert qr_payment.status == PaymentStatus.PAID
        assert qr_payment.settled_via == SettledVia.WEBHOOK

    def test_a_forged_signature_is_rejected_and_settles_nothing(
        self, api_client, qr_payment
    ):
        """The whole trust boundary. Anyone can POST to this endpoint — it is
        the only unauthenticated one in the project."""
        response = self._deliver(
            api_client, qr_payment, secret="not-the-real-secret"
        )

        assert response.status_code == 401
        qr_payment.refresh_from_db()
        assert qr_payment.status != PaymentStatus.PAID

    def test_an_unset_secret_rejects_even_a_genuine_delivery(
        self, api_client, qr_payment, settings
    ):
        """The failure this feature shipped with.

        An empty secret makes ``verify_webhook_signature`` return False for
        everything, so the money arrives and the payment stays unpaid forever.
        Pinned as a test so a blank secret fails loudly here rather than quietly
        in production.
        """
        settings.RAZORPAY_SETTINGS = {
            **settings.RAZORPAY_SETTINGS,
            "WEBHOOK_SECRET": "",
        }

        response = self._deliver(api_client, qr_payment, secret=self.SECRET)

        assert response.status_code == 401
        qr_payment.refresh_from_db()
        assert qr_payment.status != PaymentStatus.PAID

    def test_a_replayed_delivery_is_a_no_op(self, api_client, qr_payment):
        """Razorpay retries until it gets a 2xx."""
        self._deliver(api_client, qr_payment, secret=self.SECRET, event_id="evt_r")
        first_paid_at = Payment.objects.get(pk=qr_payment.pk).paid_at

        again = self._deliver(
            api_client, qr_payment, secret=self.SECRET, event_id="evt_r"
        )

        assert again.status_code == 200
        qr_payment.refresh_from_db()
        assert qr_payment.paid_at == first_paid_at


class TestAmountFormatting:
    """Display only now — the amount inside the code is Razorpay's."""

    @pytest.mark.parametrize(
        "paise,expected",
        [(100, "1.00"), (10000, "100.00"), (105050, "1050.50"), (1, "0.01")],
    )
    def test_two_decimal_places(self, paise, expected):
        assert upi.format_amount(paise) == expected

    def test_decimal_not_float(self):
        assert upi.format_amount(99_999_992) == "999999.92"
