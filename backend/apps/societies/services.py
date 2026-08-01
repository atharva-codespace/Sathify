"""
Module 2 — shared resident rules used across modules.

``primary_resident_or_403`` lives here rather than in whichever module happened
to need it first. Module 2.4 designates one primary account holder per flat and
reserves acting on the household's behalf to them; Modules 4 (hiring), 5
(bookings) and — once built — 6 (scheduling) all have to enforce exactly that
rule, and three copies of it would eventually become three slightly different
rules.
"""

from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from .models import Resident


def primary_resident_or_403(user) -> Resident:
    """The caller's resident record, if they may act for their flat.

    Raises ``PermissionDenied`` with a message that says what to do about it,
    because "403" alone leaves a resident with no idea why the button failed.

    The first person to claim a flat automatically becomes its primary (see
    ``ResidentProfileCreateSerializer``), so this never blocks a single-occupant
    household — only the second and later members of a shared one.
    """
    resident = (
        Resident.objects.filter(user=user).select_related("flat__tower", "user").first()
    )
    if resident is None:
        raise PermissionDenied("Claim your flat before hiring or booking a worker.")
    if not resident.is_primary:
        raise PermissionDenied(
            "Only the primary account holder for your flat can do this. "
            "Ask your society administrator to reassign it if that should be you."
        )
    return resident
