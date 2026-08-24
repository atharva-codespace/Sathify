"""
Module 14 — the console API.

Three properties matter more than any feature here, and each has its own class:

* **Nobody but a platform operator gets in.** The console is the one surface
  that reads across societies; a single unguarded route on it leaks the whole
  platform, so the guard is asserted route by route rather than in general.
* **Revenue and GMV never merge.** Wages are not income, and the schema says so
  (``platform_fee_paise`` is zero on every row). If a "total" ever appears that
  adds them, the company starts optimising the number it does not earn.
* **Suspension never stops the gate.** A society behind on its invoice must
  still be able to log its workers in and out, or a billing dispute becomes a
  wage dispute.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import (
    ImpersonationGrant,
    PlatformAccessLog,
    Role,
    SuperadminLevel,
    SuperadminProfile,
    User,
)
from apps.attendance.models import SessionSource, SessionStatus, WorkSession
from apps.console.serializers import mask_phone
from apps.hiring.models import Engagement, EngagementStatus, RateBasis
from apps.payments.models import (
    Payment,
    PaymentKind,
    PaymentStatus,
    SettledVia,
    SocietySubscription,
    SubscriptionTier,
)
from apps.societies.models import Flat, Resident, Society, SocietyStatus, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 8, 13)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def support(db):
    user = User.objects.create_superuser(phone_number="9800000099", password="x-12345")
    SuperadminProfile.objects.create(user=user, level=SuperadminLevel.SUPPORT)
    user.refresh_from_db()
    return user


@pytest.fixture
def finance(db):
    user = User.objects.create_superuser(phone_number="9800000098", password="x-12345")
    SuperadminProfile.objects.create(user=user, level=SuperadminLevel.FINANCE)
    user.refresh_from_db()
    return user


@pytest.fixture
def console(authenticated_client, support):
    return authenticated_client(support)


@pytest.fixture
def other_society(db):
    return Society.objects.create(
        name="Palm Grove", address_line="Kalyani Nagar", city="Pune",
        state="Maharashtra", pincode="411006", total_towers=2, total_flats=90,
        status=SocietyStatus.ACTIVE,
    )


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
        user=worker_user, photo="workers/x.jpg", is_available=True,
        trust_score=70, average_rating=4.4,
    )


def _payment(society, resident, worker, **kw):
    return Payment.objects.create(
        society=society, resident=resident, worker=worker,
        kind=kw.pop("kind", PaymentKind.ENGAGEMENT_SALARY),
        amount_paise=kw.pop("amount_paise", 420_000),
        **kw,
    )


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


class TestOnlyPlatformOperatorsGetIn:
    """Every console route, checked individually. One gap leaks the platform."""

    ROUTES = [
        ("v1:console:overview", {}),
        ("v1:console:billing-integrity", {}),
        ("v1:console:transactions", {}),
        ("v1:console:reconciliation", {}),
        ("v1:console:invoices", {}),
        ("v1:console:activity-sessions", {}),
        ("v1:console:activity-access-log", {}),
        ("v1:console:activity-impersonations", {}),
        ("v1:console:societies", {}),
        ("v1:console:users", {}),
    ]

    @pytest.mark.parametrize("name,kwargs", ROUTES)
    def test_a_society_admin_is_refused(self, authenticated_client, admin_user, name, kwargs):
        response = authenticated_client(admin_user).get(reverse(name, kwargs=kwargs))
        assert response.status_code == 403, name

    @pytest.mark.parametrize("name,kwargs", ROUTES)
    def test_a_resident_is_refused(self, authenticated_client, resident_user, name, kwargs):
        response = authenticated_client(resident_user).get(reverse(name, kwargs=kwargs))
        assert response.status_code == 403, name

    @pytest.mark.parametrize("name,kwargs", ROUTES)
    def test_anonymous_is_refused(self, api_client, name, kwargs):
        assert api_client.get(reverse(name, kwargs=kwargs)).status_code == 401, name

    @pytest.mark.parametrize("name,kwargs", ROUTES)
    def test_an_operator_is_admitted(self, console, name, kwargs):
        assert console.get(reverse(name, kwargs=kwargs)).status_code == 200, name

    def test_a_worker_cannot_suspend_a_society(self, authenticated_client, worker_user, society):
        response = authenticated_client(worker_user).post(
            reverse("v1:console:society-suspend", kwargs={"pk": society.pk}),
            {"reason": "because I want to", "acknowledge_gate_keeps_working": True},
            format="json",
        )
        assert response.status_code == 403
        society.refresh_from_db()
        assert society.status == SocietyStatus.ACTIVE


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class TestRevenueAndGmvNeverMerge:
    def test_they_are_separate_keys_with_no_combined_total(self, console, society, resident, worker):
        _payment(society, resident, worker, status=PaymentStatus.PAID, paid_at=timezone.now())
        SocietySubscription.objects.create(
            society=society, tier=SubscriptionTier.STANDARD,
            valid_until=DAY + dt.timedelta(days=90),
        )

        body = console.get(reverse("v1:console:overview")).json()

        assert "revenue" in body and "gmv" in body
        assert body["revenue"]["mrr_paise"] == 150_000
        assert body["gmv"]["settled_paise"] == 420_000
        # The number that must not exist anywhere in the payload.
        assert "total_paise" not in body
        assert not any(
            k in body for k in ("combined_paise", "grand_total_paise", "total")
        )

    def test_the_platform_earns_nothing_on_wages(self, console, society, resident, worker):
        _payment(society, resident, worker, status=PaymentStatus.PAID, paid_at=timezone.now())
        body = console.get(reverse("v1:console:overview")).json()
        assert body["gmv"]["platform_earned_paise"] == 0

    def test_the_payload_says_so_in_words(self, console):
        body = console.get(reverse("v1:console:overview")).json()
        assert "earns nothing" in body["gmv"]["note"].lower()


class TestNeedsAttention:
    def test_an_unsigned_settlement_is_visible_as_such(self, console, society, resident, worker):
        _payment(
            society, resident, worker, status=PaymentStatus.PAID,
            paid_at=timezone.now(), settled_via=SettledVia.UPI_MANUAL,
        )
        body = console.get(reverse("v1:console:reconciliation")).json()
        assert body["unsigned_settlements"]["count"] == 1
        assert "not on a verified signature" in body["unsigned_settlements"]["note"]

    def test_held_wages_reach_the_queue(self, console, society, resident, worker):
        from apps.payments.models import Invoice

        service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
        engagement = Engagement.objects.create(
            society=society, resident=resident, worker=worker, service_type=service_type,
            days_of_week=[0, 1, 2, 3, 4], start_time=dt.time(9, 0),
            expected_duration_minutes=180, monthly_rate=0, rate_basis=RateBasis.HOURLY,
            hourly_rate=120, visit_fee=60, status=EngagementStatus.ACTIVE,
        )
        Invoice.objects.create(
            society=society, engagement=engagement, resident=resident, worker=worker,
            period_start=DAY.replace(day=1), period_end=DAY, held_paise=24_000,
        )
        body = console.get(reverse("v1:console:overview")).json()
        codes = {item["code"] for item in body["needs_attention"]}
        assert "amounts_held" in codes

    def test_flagged_sessions_reach_the_queue(self, console, society, resident, worker):
        service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
        engagement = Engagement.objects.create(
            society=society, resident=resident, worker=worker, service_type=service_type,
            days_of_week=[0, 1, 2, 3, 4], start_time=dt.time(9, 0),
            expected_duration_minutes=180, monthly_rate=0, rate_basis=RateBasis.HOURLY,
            hourly_rate=120, visit_fee=60, status=EngagementStatus.ACTIVE,
        )
        WorkSession.objects.create(
            society=society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(), source=SessionSource.DERIVED,
            status=SessionStatus.AUTO_CLOSED, needs_review=True,
        )
        body = console.get(reverse("v1:console:overview")).json()
        codes = {item["code"] for item in body["needs_attention"]}
        assert "sessions_need_review" in codes


class TestBillingIntegrity:
    def test_a_society_of_derived_sessions_is_not_advised_for_hourly(
        self, console, society, resident, worker
    ):
        """Below 90% tier-1/2 capture a wage figure rests on inference."""
        service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
        engagement = Engagement.objects.create(
            society=society, resident=resident, worker=worker, service_type=service_type,
            days_of_week=[0, 1, 2, 3, 4], start_time=dt.time(9, 0),
            expected_duration_minutes=180, monthly_rate=0, rate_basis=RateBasis.HOURLY,
            hourly_rate=120, visit_fee=60, status=EngagementStatus.ACTIVE,
        )
        for offset, source in enumerate(
            [SessionSource.SELF, SessionSource.DERIVED, SessionSource.MANUAL]
        ):
            WorkSession.objects.create(
                society=society, engagement=engagement, worker=worker,
                visit_date=timezone.localdate() - dt.timedelta(days=offset),
                source=source, status=SessionStatus.CLOSED,
            )

        body = console.get(reverse("v1:console:billing-integrity")).json()

        assert body["sessions"] == 3
        assert body["trusted_capture_rate"] == pytest.approx(1 / 3, abs=0.001)
        assert body["hourly_billing_advised"] is False

    def test_no_sessions_is_not_a_pass(self, console):
        body = console.get(reverse("v1:console:billing-integrity")).json()
        assert body["sessions"] == 0
        assert body["hourly_billing_advised"] is False


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TestTransactionLedger:
    def test_it_reads_across_societies(self, console, society, other_society, resident, worker):
        _payment(society, resident, worker)
        # A platform charge: `worker` is null because it is owed to Sathify, but
        # `resident` is not — somebody asked for the emergency broadcast.
        Payment.objects.create(
            society=other_society, resident=resident, worker=None,
            kind=PaymentKind.EMERGENCY_SURCHARGE, amount_paise=15_000,
        )
        body = console.get(reverse("v1:console:transactions")).json()
        assert body["count"] == 2
        assert {row["society"] for row in body["results"]} == {society.id, other_society.id}

    def test_the_unsigned_filter_is_the_saved_view_settled_via_exists_for(
        self, console, society, resident, worker
    ):
        _payment(
            society, resident, worker, status=PaymentStatus.PAID,
            paid_at=timezone.now(), settled_via=SettledVia.UPI_MANUAL,
        )
        _payment(
            society, resident, worker, status=PaymentStatus.PAID,
            paid_at=timezone.now(), settled_via=SettledVia.WEBHOOK,
        )
        body = console.get(reverse("v1:console:transactions"), {"unsigned": "true"}).json()
        assert body["count"] == 1
        assert body["results"][0]["rests_on_a_person"] is True

    def test_the_detail_drawer_names_what_the_payment_rests_on(
        self, console, society, resident, worker
    ):
        payment = _payment(
            society, resident, worker, status=PaymentStatus.PAID,
            paid_at=timezone.now(), razorpay_signature="deadbeef",
            razorpay_payment_id="pay_123",
        )
        body = console.get(
            reverse("v1:console:transaction-detail",
                    kwargs={"receipt_number": payment.receipt_number})
        ).json()
        assert body["settlement_evidence"]["kind"] == "signature"
        assert body["worker_receives"]["paise"] == body["amount"]["paise"]

    def test_a_missing_receipt_is_a_404_not_a_500(self, console):
        response = console.get(
            reverse("v1:console:transaction-detail", kwargs={"receipt_number": "NOPE-1"})
        )
        assert response.status_code == 404

    def test_reconciliation_does_not_shadow_a_receipt_lookup(self, console):
        """URL ordering: `transactions/reconciliation/` must not read as a receipt."""
        assert console.get(reverse("v1:console:reconciliation")).status_code == 200


# ---------------------------------------------------------------------------
# Societies
# ---------------------------------------------------------------------------


class TestSuspensionNeverStopsTheGate:
    def test_it_must_be_acknowledged_before_it_will_run(self, console, society):
        response = console.post(
            reverse("v1:console:society-suspend", kwargs={"pk": society.pk}),
            {"reason": "non-payment, 62 days, three contacts unanswered",
             "acknowledge_gate_keeps_working": False},
            format="json",
        )
        assert response.status_code == 400
        society.refresh_from_db()
        assert society.status == SocietyStatus.ACTIVE

    def test_a_reason_is_required(self, console, society):
        response = console.post(
            reverse("v1:console:society-suspend", kwargs={"pk": society.pk}),
            {"reason": "no", "acknowledge_gate_keeps_working": True},
            format="json",
        )
        assert response.status_code == 400

    def test_attendance_still_writes_after_suspension(
        self, console, society, resident, worker
    ):
        """The rule from monetisation.md, asserted rather than trusted."""
        service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
        engagement = Engagement.objects.create(
            society=society, resident=resident, worker=worker, service_type=service_type,
            days_of_week=[0, 1, 2, 3, 4], start_time=dt.time(9, 0),
            expected_duration_minutes=180, monthly_rate=0, rate_basis=RateBasis.HOURLY,
            hourly_rate=120, visit_fee=60, status=EngagementStatus.ACTIVE,
        )

        response = console.post(
            reverse("v1:console:society-suspend", kwargs={"pk": society.pk}),
            {"reason": "non-payment, 62 days, three contacts unanswered",
             "acknowledge_gate_keeps_working": True},
            format="json",
        )
        assert response.status_code == 200
        society.refresh_from_db()
        assert society.status == SocietyStatus.SUSPENDED

        # The thing suspension must never break.
        session = WorkSession.objects.create(
            society=society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(), source=SessionSource.SELF,
            status=SessionStatus.OPEN,
        )
        assert session.pk is not None
        assert "attendance writes" in response.json()["scope"]["still_working"]

    def test_suspension_is_logged_with_its_reason(self, console, society):
        console.post(
            reverse("v1:console:society-suspend", kwargs={"pk": society.pk}),
            {"reason": "non-payment, 62 days, three contacts unanswered",
             "acknowledge_gate_keeps_working": True},
            format="json",
        )
        log = PlatformAccessLog.objects.get(action="society.suspend")
        assert log.society_id == society.id
        assert "62 days" in log.reason

    def test_the_detail_view_states_the_scope_before_you_press_it(self, console, society):
        body = console.get(
            reverse("v1:console:society-detail", kwargs={"pk": society.pk})
        ).json()
        assert "gate checks" in body["suspension_scope"]["keeps_working"]
        assert "wages behind a billing dispute" in body["suspension_scope"]["why"]


class TestTierChange:
    def test_it_creates_the_subscription_row(self, console, society):
        response = console.post(
            reverse("v1:console:society-tier", kwargs={"pk": society.pk}),
            {"tier": SubscriptionTier.STANDARD,
             "valid_until": str(DAY + dt.timedelta(days=365)),
             "reason": "committee agreed on the annual plan"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["tier"] == SubscriptionTier.STANDARD
        assert SocietySubscription.objects.get(society=society).is_active is True

    def test_a_society_with_no_subscription_reads_as_free(self, console, society):
        body = console.get(reverse("v1:console:societies")).json()
        row = next(r for r in body["results"] if r["id"] == society.id)
        assert row["tier"] == SubscriptionTier.FREE
        assert row["worker_cap"] == 25


# ---------------------------------------------------------------------------
# Users and PII
# ---------------------------------------------------------------------------


class TestContactDetailsAreMasked:
    def test_the_search_never_returns_a_dialable_number(
        self, console, resident_user, worker_user
    ):
        body = console.get(reverse("v1:console:users")).json()
        for row in body["results"]:
            assert "x" in row["phone"]
            assert row["phone"] != resident_user.phone_number

    def test_masking_keeps_enough_to_recognise_and_not_enough_to_dial(self):
        assert mask_phone("9876543210") == "98xxxxxx10"
        assert mask_phone("") == ""

    def test_operators_are_not_in_the_directory(self, console, support):
        body = console.get(reverse("v1:console:users")).json()
        assert all(row["role"] != Role.SUPERADMIN for row in body["results"])

    def test_revealing_requires_a_real_reason(self, console, resident_user):
        response = console.post(
            reverse("v1:console:user-reveal", kwargs={"pk": resident_user.pk}),
            {"reason": "why"},
            format="json",
        )
        assert response.status_code == 400
        assert not PlatformAccessLog.objects.filter(action="pii.reveal").exists()

    def test_revealing_is_logged_against_the_persons_own_society(
        self, console, resident_user, society
    ):
        response = console.post(
            reverse("v1:console:user-reveal", kwargs={"pk": resident_user.pk}),
            {"reason": "returning a call about invoice query #221"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["phone_number"] == resident_user.phone_number

        log = PlatformAccessLog.objects.get(action="pii.reveal")
        assert log.society_id == society.id
        assert log.superadmin_id is not None
        # The society can read this row — that is what makes the reason matter.
        assert society.platform_access_logs.filter(action="pii.reveal").exists()

    def test_reading_the_user_directory_is_itself_logged(self, console, resident_user):
        console.get(reverse("v1:console:users"))
        assert PlatformAccessLog.objects.filter(
            model_label="accounts.User", action="read"
        ).exists()


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------


class TestTheAuditSeamCannotBeShadowed:
    """Regression, structural rather than by example.

    ``PlatformScopedQuerysetMixin`` writes the audit row inside ``get_queryset``.
    A view that mixes it in and *also* defines its own ``get_queryset`` sits
    ahead of it in the MRO and shadows it completely — the endpoint keeps
    working, keeps returning every society's rows, and quietly stops logging.
    Nothing fails, so nothing tells you.

    Checking the resolution once here catches it for every console view that
    exists now or is added later, which an example test for one endpoint cannot.
    """

    def _platform_scoped_views(self):
        import inspect

        from apps.console import views as console_views
        from apps.core.platform import PlatformScopedQuerysetMixin

        return [
            obj
            for _name, obj in inspect.getmembers(console_views, inspect.isclass)
            if issubclass(obj, PlatformScopedQuerysetMixin)
            and obj is not PlatformScopedQuerysetMixin
        ]

    def test_at_least_one_view_uses_the_seam(self):
        assert self._platform_scoped_views(), "no console view is platform-scoped"

    def test_no_view_shadows_the_mixins_get_queryset(self):
        from apps.core.platform import PlatformScopedQuerysetMixin

        for view in self._platform_scoped_views():
            assert view.get_queryset is PlatformScopedQuerysetMixin.get_queryset, (
                f"{view.__name__} overrides get_queryset and would silently skip "
                "the audit log. Set a `queryset` class attribute instead."
            )

    def test_a_non_superadmin_reaching_the_seam_gets_nothing(self, rf, resident_user):
        """Fail closed if a route is ever exposed without IsPlatformOperator."""
        from apps.console.views import UserSearchView

        request = rf.get("/api/v1/console/users/")
        request.user = resident_user
        view = UserSearchView()
        view.request = request
        assert view.get_queryset().count() == 0


class TestImpersonation:
    def test_finance_cannot_impersonate(self, authenticated_client, finance, admin_user):
        response = authenticated_client(finance).post(
            reverse("v1:console:impersonation-start"),
            {"target": admin_user.pk, "reason": "checking a disputed invoice line"},
            format="json",
        )
        assert response.status_code == 403

    def test_support_can(self, console, admin_user, society):
        response = console.post(
            reverse("v1:console:impersonation-start"),
            {"target": admin_user.pk, "reason": "checking a disputed invoice line"},
            format="json",
        )
        assert response.status_code == 201
        grant = ImpersonationGrant.objects.get()
        assert grant.society_id == society.id
        assert grant.is_live is True

    def test_a_resident_may_not_be_impersonated(self, console, resident_user):
        """Administrators hold a delegated operational role. Residents do not."""
        response = console.post(
            reverse("v1:console:impersonation-start"),
            {"target": resident_user.pk, "reason": "looking at their bill for them"},
            format="json",
        )
        assert response.status_code == 400
        assert not ImpersonationGrant.objects.exists()

    def test_a_worker_may_not_be_impersonated(self, console, worker_user):
        response = console.post(
            reverse("v1:console:impersonation-start"),
            {"target": worker_user.pk, "reason": "checking her attendance history"},
            format="json",
        )
        assert response.status_code == 400

    def test_a_grant_needs_a_stated_reason(self, console, admin_user):
        response = console.post(
            reverse("v1:console:impersonation-start"),
            {"target": admin_user.pk, "reason": "fixing"},
            format="json",
        )
        assert response.status_code == 400

    def test_ending_a_grant(self, console, admin_user):
        console.post(
            reverse("v1:console:impersonation-start"),
            {"target": admin_user.pk, "reason": "checking a disputed invoice line"},
            format="json",
        )
        grant = ImpersonationGrant.objects.get()
        response = console.post(
            reverse("v1:console:impersonation-end", kwargs={"pk": grant.pk})
        )
        assert response.status_code == 200
        grant.refresh_from_db()
        assert grant.is_live is False

    def test_one_operator_cannot_end_anothers_grant(
        self, console, authenticated_client, admin_user, db
    ):
        console.post(
            reverse("v1:console:impersonation-start"),
            {"target": admin_user.pk, "reason": "checking a disputed invoice line"},
            format="json",
        )
        grant = ImpersonationGrant.objects.get()

        other = User.objects.create_superuser(phone_number="9800000097", password="x-12345")
        SuperadminProfile.objects.create(user=other, level=SuperadminLevel.SUPPORT)
        response = authenticated_client(other).post(
            reverse("v1:console:impersonation-end", kwargs={"pk": grant.pk})
        )
        assert response.status_code == 404
        grant.refresh_from_db()
        assert grant.is_live is True

    def test_the_sensitive_actions_tab_shows_the_reason_inline(self, console, admin_user):
        console.post(
            reverse("v1:console:impersonation-start"),
            {"target": admin_user.pk, "reason": "resident reports two extra sessions"},
            format="json",
        )
        body = console.get(reverse("v1:console:activity-impersonations")).json()
        assert body["count"] == 1
        assert body["results"][0]["reason"] == "resident reports two extra sessions"
