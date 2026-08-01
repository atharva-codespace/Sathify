"""
Module 7 — the gate logic.

-------------------------------------------------------------------------------
LOOKUP AND RECORD ARE SEPARATE OPERATIONS
-------------------------------------------------------------------------------
Scanning a code and logging a decision are two different things, and keeping
them apart is what makes the offline path identical to the online one:

* :func:`look_up_pass` answers "who is this, and are they expected?" It creates
  nothing. Online the guard calls it; offline their device answers the same
  question from the cached roster.
* :func:`record_event` writes what the guard actually decided. Online it runs
  immediately; offline the device queues it and :func:`sync_events` replays it
  later against the same function.

If the server decided on scan instead, the offline path would have to grow its
own decision logic and the two would drift — which is precisely the bug that
gets a worker admitted on one device and refused on another.

-------------------------------------------------------------------------------
THE SERVER RECOMMENDS; THE GUARD DECIDES
-------------------------------------------------------------------------------
SRS 3.7 gives the guard the allow/deny call. So this module computes a
*recommendation* and records the guard's answer — it never overrules them. The
one thing it does enforce is that a revoked pass or an unapproved worker cannot
be silently recorded as a clean entry: that is flagged on the event regardless
of what was decided, because it is exactly what an audit needs to surface.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.scheduling.schedule import ScheduleItem, society_schedule, worker_day
from apps.societies.models import Gate
from apps.workers.models import WorkerProfile

from .face import FaceResult, verify_face
from .models import (
    AttendanceEvent,
    Decision,
    Direction,
    GatePass,
    VerificationMethod,
)

logger = logging.getLogger(__name__)

#: How far either side of a scheduled visit an arrival still counts as "for"
#: that visit. Generous: a worker who turns up an hour early is still coming
#: for the 9am job, and forcing an exact match would orphan half the events
#: that Module 8 needs to bill from.
VISIT_MATCH_WINDOW_MINUTES = 120


class AttendanceError(Exception):
    """Base for refusals that are business rules, not bugs."""

    code = "attendance_error"


class UnknownPass(AttendanceError):
    code = "unknown_pass"


class WrongSociety(AttendanceError):
    code = "wrong_society"


# ---------------------------------------------------------------------------
# 7.2 Scan lookup
# ---------------------------------------------------------------------------


@dataclass
class PassLookup:
    """Everything the guard needs to decide, from one scan."""

    worker: WorkerProfile
    is_usable: bool
    reason: str = ""
    expected_visits: list[ScheduleItem] = field(default_factory=list)

    @property
    def is_expected(self) -> bool:
        return bool(self.expected_visits)

    @property
    def recommendation(self) -> str:
        """What the server suggests. The guard is free to disagree."""
        if not self.is_usable:
            return Decision.DENIED
        if not self.is_expected:
            # Not scheduled is not the same as not permitted — a worker may be
            # covering for someone, or collecting something. The guard looks at
            # them and decides; the event records that it was unscheduled.
            return Decision.PENDING_REVIEW
        return Decision.ALLOWED


def look_up_pass(code, society_id, *, at: dt.datetime | None = None) -> PassLookup:
    """Resolve a scanned QR code (Module 7.2). Creates nothing.

    Raises :class:`UnknownPass` for a code that does not exist and
    :class:`WrongSociety` for one belonging elsewhere — the two are separate
    because they mean different things to a guard: a stranger's card versus a
    card from the society next door.
    """
    at = at or timezone.now()

    gate_pass = (
        GatePass.objects.select_related("worker__user", "worker__user__society")
        .filter(code=code)
        .first()
    )
    if gate_pass is None:
        raise UnknownPass("That code is not recognised.")

    if gate_pass.worker.user.society_id != society_id:
        raise WrongSociety("That pass belongs to a different society.")

    worker = gate_pass.worker
    reason = ""
    if not gate_pass.is_usable:
        if gate_pass.revoked_at is not None:
            reason = f"This pass was cancelled. {gate_pass.revoked_reason}".strip()
        elif not worker.user.is_approved:
            reason = "This worker is not currently approved to enter."
        else:
            reason = "This pass is not active."

    return PassLookup(
        worker=worker,
        is_usable=gate_pass.is_usable,
        reason=reason,
        expected_visits=expected_visits_for(worker.pk, at),
    )


def expected_visits_for(worker_id, at: dt.datetime) -> list[ScheduleItem]:
    """The visits this worker is due for around ``at``.

    Reads Module 6's derived schedule rather than querying engagements and
    bookings again, so the gate and the worker's own calendar can never
    disagree about what was expected.
    """
    local = timezone.localtime(at)
    minutes = local.hour * 60 + local.minute

    return [
        item
        for item in worker_day(worker_id, local.date())
        if abs(item.start_minutes - minutes) <= VISIT_MATCH_WINDOW_MINUTES
    ]


# ---------------------------------------------------------------------------
# 7.2 / 7.4 Roster — what the guard's device caches
# ---------------------------------------------------------------------------


def gate_roster(society_id, day: dt.date) -> list[dict]:
    """The day's expected visits plus the pass codes that unlock them.

    This is the payload a guard's device caches so scanning keeps working with
    no connectivity (Module 7.2). It is deliberately assembled per worker rather
    than per visit: a worker with three visits scans one code, and the device
    needs to resolve that code once.

    Contains pass codes, so it is only ever served to gate staff of that
    society — the permission on the view is load-bearing, not decorative.
    """
    items = society_schedule(society_id, day, day)

    by_worker: dict[int, list[ScheduleItem]] = {}
    for item in items:
        by_worker.setdefault(item.worker_id, []).append(item)

    passes = {
        gate_pass.worker_id: gate_pass
        for gate_pass in GatePass.objects.select_related("worker__user").filter(
            worker__user__society_id=society_id, is_active=True, revoked_at__isnull=True
        )
    }

    roster = []
    for worker_id, visits in by_worker.items():
        gate_pass = passes.get(worker_id)
        first = visits[0]
        roster.append(
            {
                "worker_id": worker_id,
                "worker_name": first.worker_name,
                "pass_code": str(gate_pass.code) if gate_pass else None,
                "visits": [
                    {
                        "source": visit.source,
                        "source_id": visit.source_id,
                        "title": visit.title,
                        "start_time": visit.start_time,
                        "end_time": visit.end_time,
                        "flat_label": visit.flat_label,
                        "is_confirmed": visit.is_confirmed,
                    }
                    for visit in visits
                ],
            }
        )

    roster.sort(key=lambda row: row["worker_name"])
    return roster


# ---------------------------------------------------------------------------
# 7.6 Recording
# ---------------------------------------------------------------------------


def _link_visit(event: AttendanceEvent, visits: list[ScheduleItem]) -> None:
    """Attach the event to whichever visit it was for, if any."""
    if not visits:
        event.was_expected = False
        return

    event.was_expected = True
    visit = visits[0]
    if visit.source == "engagement":
        event.engagement_id = visit.source_id
    else:
        event.booking_id = visit.source_id


def _notify_resident_of_arrival(event, worker, society) -> None:
    """Tell the household their worker is here (Module 10, ATTENDANCE).

    Reaches the resident through whichever visit the event was linked to, so it
    cannot fire for a household that was not expecting anybody. Lazily imported
    and non-raising like every other notification call site: a gate entry that
    was logged must never be undone because a phone was unreachable.
    """
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    source = event.engagement or event.booking
    resident = getattr(source, "resident", None)
    if resident is None:
        return

    notify(
        recipient=resident.user,
        category=NotificationCategory.ATTENDANCE,
        title=f"{worker.user.get_full_name()} has arrived",
        body="Logged at the gate just now.",
        data={"route": "/schedule"},
        society=society,
    )


@transaction.atomic
def record_event(
    *,
    event_id,
    worker: WorkerProfile,
    society,
    direction: str,
    method: str,
    decision: str,
    occurred_at: dt.datetime,
    recorded_by=None,
    gate=None,
    decision_reason: str = "",
    device_id: str = "",
    was_offline: bool = False,
) -> tuple[AttendanceEvent, bool]:
    """Write one gate decision. Returns ``(event, created)``.

    Idempotent on ``event_id``, which the guard's device generates before the
    server has seen the record. A replayed sync therefore returns the existing
    row rather than logging someone through the gate twice — the property the
    whole offline design rests on.

    An existing event is never mutated. A device retrying a sync must not be
    able to rewrite a decision an administrator has already reviewed.
    """
    existing = AttendanceEvent.objects.filter(pk=event_id).first()
    if existing is not None:
        return existing, False

    event = AttendanceEvent(
        id=event_id,
        society=society,
        worker=worker,
        gate=gate,
        recorded_by=recorded_by,
        direction=direction,
        method=method,
        decision=decision,
        decision_reason=decision_reason,
        occurred_at=occurred_at,
        recorded_at=timezone.now(),
        device_id=device_id,
        was_offline=was_offline,
    )
    _link_visit(event, expected_visits_for(worker.pk, occurred_at))
    event.save()

    # Module 10 — GATE_ENTRY is safety-critical and cannot be muted. A refusal
    # is the case that matters: a worker turned away needs to know it happened
    # and why, because they may be standing outside wondering what went wrong.
    if decision == Decision.DENIED:
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import notify

        notify(
            recipient=worker.user,
            category=NotificationCategory.GATE_ENTRY,
            title="You were not let in",
            body=decision_reason or "The gate did not accept your pass.",
            data={"route": "/my-pass"},
            society=society,
        )

    # The other half: the household waiting for them. ATTENDANCE is mutable —
    # a resident who does not want a ping every morning can switch it off —
    # which is exactly why it is a separate category from GATE_ENTRY rather
    # than reusing the safety-critical one that cannot be muted.
    #
    # Only for an arrival that was actually expected. Notifying on an
    # unscheduled entry would tell a resident their worker had arrived on a day
    # nobody booked one.
    elif (
        decision == Decision.ALLOWED
        and direction == Direction.ENTRY
        and event.was_expected
    ):
        _notify_resident_of_arrival(event, worker, society)

    logger.info(
        "Attendance %s: worker=%s %s %s expected=%s offline=%s",
        event.pk, worker.pk, direction, decision, event.was_expected, was_offline,
    )
    return event, True


# ---------------------------------------------------------------------------
# 7.4 Sync
# ---------------------------------------------------------------------------


@dataclass
class SyncOutcome:
    """What a batch sync did, per event, so the device can clear its queue."""

    created: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "accepted_count": len(self.created) + len(self.duplicates),
        }


def sync_events(rows: list[dict], *, guard, society) -> SyncOutcome:
    """Replay a guard device's offline queue (Module 7.4).

    Each row is handled independently: one malformed event must not reject the
    other thirty-nine, because the device would then retry the whole batch
    forever and the day's attendance would never land. Rejected ids come back
    named so the device can drop exactly those and clear the rest.

    Duplicates are a success, not an error. They are the expected outcome of a
    device that synced, lost its connection before recording the response, and
    retried.
    """
    outcome = SyncOutcome()

    for row in rows:
        event_id = row.get("id")
        try:
            worker = WorkerProfile.objects.select_related("user").get(
                pk=row["worker"], user__society_id=society.pk
            )
        except (WorkerProfile.DoesNotExist, KeyError):
            outcome.rejected.append(
                {"id": str(event_id), "reason": "Unknown worker for this society."}
            )
            continue

        gate = None
        if row.get("gate"):
            # Scoped to the society: a device must not be able to attribute an
            # entry to another society's gate by sending its id.
            gate = Gate.objects.filter(pk=row["gate"], society_id=society.pk).first()

        try:
            _, created = record_event(
                event_id=event_id,
                worker=worker,
                society=society,
                gate=gate,
                direction=row.get("direction", Direction.ENTRY),
                method=row.get("method", VerificationMethod.QR),
                decision=row.get("decision", Decision.ALLOWED),
                occurred_at=row["occurred_at"],
                recorded_by=guard,
                decision_reason=row.get("decision_reason", ""),
                device_id=row.get("device_id", ""),
                was_offline=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to sync attendance event %s", event_id)
            outcome.rejected.append({"id": str(event_id), "reason": str(exc)})
            continue

        (outcome.created if created else outcome.duplicates).append(str(event_id))

    logger.info(
        "Attendance sync from guard %s: %s created, %s duplicate, %s rejected",
        getattr(guard, "pk", None),
        len(outcome.created),
        len(outcome.duplicates),
        len(outcome.rejected),
    )
    return outcome


# ---------------------------------------------------------------------------
# 7.3 Face verification
# ---------------------------------------------------------------------------


def run_face_check(event: AttendanceEvent, live_photo_path: str) -> FaceResult:
    """Compare a live gate photo against the worker's registered photo.

    Records the outcome on the event and, when the comparison did not clear the
    threshold, moves the event to ``PENDING_REVIEW`` — never to ``DENIED``. See
    ``face.py`` for why that distinction is not negotiable.

    A worker with no registered photo cannot be compared at all; that is
    reported as unavailable rather than as a failed match, because it says
    nothing about who is standing at the gate.
    """
    reference = event.worker.photo
    if not reference:
        result = FaceResult(
            available=False,
            reason="This worker has no registered photo to compare against.",
        )
    else:
        result = verify_face(live_photo_path, reference.path)

    event.face_checked = True
    event.face_match_score = result.score
    event.face_verified = result.verified

    if not result.verified and event.decision == Decision.ALLOWED:
        event.decision = Decision.PENDING_REVIEW
        event.decision_reason = (
            result.reason
            or "The face check did not match confidently. Please verify visually."
        )

    event.save(
        update_fields=[
            "face_checked", "face_match_score", "face_verified",
            "decision", "decision_reason", "updated_at",
        ]
    )
    return result


# ---------------------------------------------------------------------------
# 7.1 Gate passes
# ---------------------------------------------------------------------------


def ensure_gate_pass(worker: WorkerProfile) -> GatePass:
    """The worker's pass, creating one on first use.

    Created lazily rather than at approval time so that a worker approved
    before this module existed still gets one the first time anybody looks.
    """
    gate_pass, _ = GatePass.objects.get_or_create(worker=worker)
    return gate_pass


# ---------------------------------------------------------------------------
# 13.3 Tier 2 — worker self check-in inside a GPS geofence
# ---------------------------------------------------------------------------
#
# The secondary attendance tier, for the gate with no guard on it: an unstaffed
# service entrance, a night shift, a guard whose phone died. Without it a
# worker who turned up and did the job has no record of having done so, and
# Module 8 bills from that record.
#
# WHAT THIS TIER CAN AND CANNOT ESTABLISH
# ---------------------------------------
# It establishes that somebody holding the worker's account was plausibly near
# the society at a time they were expected. It does not establish that they went
# inside, and a phone can be handed to someone else. So a self check-in is
# recorded as *weaker evidence*, never as an equal of a guard's scan:
#
#   * ``method`` is SELF_CHECKIN, so every downstream reader can tell.
#   * Outside the geofence, or with no position at all, the decision is
#     PENDING_REVIEW — an administrator confirms it. Never DENIED: a GPS fix in
#     a courtyard between two towers is routinely 150 m out, and refusing
#     somebody's day's wages over that would be punishing them for physics.
#   * A society can switch the tier off entirely
#     (``Society.allow_resident_self_checkin``).
#
# NAMING NOTE
# -----------
# The modspec calls this "resident self check-in"; the society flag keeps that
# wording. Module 7 had already settled the actor question in the other
# direction — ``VerificationMethod.SELF_CHECKIN`` reads "Worker checked in from
# the app" — and the worker is the right one to geofence, because they are the
# person whose presence is in question. A resident is at home either way, so
# geofencing them would measure nothing.


class SelfCheckInDisabled(AttendanceError):
    code = "self_checkin_disabled"


@dataclass
class SelfCheckInResult:
    """What a self check-in produced."""

    event: AttendanceEvent
    created: bool
    geofence: object  # core.resilience.GeofenceCheck
    was_expected: bool

    @property
    def needs_review(self) -> bool:
        return self.event.decision == Decision.PENDING_REVIEW

    def as_dict(self) -> dict:
        return {
            "id": str(self.event.pk),
            "created": self.created,
            "decision": self.event.decision,
            "decision_reason": self.event.decision_reason,
            "was_expected": self.was_expected,
            "needs_review": self.needs_review,
            "distance_metres": self.geofence.distance_metres,
            "location_checked": self.geofence.available,
        }


@transaction.atomic
def self_check_in(
    *,
    event_id,
    worker: WorkerProfile,
    society,
    direction: str,
    occurred_at: dt.datetime,
    latitude=None,
    longitude=None,
    accuracy_metres: float | None = None,
    device_id: str = "",
    was_offline: bool = False,
) -> SelfCheckInResult:
    """Module 13.3's secondary tier. Idempotent on ``event_id``.

    Takes a client-generated id like every other queued event (13.1), so a
    worker who taps "I have arrived" on a dead connection and syncs later gets
    one record, not two.
    """
    from apps.core.resilience import check_geofence

    if not getattr(society, "allow_resident_self_checkin", False):
        raise SelfCheckInDisabled(
            "This society requires a guard to record entries."
        )

    geofence = check_geofence(
        latitude=latitude,
        longitude=longitude,
        centre_latitude=society.latitude,
        centre_longitude=society.longitude,
        accuracy_metres=accuracy_metres,
    )

    expected = expected_visits_for(worker.pk, occurred_at)

    decision, reason = _self_check_in_decision(geofence, expected)

    event, created = record_event(
        event_id=event_id,
        worker=worker,
        society=society,
        direction=direction,
        method=VerificationMethod.SELF_CHECKIN,
        decision=decision,
        occurred_at=occurred_at,
        recorded_by=None,  # Nobody staffed this. That is the point of the tier.
        gate=None,
        decision_reason=reason,
        device_id=device_id,
        was_offline=was_offline,
    )

    return SelfCheckInResult(
        event=event,
        created=created,
        geofence=geofence,
        was_expected=bool(expected),
    )


def _self_check_in_decision(geofence, expected: list) -> tuple[str, str]:
    """Decide what a self check-in is worth. Never returns DENIED.

    Two independent signals — was the phone near the society, and was a visit
    expected — and both must hold for this to stand on its own. Either one
    missing sends it to an administrator, who has the roster and the resident's
    phone number and can settle it in a minute.
    """
    if not geofence.available:
        return (
            Decision.PENDING_REVIEW,
            geofence.reason or "Location could not be checked.",
        )

    if not geofence.inside:
        return Decision.PENDING_REVIEW, geofence.reason

    if not expected:
        # At the society, but not on the roster for now. Common and innocent —
        # a swapped day, a booking added on paper — and equally what an
        # unscheduled visit looks like. An administrator decides which.
        return (
            Decision.PENDING_REVIEW,
            "At the society, but no visit was scheduled for this time.",
        )

    return Decision.ALLOWED, ""


__all__ = [
    "VISIT_MATCH_WINDOW_MINUTES",
    "AttendanceError",
    "PassLookup",
    "SelfCheckInDisabled",
    "SelfCheckInResult",
    "SyncOutcome",
    "UnknownPass",
    "WrongSociety",
    "ensure_gate_pass",
    "expected_visits_for",
    "gate_roster",
    "look_up_pass",
    "record_event",
    "run_face_check",
    "self_check_in",
    "sync_events",
]
