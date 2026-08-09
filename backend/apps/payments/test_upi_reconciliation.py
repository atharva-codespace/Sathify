"""
Module 8.9 — the third settlement path, and the fences around it.

This is the only way a payment reaches PAID without a verified signature, so the
tests here are mostly about what it *refuses*. The one that matters most is the
unique UTR: it is the difference between "an administrator confirms what the
bank shows" and "an administrator can mark things paid".
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.payments.models import (
    Payment,
    PaymentKind,
    PaymentStatus,
    SettledVia,
    UpiSettlement,
)
from apps.payments.services import (
    AmountMismatch,
    PaymentError,
    UtrAlreadyUsed,
    confirm_upi_settlement,
)
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import WorkerProfile

pytestmark = pytest.mark.django_db

UTR = "412345678901"


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def worker(worker_user):
    return WorkerProfile.objects.create(
        user=worker_user, photo="workers/x.jpg", trust_score=70
    )


@pytest.fixture
def payment(society, resident, worker):
    return Payment.objects.create(
        society=society, resident=resident, worker=worker,
        kind=PaymentKind.BOOKING, amount_paise=45000, tip_paise=5000,
    )


def url(payment):
    return reverse("v1:payments:settle-upi", args=[payment.pk])


class TestConfirming:
    def test_it_settles_and_records_who_said_so(self, payment, admin_user):
        confirm_upi_settlement(
            payment,
            utr=UTR,
            amount_paise=payment.total_paise,
            confirmed_by=admin_user,
            note="Seen on the 09 Aug statement.",
        )

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID
        assert payment.paid_at is not None

        evidence = payment.upi_settlement
        assert evidence.utr == UTR
        assert evidence.confirmed_by == admin_user
        assert evidence.amount_paise == payment.total_paise

    def test_the_row_says_it_rests_on_a_person_not_a_signature(
        self, payment, admin_user
    ):
        """The whole reason ``settled_via`` is a column.

        An auditor must be able to separate the payments backed by an HMAC from
        the ones backed by somebody having read a bank statement, without
        reading any code.
        """
        confirm_upi_settlement(
            payment, utr=UTR, amount_paise=payment.total_paise, confirmed_by=admin_user
        )

        payment.refresh_from_db()
        assert payment.settled_via == SettledVia.UPI_MANUAL
        assert payment.razorpay_signature == ""
        assert payment.razorpay_payment_id == ""

    def test_a_utr_is_normalised_so_case_cannot_dodge_the_constraint(
        self, payment, admin_user
    ):
        confirm_upi_settlement(
            payment, utr="  abc123def456  ", amount_paise=payment.total_paise,
            confirmed_by=admin_user,
        )
        assert payment.upi_settlement.utr == "ABC123DEF456"

    def test_confirming_twice_is_idempotent(self, payment, admin_user):
        """Two administrators working the same statement is a normal Monday."""
        confirm_upi_settlement(
            payment, utr=UTR, amount_paise=payment.total_paise, confirmed_by=admin_user
        )
        first_paid_at = Payment.objects.get(pk=payment.pk).paid_at

        confirm_upi_settlement(
            payment, utr=UTR, amount_paise=payment.total_paise, confirmed_by=admin_user
        )

        payment.refresh_from_db()
        # paid_at decides which month's salary summary this lands in, so a
        # second confirmation must not move it.
        assert payment.paid_at == first_paid_at
        assert UpiSettlement.objects.filter(payment=payment).count() == 1


class TestWhatItRefuses:
    def test_a_wrong_amount_is_refused(self, payment, admin_user):
        """Asking for the figure they can see, and refusing when it disagrees,
        is what makes this a reconciliation rather than a paid button."""
        with pytest.raises(AmountMismatch):
            confirm_upi_settlement(
                payment, utr=UTR, amount_paise=100, confirmed_by=admin_user
            )

        payment.refresh_from_db()
        assert payment.status != PaymentStatus.PAID
        assert not UpiSettlement.objects.exists()

    def test_one_utr_cannot_settle_two_payments(
        self, payment, society, resident, worker, admin_user
    ):
        """The control that matters.

        Without it an administrator could clear every outstanding charge by
        pasting one reference repeatedly, which is exactly the abuse the old
        "no third path" rule existed to prevent.
        """
        confirm_upi_settlement(
            payment, utr=UTR, amount_paise=payment.total_paise, confirmed_by=admin_user
        )
        second = Payment.objects.create(
            society=society, resident=resident, worker=worker,
            kind=PaymentKind.BOOKING, amount_paise=50000,
        )

        with pytest.raises(UtrAlreadyUsed):
            confirm_upi_settlement(
                second, utr=UTR, amount_paise=second.total_paise,
                confirmed_by=admin_user,
            )

        second.refresh_from_db()
        assert second.status != PaymentStatus.PAID

    def test_the_database_enforces_it_too(self, payment, admin_user, society, resident, worker):
        """Belt and braces: a future code path cannot reintroduce the reuse."""
        confirm_upi_settlement(
            payment, utr=UTR, amount_paise=payment.total_paise, confirmed_by=admin_user
        )
        second = Payment.objects.create(
            society=society, resident=resident, worker=worker,
            kind=PaymentKind.BOOKING, amount_paise=50000,
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            UpiSettlement.objects.create(
                payment=second, utr=UTR, amount_paise=50000, confirmed_by=admin_user
            )

    def test_a_blank_or_token_reference_is_refused(self, payment, admin_user):
        """"paid", "-" and "" are not evidence."""
        for junk in ["", "  ", "-", "ok"]:
            with pytest.raises(PaymentError):
                confirm_upi_settlement(
                    payment, utr=junk, amount_paise=payment.total_paise,
                    confirmed_by=admin_user,
                )

    def test_a_refunded_payment_cannot_be_settled(self, payment, admin_user):
        payment.status = PaymentStatus.REFUNDED
        payment.save(update_fields=["status"])

        with pytest.raises(PaymentError):
            confirm_upi_settlement(
                payment, utr=UTR, amount_paise=payment.total_paise,
                confirmed_by=admin_user,
            )

    def test_a_cancelled_payment_cannot_be_settled(self, payment, admin_user):
        payment.status = PaymentStatus.CANCELLED
        payment.save(update_fields=["status"])

        with pytest.raises(PaymentError):
            confirm_upi_settlement(
                payment, utr=UTR, amount_paise=payment.total_paise,
                confirmed_by=admin_user,
            )


class TestWhoMayDoIt:
    """The half of the original rule that has not moved: the party who benefits
    from a payment being marked paid must never be the party who marks it."""

    def test_an_administrator_may(self, authenticated_client, admin_user, payment):
        response = authenticated_client(admin_user).post(
            url(payment),
            {"utr": UTR, "amount_paise": payment.total_paise},
            format="json",
        )

        assert response.status_code == 200, response.data
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID

    def test_the_resident_who_owes_it_may_not(
        self, authenticated_client, resident_user, payment
    ):
        response = authenticated_client(resident_user).post(
            url(payment),
            {"utr": UTR, "amount_paise": payment.total_paise},
            format="json",
        )

        assert response.status_code == 403
        payment.refresh_from_db()
        assert payment.status != PaymentStatus.PAID

    def test_the_worker_who_is_owed_it_may_not(
        self, authenticated_client, worker_user, payment
    ):
        response = authenticated_client(worker_user).post(
            url(payment),
            {"utr": UTR, "amount_paise": payment.total_paise},
            format="json",
        )

        assert response.status_code == 403

    def test_an_administrator_of_another_society_may_not(
        self, authenticated_client, django_user_model, payment
    ):
        from apps.societies.models import Society, SocietyStatus

        other = Society.objects.create(
            name="Other Society", address_line="X", city="Pune", state="MH",
            pincode="411001", total_towers=1, total_flats=4,
            status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9877777777", password="test-pass-12345",
            role=Role.SOCIETY_ADMIN, society=other, is_approved=True, is_staff=True,
        )

        response = authenticated_client(outsider).post(
            url(payment),
            {"utr": UTR, "amount_paise": payment.total_paise},
            format="json",
        )
        assert response.status_code == 404


class TestTheApi:
    def test_a_mismatched_amount_is_a_409_with_both_figures(
        self, authenticated_client, admin_user, payment
    ):
        response = authenticated_client(admin_user).post(
            url(payment), {"utr": UTR, "amount_paise": 100}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "amount_mismatch"
        assert "50000" in response.data["error"]["message"]

    def test_a_reused_utr_is_a_409(
        self, authenticated_client, admin_user, payment, society, resident, worker
    ):
        client = authenticated_client(admin_user)
        client.post(
            url(payment),
            {"utr": UTR, "amount_paise": payment.total_paise},
            format="json",
        )
        second = Payment.objects.create(
            society=society, resident=resident, worker=worker,
            kind=PaymentKind.BOOKING, amount_paise=50000,
        )

        response = client.post(
            url(second),
            {"utr": UTR, "amount_paise": second.total_paise},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "utr_already_used"

    def test_the_amount_is_required(self, authenticated_client, admin_user, payment):
        """No defaulting to the payment's own total. Making the administrator
        re-key the figure is what proves they looked at the statement."""
        response = authenticated_client(admin_user).post(
            url(payment), {"utr": UTR}, format="json"
        )
        assert response.status_code == 400


class TestItDrivesEverythingSettlementDrives:
    """A reconciled payment must behave exactly like a gateway-settled one.

    This is the reason the service calls ``on_payment_settled`` rather than just
    flipping the status: an emergency whose surcharge was paid by QR has to
    reach the workers, and a worker whose salary arrived has to be told.
    """

    @pytest.fixture
    def emergency(self, society, resident, django_user_model):
        from apps.bookings import emergency as emergency_service

        # Maids to broadcast to.
        for index, name in enumerate(["Sunita", "Lakshmi"]):
            user = django_user_model.objects.create_user(
                phone_number=f"987600000{index}", password="test-pass-12345",
                role=Role.WORKER, society=society, first_name=name,
                last_name="K", is_approved=True,
            )
            WorkerProfile.objects.create(
                user=user, photo="workers/x.jpg", is_available=True,
                trust_score=70, average_rating=4.3,
            )

        moment = timezone.localtime() + dt.timedelta(minutes=20)
        booking, surcharge = emergency_service.raise_emergency(
            resident=resident,
            society=society,
            category=ServiceCategory.objects.get(slug="emergency-assistance"),
            scheduled_date=moment.date(),
            start_time=moment.time().replace(microsecond=0),
        )
        return booking, surcharge

    def test_a_reconciled_surcharge_broadcasts_the_emergency(
        self, emergency, admin_user
    ):
        """Without the hook, a household would pay by QR and the request would
        sit at payment_pending forever, reaching nobody."""
        from apps.bookings.models import BookingOffer

        booking, surcharge = emergency
        assert booking.status == BookingStatus.PAYMENT_PENDING

        confirm_upi_settlement(
            surcharge, utr=UTR, amount_paise=surcharge.total_paise,
            confirmed_by=admin_user,
        )

        booking.refresh_from_db()
        assert booking.status == BookingStatus.BROADCAST
        assert BookingOffer.objects.filter(booking=booking).count() == 2

    def test_a_reconciled_salary_tells_the_worker(
        self, payment, admin_user, worker_user, django_capture_on_commit_callbacks
    ):
        from apps.notifications.models import Notification, NotificationCategory

        payment.kind = PaymentKind.ENGAGEMENT_SALARY
        payment.save(update_fields=["kind"])

        with django_capture_on_commit_callbacks(execute=True):
            confirm_upi_settlement(
                payment, utr=UTR, amount_paise=payment.total_paise,
                confirmed_by=admin_user,
            )

        assert Notification.objects.filter(
            recipient=worker_user, category=NotificationCategory.PAYMENT
        ).exists()

    def test_a_reconciled_surcharge_does_not_tell_a_worker_they_were_paid(
        self, emergency, admin_user
    ):
        """The surcharge is Sathify's fee. Telling a worker she has "been paid"
        an amount she will never receive is worse than telling her nothing."""
        from apps.notifications.models import Notification

        _, surcharge = emergency
        confirm_upi_settlement(
            surcharge, utr=UTR, amount_paise=surcharge.total_paise,
            confirmed_by=admin_user,
        )

        assert not Notification.objects.filter(title="You have been paid").exists()
