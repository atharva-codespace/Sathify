"""
Cross-cutting abstract base models.

Every concrete model in Sathify inherits from one of these so that auditability
(SRS 5.5 — a 3-year audit trail across bookings, attendance, payments, gate
entries and complaints) is a property of the schema rather than something each
module has to remember to implement.
"""

import uuid

from django.db import models


class TimeStampedModel(models.Model):
    """Adds automatic created/updated timestamps.

    The default base for ordinary domain models.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        get_latest_by = "created_at"


class UUIDPrimaryKeyModel(models.Model):
    """Uses a UUID primary key instead of a sequential integer.

    Applied to anything whose identifier is exposed outside the server — QR
    payloads, attendance events synced from the guard's offline queue, payment
    records. Two reasons:

    1. Sequential IDs leak volume ("how many workers does this platform really
       have?") and are trivially enumerable from a mobile client.
    2. Module 13's offline sync requires the *client* to generate an ID before
       the server has ever seen the record, so that replaying a queued event
       after reconnecting cannot create a duplicate.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SocietyScopedModel(models.Model):
    """Marks a model as belonging to exactly one society.

    Sathify is multi-tenant, keyed by society (SRS 5.7). A guard or
    administrator at one society must never be able to read another society's
    data through a shared endpoint, so this FK is the anchor that
    ``SocietyScopedQuerysetMixin`` filters on.
    """

    society = models.ForeignKey(
        "societies.Society",
        on_delete=models.CASCADE,
        related_name="%(class)s_set",
        db_index=True,
    )

    class Meta:
        abstract = True
