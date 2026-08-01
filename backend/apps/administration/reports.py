"""
Module 11.2 — attendance, payment and complaint reports.

SRS section 6 requires CSV and PDF export for exactly these three, "for
compliance record-keeping". So one generic tabular :class:`Report` is assembled
by three builders and rendered by two renderers, rather than six bespoke
documents that would drift apart.

-------------------------------------------------------------------------------
WHY THIS DOES NOT REUSE apps/payments/summary.py
-------------------------------------------------------------------------------
That module renders a *statement*: one worker, one month, a specific layout with
the total as the largest thing on the page, designed to be handed to a landlord
as proof of income. This renders *tables*: arbitrary columns, many rows, meant
to be filed or opened in a spreadsheet.

Generalising one renderer to do both would make the statement worse — the
things that make it good as a document are exactly the things a generic table
renderer cannot express. The overlap is about thirty lines of reportlab
boilerplate, which is a cheaper duplication than the wrong abstraction.

-------------------------------------------------------------------------------
EVERY REPORT CARRIES THE QUESTION IT ANSWERS
-------------------------------------------------------------------------------
Both renderers print the society, the date range and the moment of generation
onto the document. A compliance export that does not say what it covers is
worthless six months later, when nobody remembers which filters were applied.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field

from django.utils import timezone


@dataclass
class Report:
    """A rendered-format-agnostic table."""

    title: str
    society_name: str
    period_start: dt.date
    period_end: dt.date

    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    #: Label/value pairs printed above the table — counts, totals, breach rates.
    #: The part most readers actually look at, so it comes first on the page.
    summary: list[tuple[str, str]] = field(default_factory=list)

    #: Right-aligned in the PDF and excluded from truncation. Money and counts.
    numeric_columns: set[int] = field(default_factory=set)

    @property
    def period_label(self) -> str:
        return f"{self.period_start:%d %b %Y} – {self.period_end:%d %b %Y}"

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def as_dict(self) -> dict:
        """The JSON form, so the app can show the same report on screen.

        Returning the identical structure the files are rendered from means a
        figure on screen and a figure in the exported PDF cannot disagree.
        """
        return {
            "title": self.title,
            "society_name": self.society_name,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "period_label": self.period_label,
            "columns": self.columns,
            "rows": self.rows,
            "summary": [{"label": label, "value": value} for label, value in self.summary],
            "row_count": self.row_count,
        }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def attendance_report(society, *, start: dt.date, end: dt.date) -> Report:
    """Every gate decision in the period (11.2, SRS 3.11 entry/exit logs)."""
    from apps.attendance.models import AttendanceEvent, Decision

    events = (
        AttendanceEvent.objects.filter(
            society=society,
            occurred_at__date__gte=start,
            occurred_at__date__lte=end,
        )
        .select_related("worker__user", "gate", "recorded_by")
        .order_by("occurred_at")
    )

    rows = []
    decisions = {value: 0 for value, _label in Decision.choices}

    for event in events:
        decisions[event.decision] = decisions.get(event.decision, 0) + 1
        local = timezone.localtime(event.occurred_at)
        rows.append(
            [
                local.strftime("%d %b %Y"),
                local.strftime("%H:%M"),
                _worker_name(event.worker),
                event.get_direction_display(),
                event.get_decision_display(),
                event.gate.name if event.gate_id else "—",
                event.get_method_display(),
            ]
        )

    allowed = decisions.get(Decision.ALLOWED, 0)
    return Report(
        title="Attendance and gate log",
        society_name=str(society),
        period_start=start,
        period_end=end,
        columns=["Date", "Time", "Worker", "Direction", "Decision", "Gate", "Method"],
        rows=rows,
        summary=[
            ("Events recorded", str(len(rows))),
            ("Allowed", str(allowed)),
            ("Denied", str(decisions.get(Decision.DENIED, 0))),
            ("Pending review", str(decisions.get(Decision.PENDING_REVIEW, 0))),
        ],
        numeric_columns={1},
    )


def payment_report(society, *, start: dt.date, end: dt.date) -> Report:
    """Every payment raised in the period, settled or not (11.2).

    Deliberately not filtered to settled payments: a compliance report that
    silently omits the failures answers the easy question and hides the one
    somebody is usually asking.
    """
    from apps.payments.models import Payment, PaymentStatus, format_paise

    payments = (
        Payment.objects.filter(
            society=society, created_at__date__gte=start, created_at__date__lte=end
        )
        .select_related("worker__user", "resident__user", "resident__flat")
        .order_by("created_at")
    )

    rows = []
    settled_paise = 0
    refunded_paise = 0
    pending = 0
    failed = 0

    for payment in payments:
        if payment.status == PaymentStatus.PAID:
            settled_paise += payment.net_paise
        elif payment.status == PaymentStatus.REFUNDED:
            refunded_paise += payment.refunded_paise
        elif payment.status == PaymentStatus.FAILED:
            failed += 1
        elif payment.is_open:
            # Still payable. Cancelled rows are neither open nor failed and are
            # deliberately counted in none of these.
            pending += 1

        rows.append(
            [
                timezone.localdate(payment.created_at).strftime("%d %b %Y"),
                payment.receipt_number,
                _worker_name(payment.worker),
                _resident_name(payment.resident),
                payment.get_kind_display(),
                payment.get_status_display(),
                format_paise(payment.net_paise),
            ]
        )

    return Report(
        title="Payment report",
        society_name=str(society),
        period_start=start,
        period_end=end,
        columns=["Date", "Receipt", "Worker", "Resident", "Type", "Status", "Net"],
        rows=rows,
        summary=[
            ("Payments recorded", str(len(rows))),
            ("Settled", format_paise(settled_paise)),
            ("Refunded", format_paise(refunded_paise)),
            ("Awaiting settlement", str(pending)),
            ("Failed", str(failed)),
        ],
        numeric_columns={6},
    )


def complaint_report(society, *, start: dt.date, end: dt.date) -> Report:
    """Complaints raised in the period, with how the SLA was met (11.2/11.3)."""
    from .models import Complaint

    complaints = (
        Complaint.objects.filter(society=society)
        .for_period(start, end)
        .select_related("raised_by", "against_worker__user", "against_resident__user")
        .order_by("created_at")
    )

    rows = []
    breached = 0
    open_count = 0

    for complaint in complaints:
        # A closed complaint counts as breached if it was *resolved* late, not
        # if it is late now — otherwise every historical report would slowly
        # accumulate breaches as the clock ran on already-finished work.
        finished = complaint.resolved_at
        was_late = bool(
            complaint.sla_due_at
            and ((finished or timezone.now()) > complaint.sla_due_at)
        )
        if was_late:
            breached += 1
        if complaint.is_open:
            open_count += 1

        rows.append(
            [
                timezone.localdate(complaint.created_at).strftime("%d %b %Y"),
                complaint.reference,
                complaint.get_category_display(),
                complaint.subject,
                complaint.subject_label,
                complaint.get_status_display(),
                f"{complaint.age_active_hours:.1f} h",
                "Yes" if was_late else "No",
            ]
        )

    total = len(rows)
    return Report(
        title="Complaint report",
        society_name=str(society),
        period_start=start,
        period_end=end,
        columns=[
            "Raised",
            "Reference",
            "Category",
            "Subject",
            "About",
            "Status",
            "Open for",
            "Past SLA",
        ],
        rows=rows,
        summary=[
            ("Complaints raised", str(total)),
            ("Still open", str(open_count)),
            ("Past the response window", str(breached)),
            ("Met the response window", _percentage(total - breached, total)),
        ],
        numeric_columns={6},
    )


#: The report kinds the API accepts, mapped to their builders.
REPORT_BUILDERS = {
    "attendance": attendance_report,
    "payments": payment_report,
    "complaints": complaint_report,
}


def build(kind: str, society, *, start: dt.date, end: dt.date) -> Report:
    builder = REPORT_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown report: {kind}")
    return builder(society, start=start, end=end)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_csv(report: Report) -> str:
    """The spreadsheet form."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow([f"Sathify — {report.title}"])
    writer.writerow(["Society", report.society_name])
    writer.writerow(["Period", report.period_label])
    writer.writerow(["Generated", timezone.localtime().strftime("%d %b %Y %H:%M")])
    writer.writerow([])

    for label, value in report.summary:
        writer.writerow([label, value])
    if report.summary:
        writer.writerow([])

    writer.writerow(report.columns)
    for row in report.rows:
        writer.writerow(row)

    if not report.rows:
        writer.writerow(["No records in this period."])

    return buffer.getvalue()


