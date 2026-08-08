"""
Module 8.7 — platform fees, subscription tiers, tip settlement.

The tests that matter here are the ones about what is *not* charged. A fee that
appears on a wage transfer, or a badge that quietly moves somebody up a search
result, are both the kind of change that looks like a small product tweak and
is actually a decision about whose income the platform takes a share of.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.hiring.models import Engagement
from apps.payments import fees
from apps.payments.models import (
    Payment,
    PaymentKind,
    PaymentStatus,
    SocietySubscription,
    SubscriptionTier,
)
from apps.payments.services import create_payment
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db

ENABLED = override_settings(PLATFORM_FEES_ENABLED=True)


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


class TestFeesShipOff:
    def test_no_fee_is_charged_today(self, society):
        """The column and the calculation ship before the price does."""
        assert fees.fees_enabled() is False
        assert (
            fees.platform_fee_paise(
                kind=PaymentKind.BOOKING, amount_paise=50_000, society=society
            )
            == 0
        )

    def test_the_intended_rate_is_still_declared(self):
        """Turning it on must be a setting, not a code change."""
        assert fees.BOOKING_FEE_RATE == 0.08
        assert fees.BOOKING_FEE_CAP_PAISE == 4_000

    @ENABLED
    def test_one_switch_turns_it_on(self, society):
        assert (
            fees.platform_fee_paise(
                kind=PaymentKind.BOOKING, amount_paise=50_000, society=society
            )
            == 4_000  # 8% of ₹500 is ₹40, exactly at the cap
        )


class TestWhatIsNeverCharged:
    """Each of these would be taking a share of somebody's wage."""

    @ENABLED
    @pytest.mark.parametrize(
        "kind",
        [
            PaymentKind.ENGAGEMENT_SALARY,
            PaymentKind.TIP,
            PaymentKind.REPLACEMENT,
            PaymentKind.REFUND,
        ],
    )
    def test_exempt_kinds_are_never_charged(self, kind, society):
        assert (
            fees.platform_fee_paise(kind=kind, amount_paise=600_000, society=society)
            == 0
        )

    @ENABLED
    def test_a_recurring_salary_carries_no_fee_even_at_scale(self, society):
        """A ₹20,000 salary is still a wage transfer, not a marketplace sale."""
        assert (
            fees.platform_fee_paise(
                kind=PaymentKind.ENGAGEMENT_SALARY,
                amount_paise=2_000_000,
                society=society,
            )
            == 0
        )

    @ENABLED
    def test_the_cap_holds(self, society):
        """A ₹2,000 deep-clean is not twenty times more platform than a ₹100 one."""
        assert (
            fees.platform_fee_paise(
                kind=PaymentKind.BOOKING, amount_paise=200_000, society=society
            )
            == fees.BOOKING_FEE_CAP_PAISE
        )

    @ENABLED
    def test_rounding_favours_the_people_in_the_transaction(self, society):
        # 8% of 1 paise is a fraction; it goes to them, not to Sathify.
        assert (
            fees.platform_fee_paise(
                kind=PaymentKind.BOOKING, amount_paise=1, society=society
            )
            == 0
        )


class TestFeeOnPayment:
    @ENABLED
    def test_the_fee_rides_on_top_and_the_worker_is_untouched(
        self, society, resident, worker
    ):
        """The worker's figure must never move because Sathify changed pricing."""
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=20_000,
        )

        assert payment.platform_fee_paise == 1_600  # 8% of ₹200
        assert payment.worker_receives_paise == 20_000
        assert payment.total_paise == 21_600

    def test_it_is_frozen_at_creation(self, society, resident, worker):
        """A later rate change must not rewrite an old receipt."""
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=20_000,
        )
        assert payment.platform_fee_paise == 0

        with override_settings(PLATFORM_FEES_ENABLED=True):
            payment.refresh_from_db()
            assert payment.platform_fee_paise == 0
            assert payment.total_paise == 20_000


class TestSubscription:
    def test_a_society_with_no_row_is_free_and_fully_working(self, society):
        subscription = SocietySubscription.for_society(society)

        assert subscription.tier == SubscriptionTier.FREE
        assert subscription.is_active is True
        assert subscription.effective_tier == SubscriptionTier.FREE

    def test_free_never_expires(self, society):
        """A permanent state, not a trial.

        A society will not move its attendance records onto a platform that can
        hold them hostage — and an unpaid invoice must never be able to stop a
        worker getting through a gate.
        """
        subscription = SocietySubscription.for_society(society)
        subscription.valid_until = timezone.localdate() - dt.timedelta(days=365)
        subscription.save(update_fields=["valid_until"])

        assert subscription.is_active is True

    def test_a_lapsed_paid_tier_reads_as_free(self, society):
        subscription = SocietySubscription.for_society(society)
        subscription.tier = SubscriptionTier.STANDARD
        subscription.valid_until = timezone.localdate() - dt.timedelta(days=1)
        subscription.save(update_fields=["tier", "valid_until"])

        assert subscription.is_active is False
        assert subscription.effective_tier == SubscriptionTier.FREE
        assert subscription.includes_reports is False

    def test_for_society_is_idempotent(self, society):
        first = SocietySubscription.for_society(society)
        second = SocietySubscription.for_society(society)

        assert first.pk == second.pk
        assert SocietySubscription.objects.filter(society=society).count() == 1

    @ENABLED
    def test_plus_waives_the_booking_fee(self, society):
        subscription = SocietySubscription.for_society(society)
        subscription.tier = SubscriptionTier.PLUS
        subscription.valid_until = timezone.localdate() + dt.timedelta(days=30)
        subscription.save(update_fields=["tier", "valid_until"])
        society.refresh_from_db()

        assert (
            fees.platform_fee_paise(
                kind=PaymentKind.BOOKING, amount_paise=50_000, society=society
            )
            == 0
        )


