"""
The nightly pass over work sessions: close what nobody stopped, record absences.

Run once a day, after the last plausible visit. Two passes rather than one,
because they make different claims about the same missing tap:

* **Auto-close** says "she was here and nobody stopped the clock." She is paid
  her scheduled hours, and the session is flagged so a person confirms it.
* **No-show** says "she was not here at all." Nothing is owed.

Conflating them would let a capture failure — a dead phone, a geofence that did
not fire, a guard's tablet offline all morning — be recorded as an absence, and
absences cost her a day's pay. So a session that exists is always closed rather
than voided, and only a scheduled visit with *no session and no leave* is ever
written down as a no-show.

Idempotent by construction: closing skips anything not OPEN, pricing skips
anything already priced, and the no-show pass skips any day that already has a
session. Running it twice, or re-running it after a crash, changes nothing.
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.payments.invoicing import close_stale_sessions, mark_no_shows
from apps.societies.models import Society


class Command(BaseCommand):
    help = "Close work sessions nobody stopped, and record scheduled visits that never happened."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            type=int,
            default=None,
            help="Restrict to one society id. Default: every society.",
        )
        parser.add_argument(
            "--day",
            type=str,
            default=None,
            help="Which day to check for no-shows (YYYY-MM-DD). Default: yesterday.",
        )
        parser.add_argument(
            "--skip-no-shows",
            action="store_true",
            help="Only auto-close. Useful while a society's capture rate is still settling.",
        )

    def handle(self, *args, **options):
        society = None
        if options["society"]:
            society = Society.objects.filter(pk=options["society"]).first()
            if society is None:
                self.stderr.write(self.style.ERROR(f"No society {options['society']}."))
                return

        if options["day"]:
            day = dt.date.fromisoformat(options["day"])
        else:
            # Yesterday, not today: a visit scheduled for this evening has not
            # had its chance yet, and marking it absent at 2am would be a lie
            # that costs somebody a day's pay.
            day = timezone.localdate() - dt.timedelta(days=1)

        closed = close_stale_sessions(society=society)
        self.stdout.write(
            self.style.SUCCESS(f"Auto-closed {closed} session(s) at their scheduled departure.")
        )

        if options["skip_no_shows"]:
            self.stdout.write("Skipped the no-show pass.")
            return

        absences = mark_no_shows(day=day, society=society)
        self.stdout.write(
            self.style.SUCCESS(f"Recorded {absences} no-show(s) for {day}, all flagged for review.")
        )