def render_pdf(report: Report) -> bytes:
    """The filing-cabinet form.

    Landscape, because a seven-column table on A4 portrait either wraps or gets
    truncated, and a compliance document with a truncated column is worse than
    no document.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    page_size = landscape(A4)
    width, height = page_size

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=page_size)

    left = 15 * mm
    right = width - 15 * mm
    column_x = _column_positions(left, right, len(report.columns))

    def header() -> float:
        y = height - 15 * mm
        page.setFont("Helvetica-Bold", 15)
        page.drawString(left, y, "Sathify")
        page.setFont("Helvetica", 10)
        page.drawRightString(right, y, report.title)

        y -= 7 * mm
        page.setFont("Helvetica-Bold", 11)
        page.drawString(left, y, report.society_name)
        page.setFont("Helvetica", 9)
        page.drawRightString(right, y, report.period_label)
        return y - 6 * mm

    def column_headings(y: float) -> float:
        page.setFont("Helvetica-Bold", 8)
        for index, name in enumerate(report.columns):
            if index in report.numeric_columns and index == len(report.columns) - 1:
                page.drawRightString(right, y, name)
            else:
                page.drawString(column_x[index], y, name)
        y -= 2 * mm
        page.line(left, y, right, y)
        return y - 4.5 * mm

    y = header()

    if report.summary:
        page.setFont("Helvetica", 9)
        for label, value in report.summary:
            page.drawString(left, y, f"{label}:")
            page.drawString(left + 55 * mm, y, value)
            y -= 5 * mm
        y -= 2 * mm

    y = column_headings(y)
    page.setFont("Helvetica", 8)

    for row in report.rows:
        if y < 20 * mm:
            page.showPage()
            y = column_headings(header())
            page.setFont("Helvetica", 8)

        for index, cell in enumerate(row):
            text = str(cell)
            if index in report.numeric_columns and index == len(row) - 1:
                page.drawRightString(right, y, text)
            else:
                page.drawString(column_x[index], y, _fit(text, index, column_x, right))
        y -= 5 * mm

    if not report.rows:
        page.setFont("Helvetica-Oblique", 9)
        page.drawString(left, y, "No records in this period.")

    page.setFont("Helvetica", 7)
    page.drawString(
        left,
        10 * mm,
        f"Generated by Sathify on {timezone.localtime():%d %b %Y at %H:%M}. "
        f"{report.row_count} record(s).",
    )

    page.showPage()
    page.save()
    return buffer.getvalue()


def _column_positions(left: float, right: float, count: int) -> list[float]:
    if count <= 0:
        return []
    span = (right - left) / count
    return [left + index * span for index in range(count)]


def _fit(text: str, index: int, column_x: list[float], right: float) -> str:
    """Trim a cell to its column so it cannot overwrite its neighbour.

    Characters rather than measured width: Helvetica at 8pt averages close
    enough to 4pt per character that this is accurate to a character or two, and
    an exact fit would need a string-width call per cell on every row.
    """
    limit = right if index >= len(column_x) - 1 else column_x[index + 1]
    available = max(4, int((limit - column_x[index]) / 4) - 1)
    return text if len(text) <= available else text[: available - 1] + "…"


def _percentage(part: int, whole: int) -> str:
    if not whole:
        return "—"
    return f"{round(100 * part / whole)}%"


def _worker_name(worker) -> str:
    if worker is None:
        return "—"
    user = getattr(worker, "user", None)
    return (user.get_full_name() or user.phone_number) if user else "—"


def _resident_name(resident) -> str:
    if resident is None:
        return "—"
    user = getattr(resident, "user", None)
    name = (user.get_full_name() or user.phone_number) if user else "—"
    flat = getattr(resident, "flat", None)
    return f"{name} ({flat})" if flat else name


__all__ = [
    "REPORT_BUILDERS",
    "Report",
    "attendance_report",
    "build",
    "complaint_report",
    "payment_report",
    "render_csv",
    "render_pdf",
]
