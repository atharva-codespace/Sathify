"""
Module 11.5 — building a report across every society, without a task queue.

-------------------------------------------------------------------------------
THE SWEEP, NOT A BROKER
-------------------------------------------------------------------------------
``docs/free-tier-constraints.md`` §7 rules out Celery and cron on this project's
plan, and warns that adding one anyway is worse than going without: "tasks would
be accepted into a queue nothing drains, the client would poll a 'processing'
state forever, and no error would ever explain why."

So this file is the sweep that document prescribes instead — *idempotent,
bounded, and safe to run twice* — with the same three triggers every other
deferred job in this codebase uses:

* a read that naturally passes it (``console.views.ReportJobListView``),
* an endpoint the external uptime pinger can call (``.../reports/run/``),
* a management command (``process_report_jobs``).

Bounded matters as much as idempotent. :func:`run_pending_jobs` builds at most
``limit`` societies per call, so a sweep triggered by somebody opening a screen
cannot hold the single free-tier web worker for the length of a 128-society
report.

-------------------------------------------------------------------------------
TENANCY LIVES IN ONE PLACE
-------------------------------------------------------------------------------
Every row this module reads comes from ``ReportJob.societies_in_scope()``, and
each society is built by the *existing* per-society builder in ``reports.py``.
There is no cross-society query anywhere in here — the fan-out is a Python loop
over a scoped list, which is why a scoping mistake cannot leak through a
builder, and why the isolation tests can assert on one method.
"""

from __future__ import annotations

import datetime as dt
import io
import logging

from django.core.files.base import ContentFile
from django.db import models, transaction
from django.utils import timezone

from . import reports
from .models import (
    ReportFormat,
    ReportJob,
    ReportJobSociety,
    ReportJobStatus,
)

logger = logging.getLogger(__name__)

#: Societies built per sweep call. Small enough that a drain-on-read cannot
#: stall the request that triggered it, large enough that the uptime pinger's
#: ten-minute cadence still finishes a full platform report in one sitting.
DEFAULT_SWEEP_LIMIT = 12

#: Columns that name a person, per report kind. Stripped unless the operator
#: explicitly asked for PII and said why.
#:
#: Held here as column *labels* rather than indices because ``reports.py`` owns
#: the column order and will change it; a label that stops matching is a loud
#: KeyError-shaped bug in a test, while a stale index silently exports the wrong
#: column — which for this particular feature means silently exporting a name.
PII_COLUMNS = {
    "attendance": {"Worker"},
    "payments": {"Worker", "Resident"},
    "complaints": {"About"},
}


class ReportJobError(Exception):
    """A build failed for a reason that is a business fact, not a bug."""


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


@transaction.atomic
def queue_report(
    *,
    kind: str,
    scope: str,
    period_start: dt.date,
    period_end: dt.date,
    formats: list[str],
    requested_by=None,
    tier: str = "",
    societies=None,
    include_pii: bool = False,
    reason: str = "",
) -> ReportJob:
    """Create a job and its per-society slices.

    The slices are materialised *now*, not when the sweep runs, so the job knows
    its own size immediately. An operator watching a progress bar that only
    learns its denominator halfway through has no way to tell "slow" from
    "stuck", and this feature's whole failure mode is looking stuck.
    """
    if period_end < period_start:
        raise ReportJobError("The period ends before it starts.")
    if not formats:
        raise ReportJobError("Choose at least one format.")

    job = ReportJob.objects.create(
        requested_by=requested_by,
        kind=kind,
        scope=scope,
        tier=tier,
        period_start=period_start,
        period_end=period_end,
        formats=list(formats),
        include_pii=include_pii,
        reason=reason,
        expires_at=timezone.now() + dt.timedelta(days=ReportJob.RETENTION_DAYS),
    )
    if societies:
        job.societies.set(societies)

    in_scope = list(job.societies_in_scope())
    if not in_scope:
        job.status = ReportJobStatus.FAILED
        job.last_error = "No societies matched that scope."
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "last_error", "finished_at"])
        return job

    ReportJobSociety.objects.bulk_create(
        [ReportJobSociety(job=job, society=society) for society in in_scope]
    )
    return job


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def run_pending_jobs(*, limit: int = DEFAULT_SWEEP_LIMIT) -> dict:
    """Advance queued report jobs. Safe to call from anywhere, at any time.

    Returns a small summary so a caller can log it or show it. Never raises for
    a build failure: a report that cannot be produced is recorded on the row and
    the sweep moves on, because one bad tenant stalling every other operator's
    report is exactly the outcome the per-society design exists to avoid.
    """
    built = failed = finished = 0
    budget = max(1, int(limit))

    for job in ReportJob.objects.claimable().order_by("created_at"):
        if budget <= 0:
            break
        if not _claim(job):
            # Another sweep took it between the query and here. Not an error —
            # two triggers firing at once is the normal case, not the odd one.
            continue

        slices = list(
            job.society_jobs.filter(status=ReportJobStatus.PENDING)
            .select_related("society")[:budget]
        )
        for slice_row in slices:
            budget -= 1
            if _build_slice(job, slice_row):
                built += 1
            else:
                failed += 1

        if not job.society_jobs.filter(status=ReportJobStatus.PENDING).exists():
            _finalise(job)
            finished += 1
        else:
            # Still work left. Release the lease so the next trigger picks it up
            # rather than waiting for the lease to lapse.
            ReportJob.objects.filter(pk=job.pk).update(
                status=ReportJobStatus.PENDING, started_at=None
            )

    return {"built": built, "failed": failed, "finished": finished}


