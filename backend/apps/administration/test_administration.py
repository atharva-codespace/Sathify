"""
Module 11 — Admin, Reporting & Complaints: tests.

Two groups carry most of the weight.

``TestSlaClock`` pins the pure SLA arithmetic, and specifically that the clock
stops overnight. That rule is the difference between an escalation an
administrator could have prevented and one that fires at 3am for reasons nobody
could have acted on — which is how escalation systems get ignored.

``TestComplaintVisibility`` pins who can read what. A worker being complained
about must be able to read the accusation, and must not be able to read the
administrator's internal notes about it. Getting either half wrong is the kind
of bug that only surfaces as a person losing work over something they were never
shown.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.administration import analytics, reports, services, sla
from apps.administration.models import (
    Complaint,
    ComplaintCategory,
    ComplaintPriority,
    ComplaintStatus,
    ComplaintUpdate,
    DemandKind,
    UnmetDemand,
)
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def maid_service(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def worker(worker_user, maid_service):
    profile = WorkerProfile.objects.create(
        user=worker_user, photo="workers/photos/test.jpg"
    )
    profile.service_types.add(maid_service)
    return profile


@pytest.fixture
def complaint(society, resident_user, worker):
    return services.raise_complaint(
        raised_by=resident_user,
        society=society,
        category=ComplaintCategory.LATE_ARRIVAL,
        subject="Did not arrive on Tuesday",
        description="No message, no replacement.",
        against_worker=worker,
    )


def _aware(year, month, day, hour, minute=0):
    """A local-time datetime, which is what the SLA clock reasons about."""
    return timezone.make_aware(dt.datetime(year, month, day, hour, minute))


# ---------------------------------------------------------------------------
# 11.3 The SLA clock
# ---------------------------------------------------------------------------


class TestSlaClock:
    def test_within_a_single_day_it_is_plain_addition(self):
        raised = _aware(2026, 3, 10, 9, 0)
        assert sla.due_at(raised, 4) == _aware(2026, 3, 10, 13, 0)

    def test_the_clock_does_not_run_overnight(self):
        """The rule this module exists to get right.

        A complaint raised at 23:40 with a four-hour window is due at noon the
        next day, not at 03:40 — a deadline nobody could have met would produce
        an escalation nobody could have prevented.
        """
        raised = _aware(2026, 3, 10, 23, 40)
        due = sla.due_at(raised, 4)

        assert due == _aware(2026, 3, 11, 12, 0)

    def test_a_complaint_raised_before_the_window_waits_for_it(self):
        raised = _aware(2026, 3, 10, 5, 0)
        assert sla.due_at(raised, 2) == _aware(2026, 3, 10, 10, 0)

    def test_a_window_longer_than_a_day_spills_onto_the_next(self):
        # 13 active hours a day, so 24 hours of SLA from 08:00 lands at 19:00
        # the following day.
        raised = _aware(2026, 3, 10, 8, 0)
        assert sla.due_at(raised, 24) == _aware(2026, 3, 11, 19, 0)

    def test_weekends_are_not_excluded(self):
        """Deliberately unlike an office SLA.

        A worker refused entry on a Saturday cannot wait until Monday, and
        somebody in the society is always around.
        """
        saturday = _aware(2026, 3, 14, 9, 0)
        assert saturday.weekday() == 5
        assert sla.due_at(saturday, 4) == _aware(2026, 3, 14, 13, 0)

    def test_active_hours_between_skips_the_night(self):
        start = _aware(2026, 3, 10, 20, 0)  # one hour before the window closes
        end = _aware(2026, 3, 11, 9, 0)  # one hour after it reopens

        assert sla.active_hours_between(start, end) == pytest.approx(2.0)

    def test_hours_remaining_goes_negative_once_past(self):
        """Both moments pinned inside the active window, deliberately.

        Deriving them from ``timezone.now()`` made this pass by day and fail
        after 21:00: once the clock has stopped, an hour of wall time is zero
        active hours, so the answer is a legitimate -0.0. That is the behaviour
        the module is built around, not a bug — but a test that only holds
        during office hours is worthless, so this states the window it means.
        """
        due = _aware(2026, 3, 10, 11, 0)
        now = _aware(2026, 3, 10, 13, 0)

        assert sla.hours_remaining(due, now=now) == pytest.approx(-2.0)

    def test_overrun_outside_the_active_window_is_zero_not_negative(self):
        """The case that broke the naive version of the test above.

        A deadline breached at 21:35 and read at 22:35 has burned no active
        hours. Clients must therefore not infer "overdue" from the sign of this
        number — ``Complaint.is_overdue`` is wall-clock and is the authority.
        """
        due = _aware(2026, 3, 10, 21, 35)
        now = _aware(2026, 3, 10, 22, 35)

        assert sla.hours_remaining(due, now=now) == 0
        assert sla.is_breached(due, now=now) is True

    def test_an_unknown_priority_gets_the_longest_window(self):
        """A typo must not manufacture an urgent deadline."""
        assert sla.hours_for("nonsense") == sla.SLA_HOURS["normal"]


# ---------------------------------------------------------------------------
# 11.3 Raising
# ---------------------------------------------------------------------------


class TestRaisingComplaints:
    def test_a_resident_can_raise_one_against_a_worker(
        self, authenticated_client, resident_user, worker
    ):
        response = authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-list"),
            {
                "category": ComplaintCategory.QUALITY,
                "subject": "Kitchen left unclean",
                "description": "The floor was not mopped on three visits.",
                "against_worker": worker.pk,
            },
            format="json",
        )

        assert response.status_code == 201
        body = response.json()["complaint"]
        assert body["reference"].startswith("CMP-")
        assert body["status"] == ComplaintStatus.OPEN
        assert body["sla_due_at"] is not None

    def test_a_worker_can_raise_one_too(
        self, authenticated_client, worker_user, resident
    ):
        """Both sides, deliberately.

        A worker with no way to report a household that withholds pay has only
        the option of leaving, which is the imbalance this platform is meant to
        reduce.
        """
        response = authenticated_client(worker_user).post(
            reverse("v1:administration:complaint-list"),
            {
                "category": ComplaintCategory.PAYMENT,
                "subject": "Salary not paid for February",
                "description": "Two reminders sent, no response.",
                "against_resident": resident.pk,
            },
            format="json",
        )

        assert response.status_code == 201
        assert Complaint.objects.filter(raised_by=worker_user).exists()

    def test_a_complaint_can_be_about_the_society_itself(
        self, authenticated_client, resident_user
    ):
        """No target is legitimate — a broken gate has nobody to name."""
        response = authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-list"),
            {
                "category": ComplaintCategory.SAFETY,
                "subject": "Service gate left unlocked overnight",
                "description": "Found it open at 6am twice this week.",
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["complaint"]["about"] == "The society"

    def test_safety_complaints_enter_the_queue_urgent(self, society, resident_user):
        """The one category that jumps the queue automatically."""
        complaint = services.raise_complaint(
            raised_by=resident_user,
            society=society,
            category=ComplaintCategory.SAFETY,
            subject="Stranger let in without a scan",
            description="The guard waved someone through.",
        )

        assert complaint.priority == ComplaintPriority.URGENT

    def test_priority_cannot_be_set_by_the_client(
        self, authenticated_client, resident_user
    ):
        """A field labelled "how urgent is this?" makes everything urgent."""
        response = authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-list"),
            {
                "category": ComplaintCategory.OTHER,
                "subject": "Minor thing",
                "description": "Barely worth mentioning.",
                "priority": ComplaintPriority.URGENT,
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.json()["complaint"]["priority"] == ComplaintPriority.NORMAL

    def test_a_complaint_cannot_target_another_society(
        self, authenticated_client, resident_user, django_user_model
    ):
        """An id from a client is a claim, not a fact."""
        from apps.societies.models import Society, SocietyStatus

        other_society = Society.objects.create(
            name="Blue Ridge",
            address_line="Elsewhere",
            city="Mumbai",
            state="Maharashtra",
            pincode="400001",
            total_towers=1,
            total_flats=10,
            status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9899999999",
            password="test-pass-12345",
            role=Role.WORKER,
            society=other_society,
            is_approved=True,
        )
        other_worker = WorkerProfile.objects.create(user=outsider)

        response = authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-list"),
            {
                "category": ComplaintCategory.QUALITY,
                "subject": "Nope",
                "description": "Should not be possible.",
                "against_worker": other_worker.pk,
            },
            format="json",
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_raising_notifies_the_administrators(
        self, society, resident_user, admin_user, worker
    ):
        from apps.notifications.models import Notification, NotificationCategory

        services.raise_complaint(
            raised_by=resident_user,
            society=society,
            category=ComplaintCategory.BEHAVIOUR,
            subject="Rude to my mother",
            description="Repeatedly.",
            against_worker=worker,
        )

        assert Notification.objects.filter(
            recipient=admin_user, category=NotificationCategory.COMPLAINT
        ).exists()

    def test_raising_writes_the_first_history_entry(self, complaint):
        entry = complaint.updates.first()

        assert entry is not None
        assert entry.new_status == ComplaintStatus.OPEN
        assert entry.is_system is False


# ---------------------------------------------------------------------------
# 11.3 Working the queue
# ---------------------------------------------------------------------------


class TestWorkingTheQueue:
    def test_an_administrator_picks_one_up(
        self, authenticated_client, admin_user, complaint
    ):
        response = authenticated_client(admin_user).post(
            reverse("v1:administration:complaint-start", args=[complaint.pk])
        )

        assert response.status_code == 200
        assert response.json()["status"] == ComplaintStatus.IN_PROGRESS

        complaint.refresh_from_db()
        assert complaint.first_response_at is not None
        assert complaint.assigned_to_id == admin_user.pk

    def test_picking_up_twice_is_refused_rather_than_silently_repeated(
        self, authenticated_client, admin_user, complaint
    ):
        client = authenticated_client(admin_user)
        url = reverse("v1:administration:complaint-start", args=[complaint.pk])

        assert client.post(url).status_code == 200
        assert client.post(url).status_code == 409

    def test_resolving_records_the_reason_and_notifies(
        self, authenticated_client, admin_user, complaint, resident_user
    ):
        from apps.notifications.models import Notification

        before = Notification.objects.filter(recipient=resident_user).count()

        response = authenticated_client(admin_user).post(
            reverse("v1:administration:complaint-close", args=[complaint.pk]),
            {"status": ComplaintStatus.RESOLVED, "resolution": "Spoke to the worker."},
            format="json",
        )

        assert response.status_code == 200
        complaint.refresh_from_db()
        assert complaint.status == ComplaintStatus.RESOLVED
        assert complaint.resolution == "Spoke to the worker."
        assert complaint.resolved_by_id == admin_user.pk
        assert Notification.objects.filter(recipient=resident_user).count() > before

    def test_rejecting_still_requires_a_reason(
        self, authenticated_client, admin_user, complaint
    ):
        """The outcome most likely to be disputed is the one that needs it most."""
        response = authenticated_client(admin_user).post(
            reverse("v1:administration:complaint-close", args=[complaint.pk]),
            {"status": ComplaintStatus.REJECTED},
            format="json",
        )

        assert response.status_code == 400

    def test_an_administrator_cannot_close_as_withdrawn(
        self, authenticated_client, admin_user, complaint
    ):
        """Withdrawal is the complainant's word, not the administrator's."""
        response = authenticated_client(admin_user).post(
            reverse("v1:administration:complaint-close", args=[complaint.pk]),
            {"status": ComplaintStatus.WITHDRAWN, "resolution": "Never mind."},
            format="json",
        )

        assert response.status_code == 400

    def test_closing_twice_conflicts(
        self, authenticated_client, admin_user, complaint
    ):
        client = authenticated_client(admin_user)
        url = reverse("v1:administration:complaint-close", args=[complaint.pk])
        payload = {"status": ComplaintStatus.RESOLVED, "resolution": "Done."}

        assert client.post(url, payload, format="json").status_code == 200
        assert client.post(url, payload, format="json").status_code == 409

    def test_the_raiser_can_withdraw(
        self, authenticated_client, resident_user, complaint
    ):
        response = authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-withdraw", args=[complaint.pk]),
            {"reason": "Sorted it directly."},
            format="json",
        )

        assert response.status_code == 200
        complaint.refresh_from_db()
        assert complaint.status == ComplaintStatus.WITHDRAWN
        # Withdrawn, not deleted: a complaint withdrawn under pressure looks
        # exactly like one withdrawn freely, and only the record shows it later.
        assert Complaint.objects.filter(pk=complaint.pk).exists()
        assert complaint.updates.filter(new_status=ComplaintStatus.WITHDRAWN).exists()

    def test_somebody_else_cannot_withdraw_your_complaint(
        self, authenticated_client, worker_user, complaint
    ):
        response = authenticated_client(worker_user).post(
            reverse("v1:administration:complaint-withdraw", args=[complaint.pk])
        )

        assert response.status_code == 403

    def test_a_note_from_the_administrator_counts_as_a_first_response(
        self, admin_user, complaint
    ):
        services.add_update(complaint, author=admin_user, note="Looking into it.")

        complaint.refresh_from_db()
        assert complaint.first_response_at is not None

    def test_a_note_from_the_raiser_does_not(self, resident_user, complaint):
        services.add_update(complaint, author=resident_user, note="Any news?")

        complaint.refresh_from_db()
        assert complaint.first_response_at is None


# ---------------------------------------------------------------------------
# 11.3 Visibility
# ---------------------------------------------------------------------------


class TestComplaintVisibility:
    def test_the_accused_worker_can_read_the_complaint(
        self, authenticated_client, worker_user, complaint
    ):
        """Being able to read the accusation is what makes it answerable."""
        response = authenticated_client(worker_user).get(
            reverse("v1:administration:complaint-detail", args=[complaint.pk])
        )

        assert response.status_code == 200
        assert response.json()["reference"] == complaint.reference

    def test_an_unrelated_user_cannot(
        self, authenticated_client, guard_user, complaint
    ):
        response = authenticated_client(guard_user).get(
            reverse("v1:administration:complaint-detail", args=[complaint.pk])
        )

        assert response.status_code == 404

    def test_internal_notes_are_hidden_from_the_accused(
        self, authenticated_client, admin_user, worker_user, complaint
    ):
        services.add_update(
            complaint, author=admin_user, note="Third one this month.", is_internal=True
        )
        services.add_update(complaint, author=admin_user, note="We have spoken to them.")

        worker_view = authenticated_client(worker_user).get(
            reverse("v1:administration:complaint-detail", args=[complaint.pk])
        ).json()
        admin_view = authenticated_client(admin_user).get(
            reverse("v1:administration:complaint-detail", args=[complaint.pk])
        ).json()

        worker_notes = [entry["note"] for entry in worker_view["updates"]]
        admin_notes = [entry["note"] for entry in admin_view["updates"]]

        assert "Third one this month." not in worker_notes
        assert "We have spoken to them." in worker_notes
        assert "Third one this month." in admin_notes

    def test_a_resident_cannot_write_an_internal_note(
        self, authenticated_client, resident_user, complaint
    ):
        """It would hide the comment from the only person who needs to read it."""
        authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-update", args=[complaint.pk]),
            {"note": "Still waiting.", "is_internal": True},
            format="json",
        )

        entry = ComplaintUpdate.objects.filter(note="Still waiting.").first()
        assert entry is not None
        assert entry.is_internal is False

    def test_an_administrator_sees_the_whole_society_queue(
        self, authenticated_client, admin_user, complaint
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:complaint-list")
        )

        assert response.status_code == 200
        assert response.json()["count"] == 1


# ---------------------------------------------------------------------------
# 11.3 Escalation
# ---------------------------------------------------------------------------


class TestEscalation:
    def _make_overdue(self, complaint):
        Complaint.objects.filter(pk=complaint.pk).update(
            sla_due_at=timezone.now() - dt.timedelta(hours=3)
        )
        complaint.refresh_from_db()
        return complaint

    def test_an_overdue_complaint_escalates(self, complaint, admin_user):
        self._make_overdue(complaint)

        assert services.escalate_overdue(society_id=complaint.society_id) == 1

        complaint.refresh_from_db()
        assert complaint.escalated_at is not None
        assert complaint.priority == ComplaintPriority.HIGH

    def test_escalation_does_not_move_the_deadline(self, complaint, admin_user):
        """A deadline that recedes when something gets more urgent is not one."""
        self._make_overdue(complaint)
        original_due = complaint.sla_due_at

        services.escalate_overdue(society_id=complaint.society_id)

        complaint.refresh_from_db()
        assert complaint.sla_due_at == original_due

    def test_escalation_is_idempotent(self, complaint, admin_user):
        self._make_overdue(complaint)

        assert services.escalate_overdue(society_id=complaint.society_id) == 1
        assert services.escalate_overdue(society_id=complaint.society_id) == 0

    def test_a_closed_complaint_never_escalates(self, complaint, admin_user):
        complaint.close(status=ComplaintStatus.RESOLVED, resolution="Done", by=admin_user)
        self._make_overdue(complaint)

        assert services.escalate_overdue(society_id=complaint.society_id) == 0

    def test_escalation_writes_a_system_entry(self, complaint, admin_user):
        self._make_overdue(complaint)
        services.escalate_overdue(society_id=complaint.society_id)

        assert complaint.updates.filter(is_system=True).exists()

    def test_escalation_notifies_the_administrators(
        self, complaint, admin_user
    ):
        from apps.notifications.models import Notification

        before = Notification.objects.filter(recipient=admin_user).count()
        self._make_overdue(complaint)
        services.escalate_overdue(society_id=complaint.society_id)

        assert Notification.objects.filter(recipient=admin_user).count() > before

    def test_loading_the_queue_runs_the_sweep(
        self, authenticated_client, admin_user, complaint
    ):
        """The free tier's substitute for a scheduled job."""
        self._make_overdue(complaint)

        authenticated_client(admin_user).get(
            reverse("v1:administration:complaint-list")
        )

        complaint.refresh_from_db()
        assert complaint.escalated_at is not None

    def test_the_escalate_endpoint_reports_what_it_did(
        self, authenticated_client, admin_user, complaint
    ):
        self._make_overdue(complaint)

        response = authenticated_client(admin_user).post(
            reverse("v1:administration:complaint-escalate")
        )

        assert response.status_code == 200
        assert response.json()["escalated"] == 1

    def test_a_resident_cannot_trigger_the_sweep(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-escalate")
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 11.1 Directory
# ---------------------------------------------------------------------------


class TestDirectory:
    def test_the_worker_directory_lists_the_society(
        self, authenticated_client, admin_user, worker
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:directory-workers")
        )

        assert response.status_code == 200
        row = response.json()["results"][0]
        assert row["full_name"] == "Rahul Sharma"
        assert row["services"] == ["Maid"]

    def test_the_directory_carries_the_open_complaint_count(
        self, authenticated_client, admin_user, worker, complaint
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:directory-workers")
        )

        assert response.json()["results"][0]["open_complaints"] == 1

    def test_a_closed_complaint_stops_counting(
        self, authenticated_client, admin_user, worker, complaint
    ):
        complaint.close(
            status=ComplaintStatus.RESOLVED, resolution="Handled.", by=admin_user
        )

        response = authenticated_client(admin_user).get(
            reverse("v1:administration:directory-workers")
        )

        assert response.json()["results"][0]["open_complaints"] == 0

    def test_search_narrows_by_name(self, authenticated_client, admin_user, worker):
        client = authenticated_client(admin_user)
        url = reverse("v1:administration:directory-workers")

        assert client.get(url, {"search": "Rahul"}).json()["count"] == 1
        assert client.get(url, {"search": "Nobody"}).json()["count"] == 0

    def test_the_resident_directory_lists_flats(
        self, authenticated_client, admin_user, resident
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:directory-residents")
        )

        assert response.status_code == 200
        assert response.json()["results"][0]["flat"] == str(resident.flat)

    def test_a_resident_cannot_read_the_directory(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:administration:directory-workers")
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 11.2 Reports
# ---------------------------------------------------------------------------


class TestReports:
    def test_a_complaint_report_counts_what_it_lists(self, society, complaint):
        today = timezone.localdate()
        report = reports.complaint_report(
            society, start=today - dt.timedelta(days=1), end=today
        )

        assert report.row_count == 1
        assert ("Complaints raised", "1") in report.summary

    def test_csv_carries_the_period_it_covers(self, society, complaint):
        """A compliance export that does not say what it covers is worthless."""
        today = timezone.localdate()
        report = reports.complaint_report(society, start=today, end=today)
        text = reports.render_csv(report)

        assert "Sathify — Complaint report" in text
        assert report.period_label in text
        assert complaint.reference in text

    def test_an_empty_report_says_so_rather_than_rendering_a_bare_header(
        self, society
    ):
        today = timezone.localdate()
        report = reports.payment_report(
            society, start=today - dt.timedelta(days=400), end=today - dt.timedelta(days=390)
        )

        assert report.row_count == 0
        assert "No records in this period." in reports.render_csv(report)

    def test_the_json_report_matches_the_file(
        self, authenticated_client, admin_user, complaint
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:report", args=["complaints"])
        )

        assert response.status_code == 200
        body = response.json()
        assert body["row_count"] == 1
        assert body["columns"][1] == "Reference"

    def test_an_unknown_report_is_a_404_naming_the_real_ones(
        self, authenticated_client, admin_user
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:report", args=["nonsense"])
        )

        assert response.status_code == 404
        assert "attendance" in response.json()["error"]["message"]

    def test_a_backwards_period_is_refused(self, authenticated_client, admin_user):
        """Unlike the dashboard: a report is a compliance document."""
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:report", args=["payments"]),
            {"start": "2026-03-10", "end": "2026-03-01"},
        )

        assert response.status_code == 400

    def test_csv_download_is_an_attachment(
        self, authenticated_client, admin_user, complaint
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:report-csv", args=["complaints"])
        )

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment;" in response["Content-Disposition"]

    def test_pdf_download_renders(self, authenticated_client, admin_user, complaint):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:report-pdf", args=["complaints"])
        )

        # 503 is the documented degradation when reportlab is absent, and is a
        # pass here: the point is that it never 500s.
        assert response.status_code in {200, 503}
        if response.status_code == 200:
            assert response["Content-Type"] == "application/pdf"
            assert bytes(response.content[:4]) == b"%PDF"

    def test_a_resident_cannot_export(self, authenticated_client, resident_user):
        response = authenticated_client(resident_user).get(
            reverse("v1:administration:report-csv", args=["payments"])
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 11.4 Analytics
# ---------------------------------------------------------------------------


class TestAnalytics:
    def test_a_new_society_says_it_has_no_data(self, society):
        """Rather than a chart of zeros somebody will read a shape into."""
        panels = analytics.dashboard(society.pk)

        assert panels["sentiment"]["has_data"] is False
        assert panels["trust"]["has_data"] is False
        assert panels["unmet_demand"]["has_data"] is False

    def test_unrated_workers_are_kept_out_of_the_lowest_band(self, society, worker):
        """Their score is zero because nothing happened, not because they did badly."""
        panels = analytics.trust_distribution(society.pk)

        assert panels["workers"]["total"] == 1
        assert panels["workers"]["rated"] == 0
        assert panels["workers"]["unrated"] == 1
        assert sum(bucket["count"] for bucket in panels["workers"]["buckets"]) == 0

    def test_a_rated_worker_lands_in_the_right_band(self, society, worker):
        WorkerProfile.objects.filter(pk=worker.pk).update(
            trust_score=72, rating_count=4, average_rating=4
        )

        panels = analytics.trust_distribution(society.pk)
        counted = {b["label"]: b["count"] for b in panels["workers"]["buckets"]}

        assert counted["60–80"] == 1
        assert panels["workers"]["average"] == 72.0

    def test_the_complaint_panel_counts_open_and_overdue(
        self, society, complaint
    ):
        Complaint.objects.filter(pk=complaint.pk).update(
            sla_due_at=timezone.now() - dt.timedelta(hours=1)
        )
        today = timezone.localdate()

        panel = analytics.complaint_summary(society.pk, since=today, until=today)

        assert panel["raised"] == 1
        assert panel["open_now"] == 1
        assert panel["overdue_now"] == 1

    def test_the_dashboard_needs_an_administrator(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:administration:dashboard")
        )

        assert response.status_code == 403

    def test_the_dashboard_renders_every_panel(
        self, authenticated_client, admin_user, complaint
    ):
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:dashboard")
        )

        assert response.status_code == 200
        body = response.json()
        for panel in ("sentiment", "trust", "complaints", "unmet_demand", "availability"):
            assert panel in body

    def test_a_malformed_date_falls_back_rather_than_failing(
        self, authenticated_client, admin_user
    ):
        """A read-only overview should not refuse to render over a bad parameter."""
        response = authenticated_client(admin_user).get(
            reverse("v1:administration:dashboard"), {"since": "not-a-date"}
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 11.4 Unmet demand
# ---------------------------------------------------------------------------


class TestUnmetDemand:
    def test_recording_never_raises_on_a_missing_society(self):
        assert services.record_unmet_demand(society=None, kind=DemandKind.NO_MATCH) is None

    def test_a_booking_search_with_no_matches_is_logged(
        self, authenticated_client, resident_user, society
    ):
        from apps.bookings.models import ServiceCategory

        category = ServiceCategory.objects.create(
            name="Deep cleaning",
            slug="deep-cleaning-test",
            expected_duration_minutes=120,
            price_min=600,
            price_max=900,
        )
        tomorrow = timezone.localdate() + dt.timedelta(days=1)

        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:match"),
            {
                "category": category.pk,
                "date": tomorrow.isoformat(),
                "start_time": "10:00",
            },
        )

        assert response.status_code == 200
        assert response.json()["count"] == 0

        logged = UnmetDemand.objects.filter(society=society, kind=DemandKind.NO_MATCH)
        assert logged.count() == 1
        assert logged.first().service_label == "Deep cleaning"

    def test_the_log_groups_by_service(self, society, resident_user):
        for _ in range(3):
            services.record_unmet_demand(
                society=society,
                kind=DemandKind.NO_MATCH,
                service_label="Cooking",
                requested_by=resident_user,
            )
        services.record_unmet_demand(
            society=society, kind=DemandKind.NO_MATCH, service_label="Ironing"
        )

        today = timezone.localdate()
        panel = analytics.unmet_demand(society.pk, since=today, until=today)

        assert panel["total"] == 4
        assert panel["by_service"][0] == {"service": "Cooking", "count": 3}

    def test_the_list_endpoint_is_administrators_only(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:administration:unmet-demand")
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Module 8.6 join
# ---------------------------------------------------------------------------


class TestPaymentDisputeJoin:
    def test_a_dispute_opens_a_complaint(self, society, resident, worker, resident_user):
        """Module 8.6 promised the handling would live here, not in a second queue."""
        from apps.payments.models import Payment, PaymentKind
        from apps.payments.models import PaymentDispute

        payment = Payment.objects.create(
            society=society,
            resident=resident,
            worker=worker,
            kind=PaymentKind.BOOKING,
            amount_paise=50000,
        )
        dispute = PaymentDispute.objects.create(
            society=society,
            payment=payment,
            raised_by=resident_user,
            reason="not_provided",
            description="The worker never came.",
        )

        complaint = services.raise_from_payment_dispute(dispute)

        assert complaint is not None
        assert complaint.category == ComplaintCategory.PAYMENT
        assert complaint.payment_dispute_id == dispute.pk
        assert complaint.against_worker_id == worker.pk

    def test_mirroring_the_same_dispute_twice_does_nothing(
        self, society, resident, worker, resident_user
    ):
        from apps.payments.models import Payment, PaymentDispute, PaymentKind

        payment = Payment.objects.create(
            society=society,
            resident=resident,
            worker=worker,
            kind=PaymentKind.BOOKING,
            amount_paise=50000,
        )
        dispute = PaymentDispute.objects.create(
            society=society,
            payment=payment,
            raised_by=resident_user,
            reason="wrong_amount",
            description="Charged twice.",
        )

        assert services.raise_from_payment_dispute(dispute) is not None
        dispute.refresh_from_db()
        assert services.raise_from_payment_dispute(dispute) is None
        assert Complaint.objects.filter(payment_dispute=dispute).count() == 1
