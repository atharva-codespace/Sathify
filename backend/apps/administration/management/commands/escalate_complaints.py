"""
Escalate complaints that have run past their SLA window.

The free tier has no scheduler (docs/free-tier-constraints.md §2), so Module
11.3's "automated escalation" is a sweep with three triggers rather than a cron
job: an administrator opening the queue, the ``complaints/escalate/`` endpoint
an external pinger can call, and this command.

All three call the same idempotent function, so running two of them at once
escalates each complaint exactly once.
"""

from django.core.management.base import BaseCommand

from apps.administration.services import escalate_overdue


class Command(BaseCommand):
    help = "Escalate complaints past their SLA window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            type=int,
            default=None,
            help="Only this society. Omit to sweep every society.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Most complaints to escalate in one run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would escalate without changing anything.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            return self._report(options["society"])

        escalated = escalate_overdue(
            society_id=options["society"], limit=options["limit"]
        )

        if escalated:
            self.stdout.write(
                self.style.WARNING(f"Escalated {escalated} overdue complaint(s).")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Nothing was overdue."))

    def _report(self, society_id):
        from apps.administration.models import Complaint

        queryset = Complaint.objects.awaiting_escalation()
        if society_id is not None:
            queryset = queryset.filter(society_id=society_id)

        pending = list(queryset.select_related("society")[:100])
        if not pending:
            self.stdout.write(self.style.SUCCESS("Nothing would escalate."))
            return

        self.stdout.write(f"{len(pending)} complaint(s) would escalate:")
        for complaint in pending:
            self.stdout.write(
                f"  {complaint.reference}  {complaint.get_priority_display():8}  "
                f"{abs(complaint.hours_remaining):.0f} h over  "
                f"{complaint.subject[:50]}"
            )
