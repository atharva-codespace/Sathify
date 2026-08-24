"""
Module 11.5 — cross-society report jobs, without a task queue.

Three properties carry this feature, and each has its own class:

* **Isolation.** A job scoped to one society must never contain another's rows.
  Cross-society reporting is the one place this codebase deliberately reads past
  the tenancy boundary, so the boundary gets asserted directly rather than
  trusted.
* **Partial completion.** One society timing out must not void the rest. A
  design that fails the whole job for one bad tenant means an operator who never
  gets a report at all.
* **Idempotency.** ``docs/free-tier-constraints.md`` §7 makes "whoever happens
  to load the screen" a legitimate trigger, which is only safe if running the
  sweep twice cannot double anything.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import (
    PlatformAccessLog,
    SuperadminLevel,
    SuperadminProfile,
    User,
)
from apps.administration import report_jobs
from apps.administration.models import (
    ReportFormat,
    ReportJob,
    ReportJobStatus,
    ReportKind,
    ReportScope,
)
from apps.attendance.models import AttendanceEvent, Decision, Direction
from apps.payments.models import SocietySubscription, SubscriptionTier
from apps.societies.models import Society, SocietyStatus
from apps.workers.models import WorkerProfile

pytestmark = pytest.mark.django_db

PERIOD = (dt.date(2026, 8, 1), dt.date(2026, 8, 31))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def operator(db):
    user = User.objects.create_superuser(phone_number="9800000099", password="x-12345")
    SuperadminProfile.objects.create(user=user, level=SuperadminLevel.SUPPORT)
    user.refresh_from_db()
    return user


@pytest.fixture
def console(authenticated_client, operator):
    return authenticated_client(operator)


@pytest.fixture
def other_society(db):
    return Society.objects.create(
        name="Palm Grove", address_line="Kalyani Nagar", city="Pune",
        state="Maharashtra", pincode="411006", total_towers=2, total_flats=90,
        status=SocietyStatus.ACTIVE,
    )


def _worker(society, phone, name):
    user = User.objects.create_user(
        phone_number=phone, password="x-12345", role="worker",
        society=society, is_approved=True, first_name=name,
    )
    return WorkerProfile.objects.create(
        user=user, photo="workers/x.jpg", is_available=True,
        trust_score=70, average_rating=4.4,
    )


def _gate_event(society, worker, day):
    return AttendanceEvent.objects.create(
        society=society, worker=worker, direction=Direction.ENTRY,
        decision=Decision.ALLOWED,
        occurred_at=timezone.make_aware(dt.datetime.combine(day, dt.time(9, 5))),
    )


def _queue(**kwargs):
    defaults = {
        "kind": ReportKind.ATTENDANCE,
        "scope": ReportScope.ALL,
        "period_start": PERIOD[0],
        "period_end": PERIOD[1],
        "formats": [ReportFormat.CSV],
    }
    defaults.update(kwargs)
    return report_jobs.queue_report(**defaults)


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestSocietiesStayIsolated:
    def test_a_selected_scope_reads_only_the_chosen_society(
        self, society, other_society
    ):
        _gate_event(society, _worker(society, "9800000011", "Sunita"), PERIOD[0])
        _gate_event(
            other_society, _worker(other_society, "9800000012", "Rekha"), PERIOD[0]
        )

        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        report_jobs.run_pending_jobs()
        job.refresh_from_db()

        assert job.status == ReportJobStatus.READY
        csv_text = job.csv_file.read().decode("utf-8")
        assert society.name in csv_text
        assert other_society.name not in csv_text

    def test_scope_is_decided_in_exactly_one_place(self, society, other_society):
        """The tenancy rule is `societies_in_scope`, and nothing else."""
        job = _queue(scope=ReportScope.SELECTED, societies=[other_society])
        assert list(job.societies_in_scope()) == [other_society]
        assert [row.society_id for row in job.society_jobs.all()] == [other_society.id]

    def test_a_tier_scope_includes_societies_with_no_subscription_row(
        self, society, other_society
    ):
        """Absence of a subscription *is* the free tier — a valid state."""
        SocietySubscription.objects.create(
            society=other_society, tier=SubscriptionTier.STANDARD,
            valid_until=dt.date(2027, 1, 1),
        )
        job = _queue(scope=ReportScope.TIER, tier=SubscriptionTier.FREE)
        assert list(job.societies_in_scope()) == [society]

    def test_a_scope_matching_nothing_fails_loudly_rather_than_hanging(self):
        job = _queue(scope=ReportScope.TIER, tier=SubscriptionTier.PLUS)
        assert job.status == ReportJobStatus.FAILED
        assert "No societies matched" in job.last_error

    def test_every_row_names_its_society(self, society, other_society):
        _gate_event(society, _worker(society, "9800000011", "Sunita"), PERIOD[0])
        _gate_event(
            other_society, _worker(other_society, "9800000012", "Rekha"), PERIOD[0]
        )

        job = _queue()
        report_jobs.run_pending_jobs()
        job.refresh_from_db()

        csv_text = job.csv_file.read().decode("utf-8")
        assert "Society" in csv_text
        assert society.name in csv_text and other_society.name in csv_text


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------


class TestPersonalDetailsAreOptIn:
    def test_names_are_excluded_by_default(self, society):
        _gate_event(society, _worker(society, "9800000011", "Sunita"), PERIOD[0])

        job = _queue()
        report_jobs.run_pending_jobs()
        job.refresh_from_db()

        csv_text = job.csv_file.read().decode("utf-8")
        assert "Worker" not in csv_text
        assert "Sunita" not in csv_text
        assert "Personal details,Excluded" in csv_text.replace(", ", ",")

    def test_names_appear_only_when_asked_for(self, society):
        _gate_event(society, _worker(society, "9800000011", "Sunita"), PERIOD[0])

        job = _queue(include_pii=True, reason="regulator asked for named records")
        report_jobs.run_pending_jobs()
        job.refresh_from_db()

        csv_text = job.csv_file.read().decode("utf-8")
        assert "Worker" in csv_text
        assert "Sunita" in csv_text

    def test_the_api_refuses_pii_without_a_stated_reason(self, console, society):
        response = console.post(
            reverse("v1:console:report-create"),
            {
                "kind": ReportKind.ATTENDANCE, "scope": ReportScope.ALL,
                "period_start": str(PERIOD[0]), "period_end": str(PERIOD[1]),
                "formats": [ReportFormat.CSV], "include_pii": True, "reason": "why",
            },
            format="json",
        )
        assert response.status_code == 400
        assert not ReportJob.objects.exists()

    def test_a_pii_export_is_logged_against_each_society_in_it(
        self, console, society, other_society
    ):
        """Each society can see its people appeared in a platform-wide extract."""
        response = console.post(
            reverse("v1:console:report-create"),
            {
                "kind": ReportKind.ATTENDANCE, "scope": ReportScope.ALL,
                "period_start": str(PERIOD[0]), "period_end": str(PERIOD[1]),
                "formats": [ReportFormat.CSV], "include_pii": True,
                "reason": "regulator asked for named attendance records",
            },
            format="json",
        )
        assert response.status_code == 201

        logs = PlatformAccessLog.objects.filter(action="report.export_pii")
        assert logs.count() == 2
        assert set(logs.values_list("society_id", flat=True)) == {
            society.id, other_society.id
        }

    def test_a_non_pii_export_is_not_logged_per_society(self, console, society):
        console.post(
            reverse("v1:console:report-create"),
            {
                "kind": ReportKind.ATTENDANCE, "scope": ReportScope.ALL,
                "period_start": str(PERIOD[0]), "period_end": str(PERIOD[1]),
                "formats": [ReportFormat.CSV],
            },
            format="json",
        )
        assert not PlatformAccessLog.objects.filter(action="report.export_pii").exists()


# ---------------------------------------------------------------------------
# Partial completion and retry
# ---------------------------------------------------------------------------


class TestOneSocietyDoesNotVoidTheRest:
    def _break_one(self, monkeypatch, doomed_name):
        """Make exactly one society's build raise, as a slow tenant would."""
        real_build = report_jobs.reports.build

        def flaky(kind, society, *, start, end):
            if society.name == doomed_name:
                raise TimeoutError("statement timeout")
            return real_build(kind, society, start=start, end=end)

        monkeypatch.setattr(report_jobs.reports, "build", flaky)

    def test_the_job_finishes_partial_with_the_others_intact(
        self, monkeypatch, society, other_society
    ):
        _gate_event(society, _worker(society, "9800000011", "Sunita"), PERIOD[0])
        self._break_one(monkeypatch, other_society.name)

        job = _queue()
        for _ in range(ReportJob.MAX_ATTEMPTS + 1):
            report_jobs.run_pending_jobs()
        job.refresh_from_db()

        assert job.status == ReportJobStatus.PARTIAL
        assert job.is_downloadable is True
        csv_text = job.csv_file.read().decode("utf-8")
        assert society.name in csv_text

    def test_the_missing_societies_are_named_not_merely_counted(
        self, monkeypatch, society, other_society
    ):
        _gate_event(society, _worker(society, "9800000011", "Sunita"), PERIOD[0])
        self._break_one(monkeypatch, other_society.name)

        job = _queue()
        for _ in range(ReportJob.MAX_ATTEMPTS + 1):
            report_jobs.run_pending_jobs()
        job.refresh_from_db()

        csv_text = job.csv_file.read().decode("utf-8")
        assert other_society.name in csv_text  # in the "Societies missing" summary

    def test_a_failing_society_is_retried_before_being_given_up_on(
        self, monkeypatch, society, other_society
    ):
        self._break_one(monkeypatch, other_society.name)
        job = _queue()

        report_jobs.run_pending_jobs()
        slice_row = job.society_jobs.get(society=other_society)
        assert slice_row.attempts == 1
        assert slice_row.status == ReportJobStatus.PENDING  # still retryable

        for _ in range(ReportJob.MAX_ATTEMPTS):
            report_jobs.run_pending_jobs()
        slice_row.refresh_from_db()
        assert slice_row.attempts == ReportJob.MAX_ATTEMPTS
        assert slice_row.status == ReportJobStatus.FAILED

    def test_every_society_failing_fails_the_job(self, monkeypatch, society):
        def always_fail(kind, s, *, start, end):
            raise TimeoutError("statement timeout")

        monkeypatch.setattr(report_jobs.reports, "build", always_fail)

        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        for _ in range(ReportJob.MAX_ATTEMPTS + 1):
            report_jobs.run_pending_jobs()
        job.refresh_from_db()

        assert job.status == ReportJobStatus.FAILED
        assert job.is_downloadable is False

    def test_retry_requeues_only_what_failed(
        self, monkeypatch, society, other_society
    ):
        self._break_one(monkeypatch, other_society.name)
        job = _queue()
        for _ in range(ReportJob.MAX_ATTEMPTS + 1):
            report_jobs.run_pending_jobs()

        good = job.society_jobs.get(society=society)
        assert good.status == ReportJobStatus.READY
        cached = good.payload

        # The tenant recovers — genuinely, by removing the patch rather than
        # reassigning it to itself.
        monkeypatch.undo()
        requeued = report_jobs.retry_failed_societies(job)

        assert requeued == 1
        good.refresh_from_db()
        # The society that worked keeps its rows — a retry costs one society's
        # work, not the whole build.
        assert good.status == ReportJobStatus.READY
        assert good.payload == cached

        # And the retry actually completes, which is the point of resetting the
        # attempt budget: the sweep had already given up once.
        report_jobs.run_pending_jobs()
        job.refresh_from_db()
        assert job.status == ReportJobStatus.READY
        assert job.society_jobs.filter(status=ReportJobStatus.FAILED).count() == 0

    def test_the_operator_button_survives_an_exhausted_auto_budget(
        self, monkeypatch, society, other_society
    ):
        """The sweep gives up; a person may still try. They are not the same call."""
        self._break_one(monkeypatch, other_society.name)
        job = _queue()
        for _ in range(ReportJob.MAX_ATTEMPTS + 1):
            report_jobs.run_pending_jobs()

        slice_row = job.society_jobs.get(society=other_society)
        assert slice_row.has_auto_attempts_left is False
        assert slice_row.can_retry is True


