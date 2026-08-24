"""
Module 11.5 — drain the cross-society report queue.

The third of the three triggers ``docs/free-tier-constraints.md`` §7 prescribes,
alongside the drain-on-read in the console's report list and the pinger endpoint
at ``/api/v1/console/reports/run/``. All three call the same bounded, idempotent
sweep, so whichever happens to fire first simply does some of the work and the
others finish it.

    python manage.py process_report_jobs
    python manage.py process_report_jobs --limit 50
    python manage.py process_report_jobs --until-done
    python manage.py process_report_jobs --prune

``--until-done`` exists for a real scheduler or a one-off Render job, where
there is no request waiting and no reason to stay bounded. It is deliberately
*not* the default: the same command run from the web service must never hold the
single free-tier worker for the length of a 128-society build.
"""

from django.core.management.base import BaseCommand

from ...report_jobs import DEFAULT_SWEEP_LIMIT, prune_expired, run_pending_jobs


class Command(BaseCommand):
    help = "Build any queued cross-society reports (Module 11.5)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_SWEEP_LIMIT,
            help=f"Societies to build per pass. Default {DEFAULT_SWEEP_LIMIT}.",
        )
        parser.add_argument(
            "--until-done",
            action="store_true",
            help="Keep sweeping until nothing is left. For a real scheduler only.",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Also delete files behind expired jobs. The rows are kept.",
        )

    def handle(self, *args, **options):
        totals = {"built": 0, "failed": 0, "finished": 0}

        while True:
            result = run_pending_jobs(limit=options["limit"])
            for key in totals:
                totals[key] += result[key]

            # Nothing moved, so another pass would do nothing either. This is
            # also what stops `--until-done` spinning on a job whose every
            # remaining society has exhausted its attempts.
            if not options["until_done"] or not any(result.values()):
                break

        self.stdout.write(
            self.style.SUCCESS(
                f"Built {totals['built']} society slice(s), "
                f"{totals['failed']} failed, "
                f"{totals['finished']} job(s) finished."
            )
        )

        if options["prune"]:
            cleared = prune_expired()
            self.stdout.write(
                self.style.SUCCESS(f"Cleared files for {cleared} expired job(s).")
            )
