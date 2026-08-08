"""
Create a payment that is due *right now*, for re-testing the Razorpay flow.

Module 8.1's checkout is the one path in this codebase that cannot be exercised
from a unit test end to end — it needs a real order id from Razorpay and a real
signed response from Checkout. So it gets tested by hand, repeatedly, and this
command is what makes that cheap: every run produces a payment whose due date is
the moment the command ran.

Two rules it follows, both because the alternative produces a test that passes
while the real thing is broken:

* **It goes through the real code path.** ``create_payment`` and ``open_order``,
  the same two functions the booking endpoint calls. Hand-inserting a row would
  exercise the database and nothing else — not the fee calculation, not the due
  date derivation, not the receipt number, not the order creation.
* **It reuses its fixtures.** Everything is ``get_or_create`` on fixed
  identifiers, so running it twenty times leaves one sample resident and one
  sample worker rather than twenty of each cluttering a shared database.

It refuses to run against live Razorpay keys. See :func:`_assert_test_mode`.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


def _rupees(paise: int) -> str:
    """Money, in ASCII.

    Deliberately not ``format_paise``: that emits U+20B9 (₹), which is correct
    for the app and the API, and which a Windows console running cp1252 cannot
    encode — the command dies with a UnicodeEncodeError halfway through printing
    a payment it has already created. A CLI's output has to survive whatever
    terminal it lands in.
    """
    rupees, remainder = divmod(int(paise), 100)
    return f"INR {rupees}.{remainder:02d}"


#: Fixed identities, so repeated runs converge rather than accumulate. The
#: numbers are in a reserved-looking range and prefixed in the name, so nobody
#: mistakes these rows for a real household.
SAMPLE_RESIDENT_PHONE = "9000000001"
SAMPLE_WORKER_PHONE = "9000000002"
SAMPLE_FLAT_NUMBER = "SAMPLE-001"
SAMPLE_TOWER_NAME = "Sample Tower"


class Command(BaseCommand):
    help = "Create a sample payment due right now, for re-testing Razorpay checkout."

    def add_arguments(self, parser):
        parser.add_argument(
            "--society",
            type=int,
            default=None,
            help="Society id to attach the sample to. Defaults to the first active one.",
        )
        parser.add_argument(
            "--amount",
            type=int,
            default=500,
            help="Amount in rupees (default 500).",
        )
        parser.add_argument(
            "--tip",
            type=int,
            default=0,
            help="Tip in rupees, to exercise the tip path (default 0).",
        )
        parser.add_argument(
            "--order",
            action="store_true",
            help="Also open a Razorpay order and print the checkout payload.",
        )

    # -- guard rails ---------------------------------------------------------

    def _assert_test_mode(self) -> None:
        """Refuse to touch live Razorpay. Loudly.

        This command creates a payable charge. Pointed at a live key it would be
        asking a real card for real money, on a schedule somebody set up to run
        repeatedly. That is worth failing hard over rather than warning about.
        """
        config = getattr(settings, "RAZORPAY_SETTINGS", {})

        if not config.get("TEST_MODE", True):
            raise CommandError(
                "RAZORPAY_TEST_MODE is off. This command creates a payable "
                "charge and will not run against live keys. Set "
                "RAZORPAY_TEST_MODE=True."
            )

        key_id = str(config.get("KEY_ID", ""))
        if key_id.startswith("rzp_live_"):
            raise CommandError(
                f"A live Razorpay key ({key_id[:12]}...) is configured. Refusing "
                "to create a sample payment against it."
            )

    # -- fixtures ------------------------------------------------------------

    def _society(self, society_id):
        from apps.societies.models import Society, SocietyStatus

        if society_id is not None:
            society = Society.objects.filter(pk=society_id).first()
            if society is None:
                raise CommandError(f"No society with id {society_id}.")
            return society

        society = Society.objects.filter(status=SocietyStatus.ACTIVE).first()
        if society is None:
            raise CommandError(
                "No active society exists. Create one, or pass --society."
            )
        return society

    def _resident(self, society):
        from apps.accounts.models import Role, User
        from apps.societies.models import Flat, Resident, Tower

        user, _ = User.objects.get_or_create(
            phone_number=SAMPLE_RESIDENT_PHONE,
            defaults={
                "role": Role.RESIDENT,
                "first_name": "Sample",
                "last_name": "Resident",
                "society": society,
                "is_approved": True,
            },
        )
        tower, _ = Tower.objects.get_or_create(
            society=society, name=SAMPLE_TOWER_NAME, defaults={"floors": 1}
        )
        flat, _ = Flat.objects.get_or_create(
            tower=tower, number=SAMPLE_FLAT_NUMBER, defaults={"floor": 1}
        )
        resident, _ = Resident.objects.get_or_create(
            user=user, defaults={"flat": flat, "is_primary": True}
        )
        return resident

    def _worker(self, society):
        from apps.accounts.models import Role, User
        from apps.workers.models import ServiceType, WorkerProfile

        user, _ = User.objects.get_or_create(
            phone_number=SAMPLE_WORKER_PHONE,
            defaults={
                "role": Role.WORKER,
                "first_name": "Sample",
                "last_name": "Worker",
                "society": society,
                "is_approved": True,
            },
        )
        worker, _ = WorkerProfile.objects.get_or_create(
            user=user, defaults={"photo": "workers/photos/sample.jpg"}
        )
        service, _ = ServiceType.objects.get_or_create(
            slug="maid", defaults={"name": "Maid"}
        )
        worker.service_types.add(service)
        return worker, service

    def _engagement(self, society, resident, worker, service, amount):
        from apps.hiring.models import Engagement, EngagementStatus

        engagement, _ = Engagement.objects.get_or_create(
            society=society,
            resident=resident,
            worker=worker,
            service_type=service,
            defaults={
                "days_of_week": [0, 1, 2, 3, 4, 5],
                "start_time": timezone.localtime().time().replace(microsecond=0),
                "expected_duration_minutes": 90,
                "monthly_rate": amount,
                "status": EngagementStatus.ACTIVE,
            },
        )
        return engagement

    # -- main ----------------------------------------------------------------

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.payments.models import PaymentKind
        from apps.payments.services import create_payment, open_order

        self._assert_test_mode()

        society = self._society(options["society"])
        resident = self._resident(society)
        worker, service = self._worker(society)
        engagement = self._engagement(
            society, resident, worker, service, options["amount"]
        )

        # The whole point: due at the exact moment this ran, so every run gives
        # a payment that is payable immediately and never one dated yesterday.
        due_at = timezone.now()

        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=options["amount"] * 100,
            tip_paise=options["tip"] * 100,
            engagement=engagement,
            due_at=due_at,
            note="Sample payment from manage.py sample_payment",
        )

        self.stdout.write(self.style.SUCCESS("Sample payment created."))
        self.stdout.write(f"  society     {society.pk}  {society.name}")
        self.stdout.write(f"  resident    {resident.pk}  {resident.user.get_full_name()}")
        self.stdout.write(f"  worker      {worker.pk}  {worker.user.get_full_name()}")
        self.stdout.write(f"  engagement  {engagement.pk}")
        self.stdout.write(f"  payment     {payment.pk}")
        self.stdout.write(f"  receipt     {payment.receipt_number}")
        self.stdout.write(f"  amount      {_rupees(payment.amount_paise)}")
        if payment.tip_paise:
            self.stdout.write(f"  tip         {_rupees(payment.tip_paise)}")
        if payment.platform_fee_paise:
            self.stdout.write(
                f"  fee         {_rupees(payment.platform_fee_paise)}"
            )
        self.stdout.write(f"  total       {_rupees(payment.total_paise)}")
        self.stdout.write(
            self.style.WARNING(f"  due at      {payment.due_at:%Y-%m-%d %H:%M:%S %Z}")
        )

        if not options["order"]:
            self.stdout.write(
                "\nRe-run with --order to also open a Razorpay order."
            )
            return

        try:
            checkout = open_order(payment)
        except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
            raise CommandError(
                f"Payment {payment.pk} was created, but opening a Razorpay "
                f"order failed: {exc}"
            ) from exc

        self.stdout.write(self.style.SUCCESS("\nRazorpay order opened."))
        for key in ("order_id", "amount", "currency", "key_id"):
            if key in checkout:
                self.stdout.write(f"  {key:<11} {checkout[key]}")
