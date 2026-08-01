"""Seed the Module 5.1 service catalogue.

The categories themselves come straight from the specification — SRS 3.4 lists
house shifting and unpacking, festival or deep cleaning, event preparation,
temporary cooking, and emergency household assistance, and modspec 5.1 repeats
four of the five. Seeding them is therefore implementing the spec, not inventing
a product decision, and without it Module 5 has an empty catalogue and no
bookable anything.

TWO THINGS AN OPERATOR SHOULD REVIEW:

* **Prices are indicative starting points, not researched rates.** The spec asks
  for "price guidance shown to the resident up front" but does not say what it
  should be. These bands are plausible for Indian metro domestic services and
  are meant to be tuned per market from the admin. They are guidance only — the
  amount actually charged is the per-booking ``quoted_price`` that the resident
  and worker agree, never these numbers.

* **``service_type`` is left unset.** It would map each category onto the kind
  of worker qualified for it, which narrows Module 5.3's candidate pool. It
  cannot be seeded because the ``ServiceType`` catalogue is itself a per-society
  administrative decision (modspec 2.5) and is deliberately not seeded. Until an
  administrator links them, every approved worker is matchable for every
  category.

Re-runnable: rows are created only when absent, so re-applying never overwrites
prices an operator has already tuned.
"""

from django.db import migrations

CATEGORIES = [
    {
        "slug": "deep-cleaning",
        "name": "Festival or deep cleaning",
        "description": "A thorough top-to-bottom clean, such as before a festival.",
        "icon": "cleaning_services",
        "expected_duration_minutes": 240,
        "price_min": 1200,
        "price_max": 3000,
    },
    {
        "slug": "house-shifting",
        "name": "House shifting and unpacking",
        "description": "Help packing, moving and unpacking a household.",
        "icon": "local_shipping",
        "expected_duration_minutes": 240,
        "price_min": 1500,
        "price_max": 4000,
    },
    {
        "slug": "event-preparation",
        "name": "Event preparation",
        "description": "Setting up and clearing away for a gathering at home.",
        "icon": "celebration",
        "expected_duration_minutes": 300,
        "price_min": 1500,
        "price_max": 3500,
    },
    {
        "slug": "temporary-cooking",
        "name": "Temporary cooking",
        "description": "Cooking for a single day, for guests or when help is away.",
        "icon": "restaurant",
        "expected_duration_minutes": 120,
        "price_min": 500,
        "price_max": 1500,
    },
    {
        "slug": "emergency-assistance",
        "name": "Emergency household assistance",
        "description": "Urgent help at short notice.",
        "icon": "emergency",
        "expected_duration_minutes": 60,
        "price_min": 300,
        "price_max": 1000,
        # The only category exempt from the society's minimum booking notice.
        # A notice window that blocks an emergency defeats the category.
        "bypasses_notice_period": True,
    },
]


def seed(apps, schema_editor):
    ServiceCategory = apps.get_model("bookings", "ServiceCategory")
    for row in CATEGORIES:
        ServiceCategory.objects.get_or_create(slug=row["slug"], defaults=row)


def unseed(apps, schema_editor):
    """Remove only the seeded rows, and only if nothing depends on them.

    A category with bookings against it is left alone: ``Booking.category`` is
    PROTECT, so deleting it would raise, and a migration that destroys booking
    history to roll back cleanly would be worse than one that leaves a row.
    """
    ServiceCategory = apps.get_model("bookings", "ServiceCategory")
    slugs = [row["slug"] for row in CATEGORIES]
    ServiceCategory.objects.filter(slug__in=slugs, bookings__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("bookings", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
