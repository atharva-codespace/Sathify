"""
Seed one hourly engagement, the visits it produced, and the bill they add up to.

Module 7.7 and 8.10's screens have nothing to show until an hourly engagement
has actually run for a few days: a WorkSession exists only once somebody works,
and an Invoice only once those sessions are accrued onto one. Neither is
reachable over HTTP -- the API can start and stop a session, but nothing in it
opens an hourly engagement or issues a bill -- so demonstrating those screens
means manufacturing the history here.

Additive by construction:

* It never flips an existing monthly engagement to hourly. Those terms are
  something two people agreed to, and rewriting them to make a demo convenient
  would be the most dishonest thing this command could do. When no hourly
  engagement exists it creates a separate one.
* Every step is idempotent -- sessions key on (engagement, visit_date), pricing
  on ``priced_at``, accrual on the existing line -- so a second run adds nothing
  the first did not.

Today is deliberately skipped, so the seeded history leaves no OPEN session for
the nightly close to reconcile afterwards.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.attendance.models import SessionSource, SessionStatus, WorkSession
from apps.hiring.models import Engagement, EngagementStatus, RateBasis
from apps.payments.invoicing import accrue_session, close_period, issue_after_review
from apps.societies.models import Resident
from apps.workers.models import WorkerProfile


class Command(BaseCommand):
    help = (
        "Create an hourly engagement with priced work sessions and the invoice "
        "they accrue onto, so Module 7.7/8.10 screens have something to show."
    )

    def add_arguments(self, parser):
        parser.add_argument("--resident", default="9800000002", help="Resident's phone number.")
        parser.add_argument("--worker", default="9800000003", help="Worker's phone number.")
        parser.add_argument("--visits", type=int, default=8, help="How many past weekdays to fill.")
        parser.add_argument("--hourly-rate", type=int, default=120, help="INR per hour.")
        parser.add_argument("--visit-fee", type=int, default=40, help="INR per visit.")
        parser.add_argument(
            "--hold-review",
            action="store_true",
            help="Leave the invoice in its review window instead of issuing it.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        resident = self._resident(options["resident"])
        worker = self._worker(options["worker"])

        engagement, created = self._hourly_engagement(
            resident, worker, options["hourly_rate"], options["visit_fee"]
        )
        self.stdout.write(
            "{} hourly engagement #{} -- {} for {}, {}/visit + {}/hr".format(
                "created" if created else "reusing",
                engagement.pk,
                worker.user.get_full_name(),
                resident.user.get_full_name(),
                engagement.visit_fee,
                engagement.hourly_rate,
            )
        )

        days = self._recent_weekdays(options["visits"])
        if not days:
            raise CommandError("No past weekdays to seed.")

        sessions = [self._session(engagement, worker, day) for day in days]

        # The bill covers the calendar month the visits fall in, so a re-run
        # accrues onto the same draft rather than opening a second one.
        period_start = days[0].replace(day=1)
        period_end = self._month_end(days[-1])

        invoice = None
        for session in sessions:
            invoice = accrue_session(
                session, period_start=period_start, period_end=period_end
            ) or invoice

        if invoice is None:
            raise CommandError("Nothing accrued -- is the engagement really hourly?")

        invoice.refresh_from_db()
        self.stdout.write(
            "invoice {}: {} visits, {} lines, status={}".format(
                invoice.number, len(sessions), invoice.lines.count(), invoice.status
            )
        )

        if not options["hold_review"]:
            close_period(invoice, hours=0)
            payment = issue_after_review(invoice)
            invoice.refresh_from_db()
            self.stdout.write(
                "issued -> status={}{}".format(
                    invoice.status,
                    ", payment #{}".format(payment.pk) if payment else " (no payment raised)",
                )
            )

        self.stdout.write(self.style.SUCCESS("Done."))

    # -- lookups -------------------------------------------------------------

    def _resident(self, phone):
        resident = (
            Resident.objects.filter(user__phone_number=phone)
            .select_related("user", "user__society", "flat")
            .first()
        )
        if resident is None:
            raise CommandError("No resident with phone {}.".format(phone))
        return resident

    def _worker(self, phone):
        worker = (
            WorkerProfile.objects.filter(user__phone_number=phone)
            .select_related("user")
            .first()
        )
        if worker is None:
            raise CommandError("No worker with phone {}.".format(phone))
        return worker

    # -- the engagement ------------------------------------------------------

    def _hourly_engagement(self, resident, worker, hourly_rate, visit_fee):
        existing = Engagement.objects.filter(
            resident=resident,
            worker=worker,
            rate_basis=RateBasis.HOURLY,
            status=EngagementStatus.ACTIVE,
        ).first()
        if existing is not None:
            return existing, False

        service_type = worker.service_types.first()
        if service_type is None:
            raise CommandError(
                "{} offers no service type.".format(worker.user.get_full_name())
            )

        engagement = Engagement.objects.create(
            # Resident carries no society of its own; it is scoped through the
            # account, which is also how the API scopes every query.
            society=resident.user.society,
            resident=resident,
            worker=worker,
            service_type=service_type,
            status=EngagementStatus.ACTIVE,
            started_on=timezone.localdate() - dt.timedelta(days=30),
            days_of_week=[0, 1, 2, 3, 4],
            start_time=dt.time(9, 0),
            expected_duration_minutes=90,
            monthly_rate=0,  # zero is the honest figure on hourly terms
            rate_basis=RateBasis.HOURLY,
            hourly_rate=hourly_rate,
            visit_fee=visit_fee,
        )
        return engagement, True

    # -- the visits ----------------------------------------------------------

    def _recent_weekdays(self, count):
        """The ``count`` most recent weekdays before today, oldest first."""
        days = []
        day = timezone.localdate() - dt.timedelta(days=1)
        while len(days) < count:
            if day.weekday() < 5:
                days.append(day)
            day -= dt.timedelta(days=1)
        return sorted(days)

    def _session(self, engagement, worker, day):
        existing = WorkSession.objects.filter(
            engagement=engagement, visit_date=day
        ).first()
        if existing is not None:
            return existing

        # A little variety in the finish time, so the bill is not eight
        # identical rows: a demo where every visit runs exactly to schedule
        # cannot show rounding, overtime, or a short visit.
        minutes = (85, 90, 95, 120, 75)[day.day % 5]
        start = timezone.make_aware(
            dt.datetime.combine(day, dt.time(9, 0)), timezone.get_current_timezone()
        )
        return WorkSession.objects.create(
            society=engagement.society,
            engagement=engagement,
            worker=worker,
            visit_date=day,
            started_at=start,
            ended_at=start + dt.timedelta(minutes=minutes),
            source=SessionSource.SELF,
            status=SessionStatus.CLOSED,
            opened_by=worker.user,
            closed_by=worker.user,
        )

    @staticmethod
    def _month_end(day):
        first_next = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return first_next - dt.timedelta(days=1)
