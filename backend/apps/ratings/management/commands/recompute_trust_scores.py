"""
Module 9.3 — the sweep that stands in for the scheduled scoring job.

Trust scores are recomputed at the moments that obviously change them: a rating
landing, a flag being resolved. But several inputs move with no event of their
own — attendance accrues, payments settle, jobs complete — so a periodic sweep
is what keeps a score from drifting away from the evidence behind it.

The modspec calls for a scheduled job. There is no scheduler on this project's
free tier, so this is a management command instead: run it from cron, from a
Render job, or by hand. Whatever eventually runs it calls the same services the
event-driven path does, so wiring up a real scheduler later changes what invokes
this, not what it does.

    python manage.py recompute_trust_scores
    python manage.py recompute_trust_scores --society 3 --workers-only
    python manage.py recompute_trust_scores --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.societies.models import Resident
from apps.workers.models import WorkerProfile

from ...services import recompute_resident_trust, recompute_worker_trust


class Command(BaseCommand):
    help = "Recompute every worker's and resident's trust score (Module 9.3)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            type=int,
            help="Limit to one society. Useful when investigating a dispute.",
        )
        parser.add_argument(
            "--workers-only", action="store_true", help="Skip residents."
        )
        parser.add_argument(
            "--residents-only", action="store_true", help="Skip workers."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        society_id = options.get("society")
        dry_run = options["dry_run"]
        trigger = "scheduled sweep"

        do_workers = not options["residents_only"]
        do_residents = not options["workers_only"]

        changed = 0

        if do_workers:
            workers = WorkerProfile.objects.select_related("user", "user__society")
            if society_id:
                workers = workers.filter(user__society_id=society_id)

            for worker in workers:
                before = worker.trust_score
                if dry_run:
                    # Compute without persisting, so a sweep can be inspected
                    # before it is trusted to rewrite everyone's score.
                    from ...services import worker_trust_inputs
                    from ...trust import worker_trust

                    after = worker_trust(worker_trust_inputs(worker)).value
                else:
                    with transaction.atomic():
                        after = recompute_worker_trust(worker, trigger=trigger).value

                if float(before) != float(after):
                    changed += 1
                    self.stdout.write(f"  worker {worker.pk}: {before} → {after}")

        if do_residents:
            residents = Resident.objects.select_related(
                "user", "flat__tower__society"
            )
            if society_id:
                residents = residents.filter(flat__tower__society_id=society_id)

            for resident in residents:
                before = resident.trust_score
                if dry_run:
                    from ...services import resident_trust_inputs
                    from ...trust import resident_trust

                    after = resident_trust(resident_trust_inputs(resident)).value
                else:
                    with transaction.atomic():
                        after = recompute_resident_trust(resident, trigger=trigger).value

                if float(before) != float(after):
                    changed += 1
                    self.stdout.write(f"  resident {resident.pk}: {before} → {after}")

        verb = "would change" if dry_run else "changed"
        self.stdout.write(
            self.style.SUCCESS(f"{changed} trust score(s) {verb}.")
        )
