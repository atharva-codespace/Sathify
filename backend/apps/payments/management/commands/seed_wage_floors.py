"""
Load the statutory minimum wage figures the hourly engine checks against.

-------------------------------------------------------------------------------
THESE NUMBERS ARE A STARTING POINT, NOT AN AUTHORITY
-------------------------------------------------------------------------------
Minimum wages for domestic and unskilled work are set per state, revised on
their own schedules, and published as monthly or daily figures that have to be
divided down to an hour. The values below are indicative and carry the note that
says so, because a floor nobody can trace back to an order is a floor nobody can
defend when it is challenged.

``--dry-run`` prints what would change without writing, which is the mode to use
when checking a revision against what is already loaded.

    python manage.py seed_wage_floors
    python manage.py seed_wage_floors --dry-run
    python manage.py seed_wage_floors --state Maharashtra --paise 11000 \\
        --from 2026-04-01 --source "Notification XYZ of 2026"
"""

from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand, CommandError

from apps.payments.models import WageFloor, format_paise

#: (state, paise per hour, in force from, provenance).
#:
#: Deliberately sparse. A state with no row is one the engine reports as
#: unchecked rather than one it silently treats as having no minimum — see
#: `wage_floor.assert_compliant`.
SEED = [
    ("Maharashtra", 11_000, dt.date(2026, 1, 1), "Indicative — replace with the current notification"),
    ("Karnataka", 10_500, dt.date(2026, 1, 1), "Indicative — replace with the current notification"),
    ("Delhi", 13_500, dt.date(2026, 1, 1), "Indicative — replace with the current notification"),
    ("Tamil Nadu", 9_500, dt.date(2026, 1, 1), "Indicative — replace with the current notification"),
    ("Telangana", 9_000, dt.date(2026, 1, 1), "Indicative — replace with the current notification"),
]


class Command(BaseCommand):
    help = "Load or update statutory minimum hourly wages by state (Module 8.11)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--state", help="Load a single state instead of the seed set.")
        parser.add_argument("--paise", type=int, help="Minimum per hour, in paise.")
        parser.add_argument("--from", dest="effective_from", help="YYYY-MM-DD.")
        parser.add_argument("--source", default="", help="Which order this came from.")

    def handle(self, *args, **options):
        if options["state"]:
            if not options["paise"] or not options["effective_from"]:
                raise CommandError("--state needs --paise and --from.")
            rows = [(
                options["state"],
                options["paise"],
                dt.date.fromisoformat(options["effective_from"]),
                options["source"],
            )]
        else:
            rows = SEED

        created = updated = unchanged = 0
        for state, paise, effective_from, source in rows:
            existing = WageFloor.objects.filter(
                state=state, effective_from=effective_from
            ).first()

            if existing and existing.min_hourly_paise == paise:
                unchanged += 1
                continue

            verb = "update" if existing else "create"
            self.stdout.write(
                f"  {verb}: {state} {format_paise(paise)}/hr from {effective_from}"
            )
            if options["dry_run"]:
                continue

            # A revision is a *new row*, never an edit of the old one: an
            # invoice raised last year was checked against last year's floor,
            # and rewriting that figure would make the historical check
            # unreproducible.
            WageFloor.objects.update_or_create(
                state=state,
                effective_from=effective_from,
                defaults={"min_hourly_paise": paise, "source_note": source},
            )
            if existing:
                updated += 1
            else:
                created += 1

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — nothing written."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"{created} created, {updated} updated, {unchanged} already current."
            )
        )
        self.stdout.write(
            "These figures are indicative. Replace them with the current "
            "notification for each state before relying on the check."
        )
