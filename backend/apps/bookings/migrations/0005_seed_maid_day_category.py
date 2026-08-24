"""Seed the maid day-hire category.

Sathify sells a helper two ways: by the month, as a recurring ``Engagement``,
and by the day, as a one-off ``Booking``. The month has always had a home. The
day did not — the seeded catalogue (migration 0002) is five *specialist* jobs
(deep cleaning, house shifting, event preparation, temporary cooking, emergency
assistance), and none of them means "a maid, for one day".

This adds that one. It is the booking counterpart of
``apps.core.pricing.MAID_MONTHLY_RATE_INR``.

WHY ITS PRICE BAND IS A SINGLE NUMBER
-------------------------------------
Every other category carries a ``price_min``/``price_max`` *range*, because the
amount is a per-booking figure the resident and worker agree between themselves.
This one does not: ``price_min == price_max == MAID_DAY_RATE_INR``, and
``BookingCreateSerializer`` refuses a different quote for this category. The
platform sets the price for a maid, by the day exactly as by the month, and a
range would be an invitation to negotiate that the product does not offer.

The five specialist categories are deliberately left alone. "A maid for a day"
and "shift my flat" are not the same price, and flattening them to one number
would misprice the work rather than simplify it.

Re-runnable, like 0002: the row is created only when absent, so re-applying
never overwrites a price an operator has tuned.
"""

from django.db import migrations

from apps.core.pricing import MAID_DAY_CATEGORY_SLUG, MAID_DAY_RATE_INR

CATEGORY = {
    "slug": MAID_DAY_CATEGORY_SLUG,
    "name": "Maid for a day",
    "description": "A helper for a single day's household work.",
    "icon": "home_work",
    # A day's help, not a full shift. Prefills the booking form; the resident
    # can still shorten or lengthen the window, which does not change the price.
    "expected_duration_minutes": 240,
    "price_min": MAID_DAY_RATE_INR,
    "price_max": MAID_DAY_RATE_INR,
}


def seed(apps, schema_editor):
    ServiceCategory = apps.get_model("bookings", "ServiceCategory")
    ServiceCategory.objects.get_or_create(
        slug=CATEGORY["slug"], defaults=CATEGORY
    )


def unseed(apps, schema_editor):
    """Remove the row, and only if nothing depends on it.

    ``Booking.category`` is PROTECT, so a category with bookings against it is
    left in place — the same reasoning as migration 0002's unseed.
    """
    ServiceCategory = apps.get_model("bookings", "ServiceCategory")
    ServiceCategory.objects.filter(
        slug=CATEGORY["slug"], bookings__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("bookings", "0004_booking_broadcast_at_and_more")]

    operations = [migrations.RunPython(seed, unseed)]