def _claim(job: ReportJob) -> bool:
    """Take the lease on a job. Returns False if somebody else already has it.

    A conditional UPDATE rather than a select-then-save: two sweeps racing —
    the pinger and somebody opening the reports screen — must not both build the
    same society and double its rows in the merged file.
    """
    stale = timezone.now() - dt.timedelta(minutes=ReportJob.LEASE_MINUTES)
    claimable = models.Q(status=ReportJobStatus.PENDING) | models.Q(
        status=ReportJobStatus.RUNNING, started_at__lt=stale
    )
    claimed = (
        ReportJob.objects.filter(pk=job.pk)
        .filter(claimable)
        .update(status=ReportJobStatus.RUNNING, started_at=timezone.now())
    )
    return bool(claimed)


def _build_slice(job: ReportJob, slice_row: ReportJobSociety) -> bool:
    """Build one society's rows and cache them. Returns whether it worked."""
    slice_row.attempts += 1
    try:
        report = reports.build(
            job.kind,
            slice_row.society,
            start=job.period_start,
            end=job.period_end,
        )
    except Exception as error:  # noqa: BLE001 — one tenant must not stop the rest
        logger.warning(
            "Report job %s failed for society %s: %s",
            job.pk, slice_row.society_id, error,
        )
        slice_row.status = (
            ReportJobStatus.PENDING
            if slice_row.has_auto_attempts_left
            else ReportJobStatus.FAILED
        )
        slice_row.last_error = str(error)[:300]
        slice_row.save(update_fields=["attempts", "status", "last_error", "updated_at"])
        return False

    slice_row.payload = {
        "columns": report.columns,
        "rows": report.rows,
        "summary": [[label, value] for label, value in report.summary],
    }
    slice_row.row_count = report.row_count
    slice_row.status = ReportJobStatus.READY
    slice_row.last_error = ""
    slice_row.save(
        update_fields=[
            "attempts", "payload", "row_count", "status", "last_error", "updated_at",
        ]
    )
    return True


def _merged_report(job: ReportJob) -> reports.Report:
    """Stitch the built slices into one table.

    A ``Society`` column is prepended rather than appended: it is the column a
    reader sorts and filters by first, and a merged export whose rows cannot be
    attributed to a tenant is not a cross-society report, it is a pile.
    """
    built = list(
        job.society_jobs.filter(status=ReportJobStatus.READY).select_related("society")
    )
    columns: list[str] = []
    for slice_row in built:
        payload_columns = slice_row.payload.get("columns") or []
        if payload_columns:
            columns = list(payload_columns)
            break

    drop = set()
    if not job.include_pii:
        sensitive = PII_COLUMNS.get(job.kind, set())
        drop = {index for index, name in enumerate(columns) if name in sensitive}

    kept = [name for index, name in enumerate(columns) if index not in drop]

    rows: list[list[str]] = []
    for slice_row in built:
        for row in slice_row.payload.get("rows") or []:
            trimmed = [
                value for index, value in enumerate(row) if index not in drop
            ]
            rows.append([slice_row.society.name, *trimmed])

    total_rows = len(rows)
    summary = [
        ("Societies included", str(len(built))),
        ("Rows", str(total_rows)),
        ("Period", f"{job.period_start:%d %b %Y} - {job.period_end:%d %b %Y}"),
    ]
    missing = list(
        job.society_jobs.filter(status=ReportJobStatus.FAILED).select_related("society")
    )
    if missing:
        # Named, not counted. An operator handed a report with silent gaps will
        # draw conclusions from a total that is missing three societies.
        summary.append(
            ("Societies missing", ", ".join(row.society.name for row in missing))
        )
    if not job.include_pii:
        summary.append(("Personal details", "Excluded"))

    return reports.Report(
        title=f"{job.get_kind_display()} — all societies in scope",
        society_name=f"{len(built)} societies",
        period_start=job.period_start,
        period_end=job.period_end,
        columns=["Society", *kept],
        rows=rows,
        summary=summary,
    )


