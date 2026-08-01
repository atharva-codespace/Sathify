"""
Module 13 — the offline and resilience conventions.

Not a feature. The modspec is explicit that this is "a set of engineering
conventions applied across the modules where connectivity or a single person's
presence can't be guaranteed", and most of it was built alongside the modules it
applies to rather than afterwards.

What lives here is the part that is genuinely shared — the geofence arithmetic
the secondary attendance tier needs — plus a written statement of each
convention, so that ``apps/core/test_resilience.py`` can check they still hold
rather than leaving them as folklore.

-------------------------------------------------------------------------------
13.1  LOCAL CACHE AND SYNC QUEUE
-------------------------------------------------------------------------------
Anything a device can record while offline is addressed by a **client-generated
UUID**, minted before the server has seen it. That is why
:class:`apps.core.models.UUIDPrimaryKeyModel` exists and why
``AttendanceEvent`` uses it. A server-assigned id cannot work: the device has to
name the record in order to retry it safely, and it has no id to retry with
until the request it is retrying succeeds.

Implemented in ``mobile/lib/features/attendance/data/local/attendance_queue.dart``
(sqflite) and the roster cache that feeds it.

-------------------------------------------------------------------------------
13.2  IDEMPOTENT SYNC ENDPOINTS
-------------------------------------------------------------------------------
A batch sync accepts the same event twice without side effects, and reports
duplicates as **success**, not error. A device that synced, lost its connection
before reading the response, and retried has done nothing wrong — and treating
its retry as a failure would make it retry forever.

Two rules follow, and both are checked in the conformance tests:

* An existing record is returned, never mutated. A retry must not be able to
  rewrite a decision an administrator has already reviewed.
* One bad row in a batch rejects that row only. Rejecting the batch would mean
  a single malformed event stops a whole day of attendance from ever landing.

Implemented as ``apps.attendance.services.sync_events``.

-------------------------------------------------------------------------------
13.3  TIERED ATTENDANCE FALLBACK
-------------------------------------------------------------------------------
Three tiers, in order of evidential strength:

1. **QR plus guard** — ``VerificationMethod.QR``, optionally with a face check.
2. **Worker self check-in inside a GPS geofence** — ``SELF_CHECKIN``, for when
   no guard is on duty. This module provides :func:`check_geofence`.
3. **Paper register, photographed and transcribed** — ``RegisterScan``.

The rule that binds them: **no tier may deny entry on its own.** Tier 1 denies
only when a guard decides to; tiers 2 and 3 produce ``PENDING_REVIEW`` at worst.
A worker turned away by a GPS drift or a bad photo loses a day's wages for a
measurement error, and the measurement is not good enough to justify that.

-------------------------------------------------------------------------------
13.4  AI FALLBACK CONVENTIONS
-------------------------------------------------------------------------------
Every Module 12 call goes through ``apps.ai_services.degradation.with_fallback``,
which takes the fallback as a **required argument** — there is no way to write
the AI half alone. See that module; the conformance tests check that each
declared AI feature still answers with no provider configured.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

EARTH_RADIUS_METRES = 6_371_000.0

#: How close a worker must be to the society to check themselves in.
#:
#: 250 m is deliberately loose. Consumer GPS in a built-up area with tall towers
#: is routinely 50–100 m out, and the society's stored coordinate is a single
#: point for a compound that may be 200 m across. A tight radius would reject
#: workers standing in the actual gateway, and the cost of that error falls
#: entirely on them.
#:
#: This is a *plausibility* check, not proof of presence. It rules out a check-in
#: from another city; it does not establish that somebody walked through a door.
#: That is why tier 2 sits below the guard scan and why it cannot deny.
DEFAULT_GEOFENCE_RADIUS_METRES = 250.0


def haversine_metres(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance between two coordinates, in metres.

    Pure, and duplicated in spirit by ``apps.hiring.scoring.haversine_km`` —
    deliberately. That one is part of a scoring formula that must stay free of
    Django imports and is tuned in kilometres for a 10 km horizon; this one
    answers a yes/no question in metres. Sharing them would couple a
    recommendation weight to a gate decision.
    """
    lat1, lon1, lat2, lon2 = (float(value) for value in (lat1, lon1, lat2, lon2))

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METRES * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class GeofenceCheck:
    """Whether a reported position is plausibly at a place.

    ``available`` is false when the check could not be made at all — the society
    has no coordinates, or the device sent none. That is a third state, not a
    failure: an unmeasured position is not a wrong one, and the caller must not
    read silence as a refusal.
    """

    available: bool
    inside: bool = False

    #: Metres from the centre point. None when nothing was measured.
    distance_metres: float | None = None

    radius_metres: float = 0.0

    #: The accuracy the device itself reported, in metres, when it did.
    reported_accuracy_metres: float | None = None

    reason: str = ""

    @property
    def needs_human_confirmation(self) -> bool:
        """True unless the position was measured *and* fell inside.

        The same shape as Module 12.4's face check, for the same reason: a
        measurement that failed and a measurement that could not be taken both
        leave the question open, and neither may be treated as a denial.
        """
        return not (self.available and self.inside)


def geofence_radius_metres() -> float:
    return float(
        getattr(settings, "GEOFENCE_SETTINGS", {}).get(
            "RADIUS_METRES", DEFAULT_GEOFENCE_RADIUS_METRES
        )
    )


def check_geofence(
    *,
    latitude,
    longitude,
    centre_latitude,
    centre_longitude,
    accuracy_metres: float | None = None,
    radius_metres: float | None = None,
) -> GeofenceCheck:
    """Is this position plausibly at the given centre? Never raises.

    The device's own reported accuracy is added to the radius when it is worse
    than the radius allows. A phone that says "I am here, give or take 180 m"
    is being honest about a poor fix, and holding that against the person
    carrying it would punish them for standing next to a tall building.
    """
    radius = radius_metres if radius_metres is not None else geofence_radius_metres()

    if latitude is None or longitude is None:
        return GeofenceCheck(
            available=False,
            radius_metres=radius,
            reason="The device did not report a position.",
        )

    if centre_latitude is None or centre_longitude is None:
        return GeofenceCheck(
            available=False,
            radius_metres=radius,
            reason="This society has no coordinates set, so location cannot be checked.",
        )

    try:
        distance = haversine_metres(
            latitude, longitude, centre_latitude, centre_longitude
        )
    except (TypeError, ValueError) as exc:
        return GeofenceCheck(
            available=False,
            radius_metres=radius,
            reason=f"The position could not be read: {exc}",
        )

    effective_radius = radius
    if accuracy_metres is not None and accuracy_metres > 0:
        effective_radius = max(radius, float(accuracy_metres))

    inside = distance <= effective_radius
    return GeofenceCheck(
        available=True,
        inside=inside,
        distance_metres=round(distance, 1),
        radius_metres=effective_radius,
        reported_accuracy_metres=accuracy_metres,
        reason=""
        if inside
        else f"{round(distance)} m from the society, outside the "
        f"{round(effective_radius)} m allowance.",
    )


__all__ = [
    "DEFAULT_GEOFENCE_RADIUS_METRES",
    "EARTH_RADIUS_METRES",
    "GeofenceCheck",
    "check_geofence",
    "geofence_radius_metres",
    "haversine_metres",
]
