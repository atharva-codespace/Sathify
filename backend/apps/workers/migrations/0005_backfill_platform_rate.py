"""Point every worker profile at the platform rate.

``expected_monthly_rate`` used to be a figure each worker named for themselves,
so existing rows hold whatever was entered before pricing became a platform
decision (the demo seed, for instance, wrote 9000 and 7500). Module 4 search
sorts and filters on this column, so leaving the old values in place would rank
workers against each other by a number none of them can influence any more —
and show a resident a rate that is not what they would be charged.

WHAT THIS DOES **NOT** TOUCH
---------------------------
``Engagement.monthly_rate`` and ``HireRequest.monthly_rate`` are left exactly as
they are. Those are not a mirror of a price list, they are the figure two people
agreed to, and ``hiring/settlement.py`` divides them to work out what a
household owes. Rewriting them would restate past agreements and silently change
what is owed on a month somebody has already worked. New requests pick up the
constant at creation (``HireRequestCreateSerializer``); old ones keep their word.

Reversible in the only sense that matters: the old per-worker figures are gone
either way, so the reverse is a no-op rather than a lie about restoring them.
"""

from django.db import migrations

from apps.core.pricing import MAID_MONTHLY_RATE_INR


def backfill(apps, schema_editor):
    WorkerProfile = apps.get_model("workers", "WorkerProfile")
    WorkerProfile.objects.exclude(
        expected_monthly_rate=MAID_MONTHLY_RATE_INR
    ).update(expected_monthly_rate=MAID_MONTHLY_RATE_INR)


def noop(apps, schema_editor):
    """Nothing to restore — the per-worker figures were not kept anywhere."""


class Migration(migrations.Migration):
    dependencies = [("workers", "0004_alter_workerprofile_expected_monthly_rate")]

    operations = [migrations.RunPython(backfill, noop)]
