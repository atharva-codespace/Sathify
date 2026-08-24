"""
Module 8.11 — the statutory floor, checked on the rate she actually earns.

The class that matters is :class:`TestTheFloorIsCheckedOnTheEffectiveRate`. A
check against ``hourly_rate`` alone would pass terms that pay below the minimum
in practice, because the advertised rate and the earned rate come apart as soon
as the visit fee stops matching ``R × T`` — worst on the shortest visits, which
is precisely where a breach hides.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command

from apps.hiring.models import Engagement, EngagementStatus, RateBasis
from apps.payments import wage_floor
from apps.payments.hourly import set_hourly_terms, suggest_hourly_terms
from apps.payments.models import WageFloor
from apps.societies.models import Flat, Resident, SocietyBillingConfig, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db

TODAY = dt.date(2026, 8, 14)


@pytest.fixture
def floor(db):
    return WageFloor.objects.create(
        state="Maharashtra",
        min_hourly_paise=11_000,  # ₹110/hour
        effective_from=dt.date(2026, 1, 1),
        source_note="test",
    )


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def engagement(society, resident_user, worker_user, flat):
    resident = Resident.objects.create(user=resident_user, flat=flat, is_primary=True)
    worker = WorkerProfile.objects.create(
        user=worker_user, photo="workers/x.jpg", is_available=True,
        trust_score=70, average_rating=4.4,
    )
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    return Engagement.objects.create(
        society=society, resident=resident, worker=worker, service_type=service_type,
        days_of_week=[0, 1, 2, 3, 4], start_time=dt.time(9, 0),
        expected_duration_minutes=60,  # a one-hour visit: the exposed case
        monthly_rate=8000, status=EngagementStatus.ACTIVE,
    )


class TestTheFloorIsCheckedOnTheEffectiveRate:
    def test_a_bare_hourly_rate_above_the_floor_can_still_breach_it(self, floor):
        """The whole reason this module measures what it measures.

        ₹120/hour advertised, no visit fee, one-hour visit, 30 minutes of
        travel: she earns ₹120 for 90 minutes of committed time — ₹80/hour,
        comfortably under a ₹110 floor, while the stored rate looks fine.
        """
        finding = wage_floor.check(
            state="Maharashtra", hourly_rate=120, visit_fee=0,
            scheduled_minutes=60, overhead_minutes=30, on=TODAY,
        )
        assert finding.hourly_paise == 12_000  # above the floor on paper
        assert finding.effective_paise == 8_000  # and below it in her hand
        assert finding.is_compliant is False

    def test_the_calibrated_fee_brings_the_same_rate_into_compliance(self, floor):
        """`F = R × T` — ₹60 on a ₹120 rate with 30 minutes of overhead."""
        finding = wage_floor.check(
            state="Maharashtra", hourly_rate=120, visit_fee=60,
            scheduled_minutes=60, overhead_minutes=30, on=TODAY,
        )
        assert finding.effective_paise == 12_000
        assert finding.is_compliant is True
        assert finding.is_calibrated is True

    @pytest.mark.parametrize("minutes", [60, 120, 180, 240, 480])
    def test_a_calibrated_fee_is_compliant_at_every_job_length(self, floor, minutes):
        """One comparison answers compliance for the whole state — but only
        because the calibration holds it flat."""
        finding = wage_floor.check(
            state="Maharashtra", hourly_rate=120, visit_fee=60,
            scheduled_minutes=minutes, overhead_minutes=30, on=TODAY,
        )
        assert finding.effective_paise == 12_000, minutes

    def test_an_undercalibrated_fee_is_flagged_even_when_compliant(self, floor):
        """Legal today, and the shape of a breach tomorrow on a shorter visit."""
        finding = wage_floor.check(
            state="Maharashtra", hourly_rate=200, visit_fee=10,
            scheduled_minutes=180, overhead_minutes=30, on=TODAY,
        )
        assert finding.is_compliant is True
        assert finding.is_calibrated is False

    def test_the_refusal_explains_itself(self, floor):
        finding = wage_floor.check(
            state="Maharashtra", hourly_rate=120, visit_fee=0,
            scheduled_minutes=60, overhead_minutes=30, on=TODAY,
        )
        assert "₹80.00" in finding.message
        assert "₹110.00" in finding.message
        assert "Raise the hourly rate or the visit fee" in finding.message


class TestAMissingFigureIsNotPermission:
    def test_an_unrecorded_state_is_neither_compliant_nor_a_breach(self):
        finding = wage_floor.check(
            state="Goa", hourly_rate=10, visit_fee=0,
            scheduled_minutes=60, overhead_minutes=30, on=TODAY,
        )
        assert finding.is_known is False
        assert finding.is_compliant is False
        assert "No minimum wage is recorded" in finding.message

    def test_by_default_an_unknown_floor_does_not_block(self):
        """Refusing every engagement in an unentered state would take the
        platform offline there to protect workers from a number we lack."""
        finding = wage_floor.assert_compliant(
            state="Goa", hourly_rate=10, visit_fee=0,
            scheduled_minutes=60, overhead_minutes=30, on=TODAY,
        )
        assert finding.is_known is False

    def test_but_it_can_be_made_to_block(self):
        with pytest.raises(wage_floor.WageFloorViolation):
            wage_floor.assert_compliant(
                allow_unknown=False, state="Goa", hourly_rate=10, visit_fee=0,
                scheduled_minutes=60, overhead_minutes=30, on=TODAY,
            )

    def test_a_known_breach_always_raises(self, floor):
        with pytest.raises(wage_floor.WageFloorViolation) as caught:
            wage_floor.assert_compliant(
                state="Maharashtra", hourly_rate=120, visit_fee=0,
                scheduled_minutes=60, overhead_minutes=30, on=TODAY,
            )
        assert caught.value.finding.is_compliant is False


class TestTheEnforcementSeam:
    def test_switching_to_hourly_below_the_floor_writes_nothing(
        self, floor, engagement, society
    ):
        society.state = "Maharashtra"
        society.save(update_fields=["state"])

        with pytest.raises(wage_floor.WageFloorViolation):
            set_hourly_terms(engagement, hourly_rate=120, visit_fee=0)

        engagement.refresh_from_db()
        assert engagement.rate_basis == RateBasis.MONTHLY
        assert engagement.hourly_rate == 0

    def test_compliant_terms_are_written(self, floor, engagement, society):
        society.state = "Maharashtra"
        society.save(update_fields=["state"])

        set_hourly_terms(engagement, hourly_rate=120, visit_fee=60)

        engagement.refresh_from_db()
        assert engagement.rate_basis == RateBasis.HOURLY
        assert engagement.hourly_rate == 120
        assert engagement.visit_fee == 60

    def test_the_suggestion_derives_the_fee_rather_than_guessing(self, society):
        SocietyBillingConfig.objects.update_or_create(
            society=society, defaults={"visit_overhead_minutes": 30}
        )
        assert suggest_hourly_terms(society, hourly_rate=120) == {
            "hourly_rate": 120, "visit_fee": 60, "overhead_minutes": 30,
        }

    def test_the_suggested_fee_rounds_up_to_the_rupee(self, society):
        """Rounding down would shortchange her by paise on every single visit."""
        SocietyBillingConfig.objects.update_or_create(
            society=society, defaults={"visit_overhead_minutes": 20}
        )
        # ₹110/hr × 20 min = ₹36.67 → ₹37, not ₹36.
        assert suggest_hourly_terms(society, hourly_rate=110)["visit_fee"] == 37

    def test_a_suggested_pair_always_clears_its_own_floor(self, floor, society):
        society.state = "Maharashtra"
        society.save(update_fields=["state"])
        SocietyBillingConfig.for_society(society)

        terms = suggest_hourly_terms(society, hourly_rate=110)
        finding = wage_floor.check(
            state="Maharashtra", hourly_rate=terms["hourly_rate"],
            visit_fee=terms["visit_fee"], scheduled_minutes=60,
            overhead_minutes=terms["overhead_minutes"], on=TODAY,
        )
        assert finding.is_compliant is True


class TestSeeding:
    def test_the_command_loads_figures(self):
        call_command("seed_wage_floors")
        assert WageFloor.objects.count() >= 5
        assert WageFloor.in_force("Maharashtra", on=TODAY) is not None

    def test_a_dry_run_writes_nothing(self):
        call_command("seed_wage_floors", "--dry-run")
        assert WageFloor.objects.count() == 0

    def test_running_twice_does_not_duplicate(self):
        call_command("seed_wage_floors")
        first = WageFloor.objects.count()
        call_command("seed_wage_floors")
        assert WageFloor.objects.count() == first

    def test_a_revision_is_a_new_row_not_an_edit(self, floor):
        """An invoice checked against last year's floor must stay reproducible."""
        call_command(
            "seed_wage_floors", "--state", "Maharashtra", "--paise", "12000",
            "--from", "2027-04-01", "--source", "Revision",
        )
        assert WageFloor.objects.filter(state="Maharashtra").count() == 2
        floor.refresh_from_db()
        assert floor.min_hourly_paise == 11_000  # untouched
        assert WageFloor.in_force("Maharashtra", on=TODAY).min_hourly_paise == 11_000
