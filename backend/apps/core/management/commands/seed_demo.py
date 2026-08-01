"""Seed a complete, working demo society so every role can be exercised end to end.

    python manage.py seed_demo

Idempotent: re-running updates the same records rather than creating duplicates,
so it is safe to run after schema changes or a partially failed attempt. Pass
--reset to delete the demo users first and rebuild them from scratch.

This exists because the API alone cannot bootstrap a testable system: guards and
administrators are deliberately NOT self-registrable (see accounts/serializers.py),
societies start PENDING and inert, and a worker stays invisible to search until
an administrator approves them AND they carry a photo. Reproducing that by hand
through the API is a dozen calls in a specific order.
"""

from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from apps.accounts.models import Role, User
from apps.bookings.models import ServiceCategory
from apps.societies.models import (
    Flat,
    Gate,
    Resident,
    ResidentRelationship,
    Society,
    SocietyStatus,
    Tower,
)
from apps.workers.models import ServiceType, WorkerProfile

DEMO_PASSWORD = "Sathify@123"

# Every seeded account. Phone numbers satisfy the Indian-mobile validator
# (10 digits, leading 6-9) and are grouped so the role is readable at a glance.
ACCOUNTS = [
    ("9800000001", "Anita", "Deshpande", Role.SOCIETY_ADMIN),
    ("9800000002", "Rohit", "Kulkarni", Role.RESIDENT),
    ("9800000003", "Sunita", "Pawar", Role.WORKER),
    ("9800000004", "Ramesh", "Jadhav", Role.GUARD),
    ("9800000005", "Meena", "Shinde", Role.WORKER),
    ("9800000006", "Priya", "Joshi", Role.RESIDENT),
]

SERVICE_TYPES = [
    ("maid", "Maid", "Sweeping, mopping, dusting and general housekeeping", "cleaning_services"),
    ("cook", "Cook", "Daily meal preparation", "restaurant"),
    ("cleaner", "Deep cleaner", "Bathroom, kitchen and deep cleaning", "sanitizer"),
    ("nanny", "Nanny", "Child care and supervision", "child_care"),
    ("driver", "Driver", "Household driving duties", "directions_car"),
    ("gardener", "Gardener", "Plant and garden upkeep", "yard"),
]

# Wires the five categories that ship in the bookings data migration to the
# kind of worker qualified for them, which is what Module 5.3 narrows on.
CATEGORY_TO_TYPE = {
    "temporary-cooking": "cook",
    "deep-cleaning": "cleaner",
    "event-preparation": "maid",
    "house-shifting": "maid",
    "emergency-assistance": None,  # any approved worker
}