def _finalise(job: ReportJob) -> ReportJob:
    """Render the merged report and attach the files."""
    report = _merged_report(job)
    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    base = f"{job.kind}-{stamp}-{str(job.pk)[:8]}"

    try:
        if ReportFormat.CSV in job.formats:
            job.csv_file.save(
                f"{base}.csv",
                ContentFile(reports.render_csv(report).encode("utf-8")),
                save=False,
            )
        if ReportFormat.PDF in job.formats:
            job.pdf_file.save(
                f"{base}.pdf",
                ContentFile(reports.render_pdf(report)),
                save=False,
            )
    except Exception as error:  # noqa: BLE001
        logger.exception("Could not render report job %s", job.pk)
        job.status = ReportJobStatus.FAILED
        job.last_error = f"Could not render the file: {error}"[:300]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "last_error", "finished_at"])
        return job

    any_failed = job.society_jobs.filter(status=ReportJobStatus.FAILED).exists()
    any_built = job.society_jobs.filter(status=ReportJobStatus.READY).exists()

    job.row_count = report.row_count
    job.finished_at = timezone.now()
    if not any_built:
        job.status = ReportJobStatus.FAILED
        job.last_error = "No society could be built."
    else:
        job.status = ReportJobStatus.PARTIAL if any_failed else ReportJobStatus.READY
    job.save(
        update_fields=[
            "csv_file", "pdf_file", "row_count", "status", "last_error", "finished_at",
        ]
    )
    return job


# ---------------------------------------------------------------------------
# Retry and housekeeping
# ---------------------------------------------------------------------------


def retry_failed_societies(job: ReportJob) -> int:
    """Re-queue only the societies that did not build. Returns how many.

    The successful slices keep their cached payload, so a retry costs one
    society's work rather than a hundred and twenty-eight — which is what makes
    offering the button reasonable at all.
    """
    rows = [row for row in job.society_jobs.all() if row.can_retry]
    if not rows:
        return 0

    # The attempt counter resets. An operator pressing Retry has normally just
    # fixed whatever broke, so carrying the old budget forward would let the
    # sweep give up again before it had genuinely tried under the new
    # conditions.
    ReportJobSociety.objects.filter(pk__in=[row.pk for row in rows]).update(
        status=ReportJobStatus.PENDING, attempts=0, last_error=""
    )
    ReportJob.objects.filter(pk=job.pk).update(
        status=ReportJobStatus.PENDING,
        started_at=None,
        finished_at=None,
        last_error="",
    )
    return len(rows)


def prune_expired(*, now=None) -> int:
    """Delete the files behind expired jobs. Returns how many were cleared.

    The rows stay — "who exported what, and when" is an audit question and
    outlives the file it produced. Only the payload goes.
    """
    now = now or timezone.now()
    cleared = 0
    for job in ReportJob.objects.filter(expires_at__lt=now).exclude(
        csv_file="", pdf_file=""
    ):
        job.csv_file.delete(save=False)
        job.pdf_file.delete(save=False)
        job.csv_file = ""
        job.pdf_file = ""
        job.save(update_fields=["csv_file", "pdf_file"])
        cleared += 1
    return cleared


__all__ = [
    "DEFAULT_SWEEP_LIMIT",
    "PII_COLUMNS",
    "ReportJobError",
    "prune_expired",
    "queue_report",
    "retry_failed_societies",
    "run_pending_jobs",
]