# ---------------------------------------------------------------------------
# Idempotency and boundedness
# ---------------------------------------------------------------------------


class TestTheSweepIsSafeToRunTwice:
    def test_running_it_again_does_not_duplicate_rows(self, society, other_society):
        _gate_event(society, _worker(society, "9800000011", "Sunita"), PERIOD[0])

        job = _queue()
        report_jobs.run_pending_jobs()
        job.refresh_from_db()
        first = job.row_count

        report_jobs.run_pending_jobs()
        job.refresh_from_db()
        assert job.row_count == first

    def test_a_finished_job_is_not_picked_up_again(self, society):
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        report_jobs.run_pending_jobs()
        job.refresh_from_db()
        finished_at = job.finished_at

        report_jobs.run_pending_jobs()
        job.refresh_from_db()
        assert job.finished_at == finished_at

    def test_the_sweep_is_bounded(self, society, other_society):
        """A drain-on-read must not become the slow request."""
        job = _queue()
        result = report_jobs.run_pending_jobs(limit=1)

        assert result["built"] == 1
        assert job.society_jobs.filter(status=ReportJobStatus.PENDING).count() == 1
        job.refresh_from_db()
        assert job.status == ReportJobStatus.PENDING  # lease released for the next pass

    def test_a_job_abandoned_mid_build_is_reclaimed(self, society):
        """A process dying must not strand a job in RUNNING forever."""
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        ReportJob.objects.filter(pk=job.pk).update(
            status=ReportJobStatus.RUNNING,
            started_at=timezone.now() - dt.timedelta(minutes=ReportJob.LEASE_MINUTES + 1),
        )
        report_jobs.run_pending_jobs()
        job.refresh_from_db()
        assert job.status == ReportJobStatus.READY

    def test_a_freshly_claimed_job_is_left_alone(self, society):
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        ReportJob.objects.filter(pk=job.pk).update(
            status=ReportJobStatus.RUNNING, started_at=timezone.now()
        )
        assert report_jobs.run_pending_jobs() == {
            "built": 0, "failed": 0, "finished": 0
        }