def _avatar(label: str, colour: tuple[int, int, int]) -> ContentFile:
    """A deterministic placeholder photo.

    WorkerProfile.is_searchable requires a photo — it is the reference image the
    gate face check compares against — so a worker without one never appears in
    search no matter how they are approved.
    """
    image = Image.new("RGB", (256, 256), colour)
    draw = ImageDraw.Draw(image)
    draw.ellipse((78, 48, 178, 148), fill=(255, 255, 255))
    draw.ellipse((48, 158, 208, 328), fill=(255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return ContentFile(buffer.getvalue(), name=f"{label}.jpg")


class Command(BaseCommand):
    help = "Seed a demo society with one account per role, ready to log in."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the demo users and their profiles before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            phones = [a[0] for a in ACCOUNTS]
            Resident.objects.filter(user__phone_number__in=phones).delete()
            WorkerProfile.objects.filter(user__phone_number__in=phones).delete()
            User.objects.filter(phone_number__in=phones).delete()
            self.stdout.write(self.style.WARNING("Deleted existing demo accounts."))

        society = self._society()
        towers, flats = self._towers_and_flats(society)
        self._gates(society)
        types = self._service_types()
        self._link_categories(types)
        users = self._users(society)
        self._resident_profiles(users, flats)
        self._worker_profiles(users, types, admin=users["9800000001"])
        self._adopt_orphan_admins(society)

        self._report(society, towers, flats)

    # -- pieces ------------------------------------------------------------

    def _society(self) -> Society:
        society, created = Society.objects.update_or_create(
            name="Green Valley Residency",
            pincode="411045",
            defaults={
                "registration_number": "PUN/RWA/2019/0142",
                "address_line": "Baner Road, Baner",
                "city": "Pune",
                "state": "Maharashtra",
                # Real Baner coordinates: Module 4.3 proximity scoring and the
                # Module 13.3 self check-in geofence both read these, and a
                # null island fix would put every worker 8,000 km away.
                "latitude": "18.559000",
                "longitude": "73.776600",
                "total_towers": 2,
                "total_flats": 24,
                "gate_count": 2,
                "booking_notice_hours": 12,
                "allow_resident_self_checkin": True,
            },
        )
        # activate() flips status to ACTIVE *and* approves its administrators.
        # A PENDING society leaves every admin unapproved and nothing works.
        if society.status != SocietyStatus.ACTIVE:
            society.activate()
        self.stdout.write(f"Society  : {'created' if created else 'updated'} -> {society}")
        return society

    def _towers_and_flats(self, society):
        towers, flats = [], []
        for tower_name in ("A", "B"):
            tower, _ = Tower.objects.get_or_create(
                society=society, name=tower_name, defaults={"floors": 3}
            )
            towers.append(tower)
            for floor in range(1, 4):
                for unit in range(1, 5):
                    flat, _ = Flat.objects.get_or_create(
                        tower=tower, number=f"{floor}0{unit}", defaults={"floor": floor}
                    )
                    flats.append(flat)
        self.stdout.write(f"Towers   : {len(towers)}   Flats: {len(flats)}")
        return towers, flats

    def _gates(self, society):
        for name in ("Main Gate", "Service Gate"):
            Gate.objects.get_or_create(society=society, name=name, defaults={"is_active": True})
        self.stdout.write("Gates    : 2")

    def _service_types(self) -> dict[str, ServiceType]:
        types = {}
        for slug, name, description, icon in SERVICE_TYPES:
            obj, _ = ServiceType.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "description": description,
                    "icon": icon,
                    "is_active": True,
                },
            )
            types[slug] = obj
        self.stdout.write(f"Services : {len(types)} service types")
        return types

    def _link_categories(self, types):
        linked = 0
        for slug, type_slug in CATEGORY_TO_TYPE.items():
            if type_slug is None:
                continue
            updated = ServiceCategory.objects.filter(
                slug=slug, service_type__isnull=True
            ).update(service_type=types[type_slug])
            linked += updated
        self.stdout.write(f"Bookings : linked {linked} categories to a service type")

    def _users(self, society) -> dict[str, User]:
        users = {}
        for phone, first, last, role in ACCOUNTS:
            user, created = User.objects.get_or_create(
                phone_number=phone,
                defaults={"first_name": first, "last_name": last, "role": role},
            )
            user.first_name = first
            user.last_name = last
            user.role = role
            user.society = society
            user.is_approved = True
            user.approved_at = user.approved_at or timezone.now()
            user.is_phone_verified = True
            user.preferred_language = "en"
            # Always reset, so a forgotten demo password is one re-run away.
            user.set_password(DEMO_PASSWORD)
            user.save()
            users[phone] = user
            self.stdout.write(f"  user   : {phone} {role:<14} {'created' if created else 'updated'}")
        return users

    def _resident_profiles(self, users, flats):
        # Distinct flats: one_primary_resident_per_flat is a database
        # constraint, so two primaries in the same flat would raise.
        pairs = [("9800000002", flats[0]), ("9800000006", flats[1])]
        for phone, flat in pairs:
            Resident.objects.update_or_create(
                user=users[phone],
                defaults={
                    "flat": flat,
                    "relationship": ResidentRelationship.OWNER,
                    "is_primary": True,
                    "move_in_date": timezone.now().date(),
                    "reviewed_at": timezone.now(),
                },
            )
            self.stdout.write(f"  resident: {phone} -> flat {flat}")

    def _worker_profiles(self, users, types, admin):
        spec = [
            ("9800000003", ["maid", "cook"], 6, 9000, "Hindi, Marathi", (76, 175, 129)),
            ("9800000005", ["cleaner", "nanny"], 3, 7500, "Marathi, English", (91, 134, 229)),
        ]
        for phone, type_slugs, years, rate, languages, colour in spec:
            user = users[phone]
            profile, _ = WorkerProfile.objects.update_or_create(
                user=user,
                defaults={
                    "years_of_experience": years,
                    "expected_monthly_rate": rate,
                    "languages_spoken": languages,
                    "bio": f"{user.first_name} has {years} years of experience in this society's area.",
                    "is_available": True,
                    "reviewed_at": timezone.now(),
                    "reviewed_by": admin,
                },
            )
            profile.service_types.set([types[s] for s in type_slugs])
            if not profile.photo:
                profile.photo.save(f"worker_{user.id}.jpg", _avatar(f"worker_{user.id}", colour), save=True)
            self.stdout.write(
                f"  worker  : {phone} types={type_slugs} searchable={profile.is_searchable}"
            )

    def _adopt_orphan_admins(self, society):
        """Attach any pre-existing superuser to the demo society.

        A superuser made with createsuperuser has society=None, and every
        society-scoped endpoint filters on it — so it can log in and then see
        nothing at all, which reads as a broken app rather than an empty tenant.
        """
        orphans = User.objects.filter(society__isnull=True, is_superuser=True)
        count = orphans.update(society=society, is_approved=True)
        if count:
            self.stdout.write(f"Adopted  : {count} superuser(s) into {society.name}")

    # -- output ------------------------------------------------------------

    def _report(self, society, towers, flats):
        line = "=" * 72
        self.stdout.write("\n" + line)
        self.stdout.write(self.style.SUCCESS("  SATHIFY DEMO DATA READY"))
        self.stdout.write(line)
        self.stdout.write(f"  Society : {society.name}, {society.city} (id={society.id}, {society.status})")
        self.stdout.write(f"  Towers  : {len(towers)}    Flats: {len(flats)}    Gates: {society.gates.count()}")
        self.stdout.write(f"\n  Password for EVERY account below: {DEMO_PASSWORD}\n")
        self.stdout.write(f"  {'ROLE':<16}{'PHONE':<14}{'NAME'}")
        self.stdout.write("  " + "-" * 68)
        for phone, first, last, role in ACCOUNTS:
            self.stdout.write(f"  {role:<16}{phone:<14}{first} {last}")
        self.stdout.write(line + "\n")
