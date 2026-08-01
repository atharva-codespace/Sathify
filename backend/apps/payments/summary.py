"""
Module 8.3 — monthly salary summaries and receipts.

A worker needs a document they can show to a landlord, a lender, or a government
scheme, and a resident needs one for their own records. So both formats the
modspec asks for are produced from one assembled summary: CSV for anything that
will be opened in a spreadsheet, PDF for anything that will be printed or
forwarded.

-------------------------------------------------------------------------------
FORMATTING HAPPENS ONCE, AT THE EDGE
-------------------------------------------------------------------------------
Everything upstream counts in paise (see models.py). Rupee strings are produced
here and nowhere else, so there is exactly one place where money becomes text
and no chance of two documents disagreeing about the same figure.
"""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import io
from dataclasses import dataclass, field

from .models import Payment, PaymentKind, format_paise


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    """First and last day of a month."""
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last)


@dataclass
class SummaryLine:
    """One settled payment, as it appears on a statement."""

    #: The payment's id, so a statement line can be opened as a receipt. The
    #: receipt number is for humans to read; this is what addresses the record.
    payment_id: str
    date: dt.date
    receipt_number: str
    description: str
    kind: str
    amount_paise: int
    tip_paise: int
    refunded_paise: int

    @property
    def net_paise(self) -> int:
        return max(0, self.amount_paise + self.tip_paise - self.refunded_paise)


@dataclass
class MonthlySummary:
    """Everything on one worker's statement for one month."""

    worker_name: str
    society_name: str
    year: int
    month: int
    lines: list[SummaryLine] = field(default_factory=list)

    @property
    def month_name(self) -> str:
        return f"{calendar.month_name[self.month]} {self.year}"

    @property
    def total_paise(self) -> int:
        return sum(line.net_paise for line in self.lines)

    @property
    def tips_paise(self) -> int:
        return sum(line.tip_paise for line in self.lines)

    @property
    def refunded_paise(self) -> int:
        return sum(line.refunded_paise for line in self.lines)

    @property
    def payment_count(self) -> int:
        return len(self.lines)

    def as_dict(self) -> dict:
        """The API shape. Amounts appear in both paise and rupee strings.

        Both, deliberately: the app does arithmetic and comparisons on the
        integer, and displays the string — deriving the string client-side would
        eventually produce a receipt and an app screen that disagree.
        """
        return {
            "worker_name": self.worker_name,
            "society_name": self.society_name,
            "year": self.year,
            "month": self.month,
            "month_name": self.month_name,
            "payment_count": self.payment_count,
            "total_paise": self.total_paise,
            "total_display": format_paise(self.total_paise),
            "tips_paise": self.tips_paise,
            "tips_display": format_paise(self.tips_paise),
            "refunded_paise": self.refunded_paise,
            "refunded_display": format_paise(self.refunded_paise),
            "lines": [
                {
                    "payment_id": line.payment_id,
                    "date": line.date,
                    "receipt_number": line.receipt_number,
                    "description": line.description,
                    "kind": line.kind,
                    "amount_paise": line.amount_paise,
                    "tip_paise": line.tip_paise,
                    "refunded_paise": line.refunded_paise,
                    "net_paise": line.net_paise,
                    "net_display": format_paise(line.net_paise),
                }
                for line in self.lines
            ],
        }


def _describe(payment: Payment) -> str:
    if payment.kind == PaymentKind.ENGAGEMENT_SALARY and payment.period_start:
        return f"Salary, {payment.period_start:%d %b} – {payment.period_end:%d %b}"
    if payment.kind == PaymentKind.BOOKING and payment.booking_id:
        category = getattr(payment.booking, "category", None)
        return f"One-day booking: {category.name}" if category else "One-day booking"
    return payment.get_kind_display()


def build_monthly_summary(worker, *, year: int, month: int) -> MonthlySummary:
    """Assemble one worker's statement for a month.

    Keyed on settlement date, not order date: a statement is about money that
    actually arrived that month.
    """
    start, end = month_bounds(year, month)

    payments = (
        Payment.objects.for_period(start, end)
        .filter(worker=worker)
        .select_related("booking__category", "engagement")
        .order_by("paid_at")
    )

    return MonthlySummary(
        worker_name=worker.user.get_full_name() or worker.user.phone_number,
        society_name=str(worker.user.society) if worker.user.society else "",
        year=year,
        month=month,
        lines=[
            SummaryLine(
                payment_id=str(payment.pk),
                date=payment.paid_at.date(),
                receipt_number=payment.receipt_number,
                description=_describe(payment),
                kind=payment.get_kind_display(),
                amount_paise=payment.amount_paise,
                tip_paise=payment.tip_paise,
                refunded_paise=payment.refunded_paise,
            )
            for payment in payments
        ],
    )