# ---------------------------------------------------------------------------
# The three triggers
# ---------------------------------------------------------------------------


class TestAllThreeTriggersDriveTheSameSweep:
    def test_the_management_command(self, society):
        _queue(scope=ReportScope.SELECTED, societies=[society])
        call_command("process_report_jobs", "--until-done")
        assert ReportJob.objects.get().status == ReportJobStatus.READY

    def test_the_pinger_endpoint(self, console, society):
        _queue(scope=ReportScope.SELECTED, societies=[society])
        response = console.post(reverse("v1:console:report-run"))
        assert response.status_code == 200
        assert response.json()["finished"] == 1

    def test_loading_the_list_advances_the_work(self, console, society):
        _queue(scope=ReportScope.SELECTED, societies=[society])
        response = console.get(reverse("v1:console:reports"))
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == ReportJobStatus.READY

    def test_the_command_prunes_expired_files(self, society):
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        report_jobs.run_pending_jobs()
        job.refresh_from_db()
        assert job.csv_file

        ReportJob.objects.filter(pk=job.pk).update(
            expires_at=timezone.now() - dt.timedelta(days=1)
        )
        call_command("process_report_jobs", "--prune")
        job.refresh_from_db()
        # The row survives — "who exported what" outlives the file.
        assert not job.csv_file
        assert ReportJob.objects.filter(pk=job.pk).exists()