class TestVerificationBadge:
    def test_a_badge_with_no_end_date_is_not_a_badge(self, worker):
        """Undated is a claim, not a fact."""
        worker.police_verified_at = timezone.localdate()
        worker.save(update_fields=["police_verified_at"])

        assert worker.is_police_verified is False

    def test_a_live_badge_reads_true(self, worker):
        worker.police_verified_at = timezone.localdate() - dt.timedelta(days=30)
        worker.police_verified_until = timezone.localdate() + dt.timedelta(days=335)
        worker.save(
            update_fields=["police_verified_at", "police_verified_until"]
        )

        assert worker.is_police_verified is True

    def test_an_expired_badge_stops_reading_true(self, worker):
        worker.police_verified_at = timezone.localdate() - dt.timedelta(days=800)
        worker.police_verified_until = timezone.localdate() - dt.timedelta(days=1)
        worker.save(
            update_fields=["police_verified_at", "police_verified_until"]
        )

        assert worker.is_police_verified is False

    def test_the_badge_is_not_an_input_to_the_trust_score(self):
        """The one that stops this becoming pay-to-rank.

        `apps/hiring/scoring.py` is what a resident is told to read when deciding
        who enters their home. Money must not be a term in it. If promoted
        placement is ever built it belongs in a post-sort step, never here.
        """
        import inspect

        from apps.hiring import scoring

        source = inspect.getsource(scoring)
        for forbidden in (
            "police_verified",
            "is_police_verified",
            "subscription",
            "platform_fee",
        ):
            assert forbidden not in source, (
                f"{forbidden!r} appears in the scorer — a paid signal must never "
                "be an input to a trust score residents rely on."
            )

    def test_a_paid_badge_does_not_change_the_match_score(self, worker):
        from apps.hiring.services import score_worker

        before = score_worker(worker).total
        worker.police_verified_at = timezone.localdate()
        worker.police_verified_until = timezone.localdate() + dt.timedelta(days=365)
        worker.save(
            update_fields=["police_verified_at", "police_verified_until"]
        )

        assert score_worker(worker).total == before


class TestTipSettlement:
    def url(self):
        return reverse("v1:payments:tips-owed")

    def test_paid_tips_are_listed_for_hand_settlement(
        self, authenticated_client, admin_user, society, resident, worker
    ):
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=20_000,
            tip_paise=5_000,
        )
        payment.status = PaymentStatus.PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at"])

        response = authenticated_client(admin_user).get(self.url())

        assert response.status_code == 200
        assert response.data["count"] == 1
        row = response.data["results"][0]
        assert row["worker_id"] == worker.pk
        assert row["tip_paise"] == 5_000
        assert payment.receipt_number in row["receipts"]
        assert response.data["settlement"] == "cash"

    def test_an_unpaid_tip_is_not_owed_to_anybody_yet(
        self, authenticated_client, admin_user, society, resident, worker
    ):
        """Listing it would have an administrator hand over money that never arrived."""
        create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=20_000,
            tip_paise=5_000,
        )

        response = authenticated_client(admin_user).get(self.url())

        assert response.data["count"] == 0

    def test_tips_are_grouped_per_worker(
        self, authenticated_client, admin_user, society, resident, worker
    ):
        for _ in range(3):
            payment = create_payment(
                resident=resident,
                worker=worker,
                society=society,
                kind=PaymentKind.BOOKING,
                amount_paise=20_000,
                tip_paise=2_000,
            )
            payment.status = PaymentStatus.PAID
            payment.paid_at = timezone.now()
            payment.save(update_fields=["status", "paid_at"])

        response = authenticated_client(admin_user).get(self.url())

        assert response.data["count"] == 1
        assert response.data["results"][0]["tip_paise"] == 6_000
        assert response.data["results"][0]["payment_count"] == 3

    def test_a_worker_cannot_read_the_settlement_list(
        self, authenticated_client, worker_user
    ):
        response = authenticated_client(worker_user).get(self.url())
        assert response.status_code == 403


class TestFeeQuote:
    def test_the_quote_is_zero_and_silent_while_fees_are_off(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:payments:fee-quote"), {"amount_paise": 50_000}
        )

        assert response.status_code == 200
        assert response.data["platform_fee_paise"] == 0
        assert response.data["total_paise"] == 50_000
        # The screen renders nothing rather than a "₹0.00 fee" line.
        assert response.data["fee_applies"] is False

    @ENABLED
    def test_the_quote_shows_the_fee_before_confirmation(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:payments:fee-quote"), {"amount_paise": 20_000}
        )

        assert response.data["platform_fee_paise"] == 1_600
        assert response.data["total_paise"] == 21_600
        assert response.data["fee_applies"] is True

    def test_a_nonsense_amount_is_refused(self, authenticated_client, resident_user):
        response = authenticated_client(resident_user).get(
            reverse("v1:payments:fee-quote"), {"amount_paise": "lots"}
        )
        assert response.status_code == 400