def render_csv(summary: MonthlySummary) -> str:
    """The spreadsheet form."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["Sathify — salary summary"])
    writer.writerow(["Worker", summary.worker_name])
    writer.writerow(["Society", summary.society_name])
    writer.writerow(["Month", summary.month_name])
    writer.writerow([])
    writer.writerow(["Date", "Receipt", "Description", "Amount", "Tip", "Refunded", "Net"])

    for line in summary.lines:
        writer.writerow(
            [
                line.date.isoformat(),
                line.receipt_number,
                line.description,
                format_paise(line.amount_paise),
                format_paise(line.tip_paise),
                format_paise(line.refunded_paise),
                format_paise(line.net_paise),
            ]
        )

    writer.writerow([])
    writer.writerow(["Total", "", "", "", "", "", format_paise(summary.total_paise)])
    return buffer.getvalue()


def render_pdf(summary: MonthlySummary) -> bytes:
    """The printable form.

    Plain and dense on purpose. This gets printed on a shared office printer and
    handed over as proof of income, so legibility beats design, and the total is
    the largest thing on the page because it is what anyone reading it is
    looking for.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 25 * mm

    page.setFont("Helvetica-Bold", 18)
    page.drawString(20 * mm, y, "Sathify")
    page.setFont("Helvetica", 11)
    page.drawRightString(width - 20 * mm, y, "Salary summary")

    y -= 12 * mm
    page.setFont("Helvetica-Bold", 13)
    page.drawString(20 * mm, y, summary.worker_name)
    y -= 6 * mm
    page.setFont("Helvetica", 10)
    if summary.society_name:
        page.drawString(20 * mm, y, summary.society_name)
        y -= 5 * mm
    page.drawString(20 * mm, y, summary.month_name)

    y -= 10 * mm
    page.line(20 * mm, y, width - 20 * mm, y)
    y -= 7 * mm

    page.setFont("Helvetica-Bold", 9)
    page.drawString(20 * mm, y, "Date")
    page.drawString(42 * mm, y, "Receipt")
    page.drawString(85 * mm, y, "Description")
    page.drawRightString(width - 20 * mm, y, "Amount")
    y -= 5 * mm

    page.setFont("Helvetica", 9)
    for line in summary.lines:
        if y < 35 * mm:
            page.showPage()
            y = height - 25 * mm
            page.setFont("Helvetica", 9)

        page.drawString(20 * mm, y, line.date.strftime("%d %b"))
        page.drawString(42 * mm, y, line.receipt_number)
        page.drawString(85 * mm, y, line.description[:45])
        page.drawRightString(width - 20 * mm, y, format_paise(line.net_paise))
        y -= 5.5 * mm

    if not summary.lines:
        page.drawString(20 * mm, y, "No payments were settled in this month.")
        y -= 6 * mm

    y -= 4 * mm
    page.line(20 * mm, y, width - 20 * mm, y)
    y -= 9 * mm

    page.setFont("Helvetica-Bold", 14)
    page.drawString(20 * mm, y, "Total received")
    page.drawRightString(width - 20 * mm, y, format_paise(summary.total_paise))

    if summary.tips_paise:
        y -= 6 * mm
        page.setFont("Helvetica", 9)
        page.drawRightString(
            width - 20 * mm, y, f"includes {format_paise(summary.tips_paise)} in tips"
        )

    page.setFont("Helvetica", 8)
    page.drawString(
        20 * mm,
        18 * mm,
        "Generated by Sathify. Payments are processed by Razorpay; Sathify does "
        "not hold card or bank details.",
    )

    page.showPage()
    page.save()
    return buffer.getvalue()


def receipt_dict(payment: Payment) -> dict:
    """A single transaction's receipt, issued to both parties (Module 8.3)."""
    return {
        "receipt_number": payment.receipt_number,
        "status": payment.status,
        "kind": payment.get_kind_display(),
        "description": _describe(payment),
        "paid_at": payment.paid_at,
        "worker_name": payment.worker.user.get_full_name(),
        "resident_name": payment.resident.user.get_full_name(),
        "flat": str(payment.resident.flat),
        "amount_paise": payment.amount_paise,
        "amount_display": format_paise(payment.amount_paise),
        "tip_paise": payment.tip_paise,
        "tip_display": format_paise(payment.tip_paise),
        "total_paise": payment.total_paise,
        "total_display": format_paise(payment.total_paise),
        "refunded_paise": payment.refunded_paise,
        "net_paise": payment.net_paise,
        "net_display": format_paise(payment.net_paise),
        # Not the signature or any gateway secret — just enough for a support
        # conversation with Razorpay.
        "gateway_payment_id": payment.razorpay_payment_id,
    }


__all__ = [
    "MonthlySummary",
    "SummaryLine",
    "build_monthly_summary",
    "month_bounds",
    "receipt_dict",
    "render_csv",
    "render_pdf",
]
