"""
What hiring a helper costs, in one greppable place.

-------------------------------------------------------------------------------
WHY THESE ARE CONSTANTS AND NOT COLUMNS
-------------------------------------------------------------------------------
Sathify quotes one price for everybody. A helper is not cheaper because she is
new to the platform and not dearer because she is popular, and a resident cannot
talk the figure down. That is a deliberate product decision rather than a
missing feature: per-worker rates turn a hiring screen into a haggling screen,
and the person with least room to refuse is always the same one.

So the rate lives here, as a number, instead of in a field somebody can edit.
``WorkerProfile.expected_monthly_rate`` and ``Engagement.monthly_rate`` are
still columns — history has to keep the figure it was actually agreed at, and a
settlement computed last month must not silently re-price when this file
changes — but nothing outside these constants writes a *new* value.

-------------------------------------------------------------------------------
WHY A DAY IS NOT A THIRTIETH OF A MONTH
-------------------------------------------------------------------------------
``MAID_DAY_RATE_INR`` is 250 while a month is 2500, so a day costs three times
its pro-rata share. That is not an arithmetic slip. A single day is a different
product: it carries no ongoing income, the helper travels for one visit, and it
is priced the way short-term work is priced everywhere.

The two numbers therefore reach the database by two different routes, and it is
worth being explicit about which is which:

* The **month** is :data:`MAID_MONTHLY_RATE_INR`, on an ``Engagement``. Part
  months still settle pro-rata — ``hiring/settlement.py`` divides this by the
  calendar, and that is untouched.
* The **day** is :data:`MAID_DAY_RATE_INR`, on a one-off ``Booking`` in the
  ``maid-day`` service category. It never passes through the settlement engine.

Nothing divides one of these by the other, and nothing should start.
"""

from __future__ import annotations

#: Agreed monthly pay for a recurring helper, in whole rupees. Written to
#: ``Engagement.monthly_rate`` at hire time and mirrored onto
#: ``WorkerProfile.expected_monthly_rate`` so search and sort keep working.
MAID_MONTHLY_RATE_INR = 2500

#: Price of a single day's help, in whole rupees. Quoted on a one-off booking
#: in the :data:`MAID_DAY_CATEGORY_SLUG` category. See the module docstring for
#: why this is not ``MAID_MONTHLY_RATE_INR / 30``.
MAID_DAY_RATE_INR = 250

#: The service category that day-hire bookings are made against. Seeded by
#: ``bookings.migrations.0005_seed_maid_day_category``; the specialist
#: categories alongside it (deep cleaning, house shifting) keep their own
#: ranges, because "a maid for a day" and "shift my flat" are not one price.
MAID_DAY_CATEGORY_SLUG = "maid-day"
