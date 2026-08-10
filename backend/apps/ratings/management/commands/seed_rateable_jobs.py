"""Give the demo accounts something to rate (Module 9).

    python manage.py seed_demo            # accounts, profiles, approvals
    python manage.py seed_rateable_jobs   # work for those accounts to rate

Module 9 only ever offers work that has actually finished: a booking has to be
COMPLETED and an engagement TERMINATED before either side may rate it. Nothing
in ``seed_demo`` creates either, so a freshly seeded system shows an empty Rate
Work screen on both sides — correct, and completely untestable. Reaching a
finished job through the API means booking, accepting, waiting for the date to
pass, marking complete, paying, then hiring, then ending, in that order.

This creates one completed booking and one ended engagement per resident/worker
pair, which gives *both* sides two rateable jobs each — a rating runs in both
directions and testing only one of them is how the other one stays broken.

Idempotent: seeded rows carry a marker note and are matched on it, so re-running
adds nothing and nothing that was not created here is ever touched or ended.

    --reset   delete the ratings on these jobs so they can be rated again, and
              recompute the trust scores those ratings had moved.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.hiring.models import Engagement, EngagementEndReason, EngagementStatus
from apps.ratings.models import Rating
from apps.ratings.services import recompute_resident_trust, recompute_worker_trust
from apps.societies.models import Resident, Society
from apps.workers.models import ServiceType, WorkerProfile

#: Stamped on every row this command creates, and the only thing it will touch
#: on a later run. Without it, "make an engagement for this pair" would happily
#: match — and then terminate — a live arrangement somebody was testing.
SEED_NOTE = "Seeded by seed_rateable_jobs"


class Command(BaseCommand):
    help = (
        "Create completed bookings and ended engagements so the rating flow "
        "has something to work on."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            type=int,
            default=None,
            help="Restrict to one society id. Defaults to every society.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Delete the ratings already given on the seeded jobs so they "
                "can be rated again, and recompute the affected trust scores."
            ),
        )

    def handle(self, *args, **options):
        # The transaction wraps the writes and nothing else. It used to wrap
        # this whole method, which meant the report at the end was inside it —
        # and a console that could not encode a character in a *summary line*
        # rolled back the entire seed. Reporting is not part of the work.
        #
        # Everything written to the console here stays ASCII for the same
        # reason: this is run from a Windows terminal in cp1252, where one
        # arrow glyph is a crash rather than a mojibake.
        societies = Society.objects.all()
        if options["society"] is not None:
            societies = societies.filter(pk=options["society"])

        if not societies.exists():
            self.stderr.write(
                self.style.ERROR(
                    "No society found. Run `python manage.py seed_demo` first."
                )
            )
            return

        created = 0
        paired = 0
        with transaction.atomic():
            if options["reset"]:
                self._reset()

            for society in societies:
                for resident, worker in self._pairs(society):
                    paired += 1
                    created += self._booking(society, resident, worker)
                    created += self._engagement(society, resident, worker)

        if paired == 0:
            self.stderr.write(
                self.style.WARNING(
                    "No resident/worker pair to work with. A society needs at "
                    "least one of each - run `python manage.py seed_demo`."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Rateable work: {paired} pair(s), {created} new job(s). "
                "Both sides of each pair now have two jobs to rate."
            )
        )
        self._report()

    # --- pairing -----------------------------------------------------------

    def _pairs(self, society):
        """One worker per resident, in order.

        Deliberately not every resident against every worker: that multiplies
        into a Rate Work screen nobody can read, and the flow being tested is
        the same whether there is one pending job or twenty.
        """
        residents = list(
            Resident.objects.filter(flat__tower__society=society).select_related(
                "user", "flat__tower"
            )
        )
        workers = list(
            WorkerProfile.objects.filter(user__society=society).select_related("user")
        )
        return list(zip(residents, workers))

    # --- the work ----------------------------------------------------------

    def _booking(self, society, resident, worker) -> int:
        """A one-day job, finished yesterday."""
        if Booking.objects.filter(
            resident=resident, worker=worker, notes=SEED_NOTE
        ).exists():
            return 0

        category = (
            ServiceCategory.objects.filter(slug="deep-cleaning").first()
            or ServiceCategory.objects.first()
        )
        if category is None:
            self.stderr.write(
                self.style.WARNING(
                    "No service category exists - skipping bookings. Run "
                    "migrations, which seed the five that ship with Module 5."
                )
            )
            return 0

        Booking.objects.create(
            society=society,
            resident=resident,
            worker=worker,
            category=category,
            # Yesterday, so it is unambiguously in the past whatever the
            # society's timezone does around midnight.
            scheduled_date=timezone.localdate() - dt.timedelta(days=1),
            start_time=dt.time(10, 0),
            quoted_price=800,
            status=BookingStatus.COMPLETED,
            completed_at=timezone.now(),
            notes=SEED_NOTE,
        )
        return 1

    def _engagement(self, society, resident, worker) -> int:
        """A standing arrangement that has since ended."""
        service_type = (
            worker.service_types.first() or ServiceType.objects.first()
        )
        if service_type is None:
            self.stderr.write(
                self.style.WARNING(
                    "No service type exists - skipping engagements. Run "
                    "`python manage.py seed_demo`, which creates them."
                )
            )
            return 0

        if Engagement.objects.filter(
            resident=resident,
            worker=worker,
            status=EngagementStatus.TERMINATED,
            end_note=SEED_NOTE,
        ).exists():
            return 0

        # Written straight to TERMINATED rather than created and then ended.
        #
        # `one_live_engagement_per_pair` covers ACTIVE and PAUSED only, so an
        # ended row never collides — whereas creating this ACTIVE first would
        # fail outright on any pair that already has a live arrangement, which
        # is exactly the database somebody is most likely to run this against.
        # Ending it afterwards is also the wrong shape: nothing here ever ran,
        # and `terminate()` is a transition, not a way to write history.
        #
        # Terminated rather than serving notice, because notice leaves an
        # engagement ACTIVE for ten more days and an active engagement is not
        # rateable — which would defeat the point of seeding it.
        Engagement.objects.create(
            society=society,
            resident=resident,
            worker=worker,
            service_type=service_type,
            days_of_week=[0, 2, 4],
            start_time=dt.time(9, 0),
            monthly_rate=4000,
            started_on=timezone.localdate() - dt.timedelta(days=60),
            status=EngagementStatus.TERMINATED,
            ended_at=timezone.now(),
            end_reason=EngagementEndReason.RESIDENT_ENDED,
            end_note=SEED_NOTE,
        )
        return 1

    # --- reset -------------------------------------------------------------

    def _reset(self):
        """Un-rate the seeded jobs, and put the scores back where they belong.

        Deleting the ratings alone would leave every subject carrying an average
        and a trust score computed from ratings that no longer exist — which is
        precisely the inconsistency the rest of Module 9 goes out of its way to
        prevent.
        """
        # One filter with an OR rather than two querysets combined with `|`:
        # the combined form joins both relations and can need a DISTINCT, which
        # `delete()` refuses.
        ratings = Rating.objects.filter(
            Q(booking__notes=SEED_NOTE) | Q(engagement__end_note=SEED_NOTE)
        )
        workers = list(
            WorkerProfile.objects.filter(pk__in=ratings.values("worker_id"))
        )
        residents = list(Resident.objects.filter(pk__in=ratings.values("resident_id")))

        removed = ratings.count()
        ratings.delete()

        for worker in workers:
            recompute_worker_trust(worker, trigger="seed reset")
        for resident in residents:
            recompute_resident_trust(resident, trigger="seed reset")

        self.stdout.write(f"Reset    : removed {removed} rating(s)")

    # --- report ------------------------------------------------------------

    def _report(self):
        bookings = Booking.objects.filter(notes=SEED_NOTE).select_related(
            "resident__user", "worker__user"
        )
        for booking in bookings:
            self.stdout.write(
                f"  booking  #{booking.pk}: "
                f"{booking.resident.user.get_full_name()} + "
                f"{booking.worker.user.get_full_name()}"
            )

        engagements = Engagement.objects.filter(end_note=SEED_NOTE).select_related(
            "resident__user", "worker__user"
        )
        for engagement in engagements:
            self.stdout.write(
                f"  ended    #{engagement.pk}: "
                f"{engagement.resident.user.get_full_name()} + "
                f"{engagement.worker.user.get_full_name()}"
            )

        self.stdout.write(
            "\nSign in as either side and open Account > Rate work. "
            "Re-run with --reset to rate them again."
        )
