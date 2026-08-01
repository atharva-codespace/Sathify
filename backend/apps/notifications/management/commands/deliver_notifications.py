"""
Module 10 — the delivery sweep.

Turns Module 6.4's due reminders into notifications and retries anything push
never reached. There is no scheduler on this project's free tier, so this is a
management command: run it from cron, a Render job, or an external pinger.

The same work is available at ``POST /api/v1/notifications/deliver-due/`` for a
pinger that can only make HTTP calls.

    python manage.py deliver_notifications
    python manage.py deliver_notifications --society 3
    python manage.py deliver_notifications --no-retry
"""

from django.core.management.base import BaseCommand

from ...services import deliver_due_reminders, retry_failed_deliveries


class Command(BaseCommand):
    help = "Deliver due reminders and retry failed sends (Module 10)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society", type=int, help="Limit reminder delivery to one society."
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Cap per run, so one invocation cannot run for hours.",
        )
        parser.add_argument(
            "--no-retry",
            action="store_true",
            help="Skip the SMS retry pass.",
        )

    def handle(self, *args, **options):
        delivered = deliver_due_reminders(
            society_id=options.get("society"), limit=options["limit"]
        )
        self.stdout.write(f"Reminders delivered: {delivered}")

        if not options["no_retry"]:
            retried = retry_failed_deliveries(limit=options["limit"])
            self.stdout.write(f"Deliveries retried: {retried}")

        self.stdout.write(self.style.SUCCESS("Done."))