# ---------------------------------------------------------------------------
# The API surface
# ---------------------------------------------------------------------------


class TestTheReportEndpoints:
    def test_only_a_platform_operator_may_queue_one(
        self, authenticated_client, admin_user
    ):
        response = authenticated_client(admin_user).post(
            reverse("v1:console:report-create"),
            {
                "kind": ReportKind.ATTENDANCE, "scope": ReportScope.ALL,
                "period_start": str(PERIOD[0]), "period_end": str(PERIOD[1]),
                "formats": [ReportFormat.CSV],
            },
            format="json",
        )
        assert response.status_code == 403
        assert not ReportJob.objects.exists()

    def test_a_backwards_period_is_refused(self, console):
        response = console.post(
            reverse("v1:console:report-create"),
            {
                "kind": ReportKind.ATTENDANCE, "scope": ReportScope.ALL,
                "period_start": str(PERIOD[1]), "period_end": str(PERIOD[0]),
                "formats": [ReportFormat.CSV],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_a_tier_scope_needs_a_tier(self, console):
        response = console.post(
            reverse("v1:console:report-create"),
            {
                "kind": ReportKind.ATTENDANCE, "scope": ReportScope.TIER,
                "period_start": str(PERIOD[0]), "period_end": str(PERIOD[1]),
                "formats": [ReportFormat.CSV],
            },
            format="json",
        )
        assert response.status_code == 400

    def test_downloading_before_it_is_ready_is_a_409_not_a_404(
        self, console, society
    ):
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        response = console.get(
            reverse(
                "v1:console:report-download", kwargs={"pk": job.pk, "fmt": "csv"}
            )
        )
        assert response.status_code == 409

    def test_downloading_a_format_that_was_not_built(self, console, society):
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        report_jobs.run_pending_jobs()
        response = console.get(
            reverse(
                "v1:console:report-download", kwargs={"pk": job.pk, "fmt": "pdf"}
            )
        )
        assert response.status_code == 404

    def test_a_ready_csv_downloads(self, console, society):
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        report_jobs.run_pending_jobs()
        response = console.get(
            reverse(
                "v1:console:report-download", kwargs={"pk": job.pk, "fmt": "csv"}
            )
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert "attachment" in response["Content-Disposition"]

    def test_retrying_a_healthy_job_is_refused(self, console, society):
        job = _queue(scope=ReportScope.SELECTED, societies=[society])
        report_jobs.run_pending_jobs()
        response = console.post(
            reverse("v1:console:report-retry", kwargs={"pk": job.pk})
        )
        assert response.status_code == 400

    def test_the_list_reports_progress(self, console, society, other_society):
        _queue()
        report_jobs.run_pending_jobs(limit=1)
        body = console.get(reverse("v1:console:reports")).json()
        progress = body["results"][0]["progress"]
        assert progress["total"] == 2
        assert progress["done"] >= 1
