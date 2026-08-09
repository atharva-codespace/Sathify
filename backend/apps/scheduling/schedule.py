"""
Module 6.1 — assembling one true schedule from two systems.

A worker's day is made of recurring engagements (Module 4) and one-day bookings
(Module 5). The modspec's requirement is that they see *one* schedule rather
than two systems to check, and this is where the two are merged.

-------------------------------------------------------------------------------
DERIVED, NOT STORED
-------------------------------------------------------------------------------
Nothing here writes a row. An engagement already says "Mondays and Thursdays at
09:00 for 90 minutes"; expanding that into stored calendar entries would create a
second copy of the truth that drifts the moment someone pauses the engagement or
changes its days. So occurrences are expanded on read, over a bounded window.

The cost of that choice is that a query must be bounded — hence
:data:`MAX_SCHEDULE_DAYS`. The benefit is that pausing an engagement takes effect
instantly and correctly everywhere, with no reconciliation job to get wrong.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace

from django.db import models
from django.db.models import Prefetch
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.hiring.models import Engagement, EngagementStatus

from .models import VisitStatus

#: Widest range a single schedule query may span. Recurring engagements expand
#: to one item per matching day, so an unbounded range is an unbounded response.
MAX_SCHEDULE_DAYS = 62


class ScheduleRangeTooWide(ValueError):
    """Raised when a caller asks for more than :data:`MAX_SCHEDULE_DAYS`."""


@dataclass(frozen=True)
class ScheduleItem:
    """One expected visit, from either source.

    Carries ``source``/``source_id`` rather than a foreign key because it is not
    a database row — it is a projection, and the client uses those two to
    navigate back to the engagement or booking it came from.
    """

    source: str  # "engagement" | "booking"
    source_id: int
    date: dt.date
    start_time: dt.time
    duration_minutes: int

    title: str = ""
    worker_id: int = 0
    worker_name: str = ""
    resident_id: int = 0
    resident_name: str = ""
    flat_label: str = ""
    status: str = ""

    #: Bookings need the worker's confirmation; engagements are already agreed.
    is_confirmed: bool = True

    #: Whether this worker may still accept or decline this visit *right now*.
    #:
    #: Sent for the same reason ``can_mark_done`` is: the card that draws the
    #: buttons and the endpoint that answers them must not hold separate
    #: opinions. It is deliberately not the same thing as ``not is_confirmed`` —
    #: a request whose answering deadline has passed is still unconfirmed, and
    #: offering an Accept button for it produces a refusal the worker cannot act
    #: on. See ``Booking.is_actionable``.
    can_respond: bool = False

    #: The resident's expected arrival, where Module 6.2 timing overrides the
    #: engagement's own start time.
    expected_arrival: dt.time | None = None
    grace_minutes: int = 0
    task_notes: str = ""

    # --- 6.5 urgent leave ---------------------------------------------------
    #
    # A visit the regular worker is away for stays *on* the schedule rather than
    # disappearing from it. Everyone involved has to see that the slot exists and
    # that nobody — or somebody else — is filling it. Silently dropping the item
    # would make the gate roster (Module 7) forget the visit was ever expected,
    # and payroll (Module 8) counts expected visits from exactly this list.
    on_leave: bool = False
    leave_status: str = ""
    leave_request_id: int = 0

    #: Who is covering, shown on the regular worker's and the household's views.
    cover_worker_name: str = ""

    #: True on the *replacement's* own schedule: this is somebody else's visit
    #: that they agreed to take for one day.
    is_cover: bool = False
    #: Who they are covering for, on that same view.
    covering_for_name: str = ""

    # --- 6.6 how far through the day's work this visit is -------------------
    #
    # Composed from three sources, none of which is copied: the gate log says
    # whether somebody arrived and whether they left, and TaskCompletion says
    # whether the work was marked done. Departure travels separately from
    # status on purpose — finishing and leaving are different facts, and a
    # worker who stayed for a cup of tea has still finished.
    visit_status: str = "pending"
    checked_in_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    exit_confirmed_at: dt.datetime | None = None
    completion_note: str = ""
    completion_photo_url: str = ""

    #: Whether the worker may mark this visit done *right now*.
    #:
    #: Decided here and sent to the client, rather than re-derived there. The
    #: app used to work it out for itself from the visit date and a handful of
    #: flags, which is how the "Work done" button came to be missing on exactly
    #: the visits that needed it: the two rules had drifted, and the client's
    #: version was the one drawing the button. One rule, computed where the
    #: refusal is also decided, so the button and the endpoint cannot disagree.
    can_mark_done: bool = False

    #: How the worker's fee is settled: ``app`` or ``cash``. An emergency is
    #: cash, hand to hand, and the card must not offer to collect it.
    settlement: str = "app"

    # --- 6.7 what this visit is worth ---------------------------------------
    #
    # ``pay_paise`` is the day's rate — what this visit is worth *if* it is
    # worked. ``pay_state`` is the separate question of whether it has been
    # earned yet, and it deliberately has a third value beyond yes and no.
    pay_paise: int = 0
    pay_state: str = "not_yet"

    #: Minutes until this worker's next visit after this one. -1 when there is
    #: no next visit inside the window that was asked for — which is not the
    #: same as "no next visit", and the client should not render it as such.
    minutes_to_next: int = -1
    next_visit_at: dt.datetime | None = None

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    @property
    def has_left(self) -> bool:
        """Whether a departure was actually confirmed at the gate."""
        return self.exit_confirmed_at is not None

    @property
    def start_minutes(self) -> int:
        return self.start_time.hour * 60 + self.start_time.minute

    @property
    def end_minutes(self) -> int:
        return self.start_minutes + self.duration_minutes

    @property
    def end_time(self) -> dt.time:
        """Local end time. Wraps past midnight rather than clamping."""
        base = dt.datetime.combine(dt.date.min, self.start_time)
        return (base + dt.timedelta(minutes=self.duration_minutes)).time()

    @property
    def is_recurring(self) -> bool:
        return self.source == "engagement"

    def overlaps(self, other: "ScheduleItem") -> bool:
        if self.date != other.date:
            return False
        # Touching windows do not overlap: a visit ending at 12:00 and another
        # starting at 12:00 are back-to-back, which is a normal working day.
        return (
            self.start_minutes < other.end_minutes
            and other.start_minutes < self.end_minutes
        )


def _date_range(start: dt.date, end: dt.date):
    if end < start:
        raise ScheduleRangeTooWide("The end date is before the start date.")
    span = (end - start).days + 1
    if span > MAX_SCHEDULE_DAYS:
        raise ScheduleRangeTooWide(
            f"A schedule query may span at most {MAX_SCHEDULE_DAYS} days."
        )
    return [start + dt.timedelta(days=offset) for offset in range(span)]


def _engagement_queryset(**filters):
    from .models import TaskTiming

    return (
        Engagement.objects.filter(status=EngagementStatus.ACTIVE, **filters)
        .select_related(
            "worker__user", "resident__user", "resident__flat__tower", "service_type"
        )
        .prefetch_related(
            Prefetch("task_timing", queryset=TaskTiming.objects.all())
        )
    )


#: Booking statuses that belong on a schedule.
#:
#: COMPLETED is here, and its absence was half of a real bug. A worker tapped
#: "Mark as done", the server moved the booking to COMPLETED, and the card then
#: vanished from her schedule entirely — because this filter no longer matched
#: it. From where she was standing the button had deleted her job rather than
#: finished it, and the obvious response to that is to assume it failed.
#:
#: A finished visit is still a fact about the day: the household wants to see it
#: happened, the gate roster still needs her to be able to leave, and Module 6.6
#: has a "Work marked done" state that nothing could ever render while the row
#: was being filtered out from underneath it.
#:
#: The emergency statuses are deliberately *not* here. PAYMENT_PENDING and
#: BROADCAST have no worker, so there is no schedule for them to be on yet.
SCHEDULE_BOOKING_STATUSES = [
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.COMPLETED,
]


def _booking_queryset(start: dt.date, end: dt.date, **filters):
    return (
        Booking.objects.filter(
            status__in=SCHEDULE_BOOKING_STATUSES,
            scheduled_date__gte=start,
            scheduled_date__lte=end,
            **filters,
        )
        .select_related(
            "worker__user", "resident__user", "resident__flat__tower", "category"
        )
    )


def _timing_for(engagement):
    """The engagement's Module 6.2 timing, if the resident set one."""
    return getattr(engagement, "task_timing", None)


def _leave_index(engagement_ids, days: list[dt.date]) -> dict:
    """Leave rows for these engagements over this window, keyed by (id, date).

    One query for the whole range, so making the schedule leave-aware costs the
    same whether it covers a day or two months.
    """
    from .models import LeaveRequest

    engagement_ids = list(engagement_ids)
    if not engagement_ids or not days:
        return {}

    rows = (
        LeaveRequest.objects.live()
        .for_dates(days[0], days[-1])
        .filter(engagement_id__in=engagement_ids)
        .select_related("replacement__user")
    )
    return {(row.engagement_id, row.leave_date): row for row in rows}


def _completion_index(engagement_ids, booking_ids, days: list[dt.date]) -> dict:
    """Completion marks over this window, keyed by ``(source, id, date)``.

    One query for the whole range, so a month's dashboard costs the same as a
    day's.
    """
    from .models import TaskCompletion

    engagement_ids, booking_ids = list(engagement_ids), list(booking_ids)
    if not days or not (engagement_ids or booking_ids):
        return {}

    rows = TaskCompletion.objects.filter(
        models.Q(engagement_id__in=engagement_ids, visit_date__gte=days[0],
                 visit_date__lte=days[-1])
        | models.Q(booking_id__in=booking_ids)
    )

    index = {}
    for row in rows:
        if row.engagement_id:
            index[("engagement", row.engagement_id, row.visit_date)] = row
        else:
            index[("booking", row.booking_id, row.visit_date)] = row
    return index


def _attendance_index(worker_ids, days: list[dt.date]) -> dict:
    """First allowed entry and last allowed exit per worker per day.

    Read from the gate log rather than duplicated onto the visit, because the
    gate is where arrival is actually established (Module 7) and a second copy
    would eventually disagree with it. A denied or still-pending entry is not
    an arrival, so neither counts here.
    """
    from apps.attendance.models import AttendanceEvent, Decision, Direction

    worker_ids = list(worker_ids)
    if not days or not worker_ids:
        return {}

    rows = AttendanceEvent.objects.filter(
        worker_id__in=worker_ids,
        decision=Decision.ALLOWED,
        occurred_at__date__gte=days[0],
        occurred_at__date__lte=days[-1],
    ).values("worker_id", "direction", "occurred_at")

    index: dict = {}
    for row in rows:
        key = (row["worker_id"], timezone.localtime(row["occurred_at"]).date())
        slot = index.setdefault(key, {"entry": None, "exit": None})

        if row["direction"] == Direction.ENTRY:
            # Earliest arrival: a worker who passed the gate twice arrived once.
            if slot["entry"] is None or row["occurred_at"] < slot["entry"]:
                slot["entry"] = row["occurred_at"]
        elif row["direction"] == Direction.EXIT:
            # Latest departure: stepping out and back is not leaving.
            if slot["exit"] is None or row["occurred_at"] > slot["exit"]:
                slot["exit"] = row["occurred_at"]

    return index


#: A visit that was worked and marked done. The money is owed.
PAY_EARNED = "earned"
#: Still today, still open. Nothing has been decided and nothing needs to be.
PAY_NOT_YET = "not_yet"
#: The day is over and the work was never marked complete.
#:
#: **This is a flag, not a verdict.** Whether an unmarked day is paid in full,
#: pro-rated, or not paid is a policy decision with somebody's wages on the end
#: of it, and there is no defensible default to invent here — a worker may have
#: done the whole job and forgotten to press a button, or a phone may have been
#: flat, or she may genuinely not have come. The dashboard surfaces it and a
#: person decides. See ``docs/monetisation.md`` and the Section 6 status note.
PAY_UNRESOLVED = "unresolved"


def _pay_for(item_source: str, subject, completion, day: dt.date) -> tuple[int, str]:
    """What a visit is worth, and whether it has been earned yet.

    The amount comes from ``daily_rate_paise`` for a recurring engagement and
    the agreed price for a one-day booking — reusing the existing figures rather
    than introducing a second way to value a day's work.
    """
    from apps.payments.models import rupees_to_paise
    from apps.payments.services import daily_rate_paise

    if item_source == "booking":
        amount = rupees_to_paise(getattr(subject, "quoted_price", 0) or 0)
    else:
        amount = daily_rate_paise(subject)

    if completion is not None:
        return amount, PAY_EARNED
    if day < timezone.localdate():
        return amount, PAY_UNRESOLVED
    return amount, PAY_NOT_YET


def _apply_next_visit(items: list[ScheduleItem]) -> list[ScheduleItem]:
    """Fill in the gap to each worker's following visit.

    Computed after assembly, over the sorted list, so it costs one pass and no
    extra queries. A visit with nothing after it inside the requested window
    keeps ``minutes_to_next = -1`` rather than 0 — "no next visit in this
    window" and "the next visit is now" are very different things to show
    somebody.
    """
    filled: list[ScheduleItem] = []
    by_worker: dict[int, ScheduleItem] = {}

    # Backwards, so each item already knows the one that follows it.
    for item in reversed(items):
        following = by_worker.get(item.worker_id)
        if following is not None:
            start = _as_datetime(following.date, following.start_time)
            here = _as_datetime(item.date, item.end_time)
            gap = int((start - here).total_seconds() // 60)
            item = replace(
                item, minutes_to_next=max(0, gap), next_visit_at=start
            )
        by_worker[item.worker_id] = item
        filled.append(item)

    filled.reverse()
    return filled


def _as_datetime(day: dt.date, moment: dt.time) -> dt.datetime:
    naive = dt.datetime.combine(day, moment)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _visit_progress(completion, attendance) -> dict:
    """Compose the three signals into the fields a schedule item carries."""
    entry = (attendance or {}).get("entry")
    departure = (attendance or {}).get("exit")

    if completion is not None:
        status = VisitStatus.COMPLETE
    elif entry is not None:
        status = VisitStatus.IN_PROGRESS
    else:
        status = VisitStatus.PENDING

    photo = getattr(completion, "photo", None)
    return {
        "visit_status": status,
        "checked_in_at": entry,
        "completed_at": completion.completed_at if completion else None,
        "exit_confirmed_at": departure,
        "completion_note": completion.note if completion else "",
        "completion_photo_url": photo.url if photo else "",
    }


def _engagement_can_mark_done(day: dt.date, completion, leave) -> bool:
    """Whether a recurring visit is markable today.

    Not on a future day — there is nothing to have finished yet. Not twice. Not
    on a day the regular worker is away for, since the item on *her* schedule
    describes an absence rather than work; the replacement sees the same visit
    through :func:`_cover_item`, which carries no leave and is markable.
    """
    if completion is not None:
        return False
    if leave is not None:
        return False
    return day <= timezone.localdate()


def _engagement_item(
    engagement, day: dt.date, leave=None, progress=None, completion=None
) -> ScheduleItem:
    timing = _timing_for(engagement)
    cover = leave.replacement if leave is not None and leave.replacement_id else None
    pay_paise, pay_state = _pay_for("engagement", engagement, completion, day)

    return ScheduleItem(
        source="engagement",
        source_id=engagement.pk,
        date=day,
        start_time=engagement.start_time,
        duration_minutes=engagement.expected_duration_minutes,
        title=engagement.service_type.name if engagement.service_type_id else "Regular visit",
        worker_id=engagement.worker_id,
        worker_name=engagement.worker.user.get_full_name(),
        resident_id=engagement.resident_id,
        resident_name=engagement.resident.user.get_full_name(),
        flat_label=str(engagement.resident.flat),
        status=engagement.status,
        is_confirmed=True,
        expected_arrival=timing.arrival if timing else engagement.start_time,
        grace_minutes=timing.arrival_grace_minutes if timing else 0,
        task_notes=timing.task_notes if timing else "",
        on_leave=leave is not None,
        leave_status=leave.status if leave is not None else "",
        leave_request_id=leave.pk if leave is not None else 0,
        cover_worker_name=cover.user.get_full_name() if cover else "",
        pay_paise=pay_paise,
        pay_state=pay_state,
        can_mark_done=_engagement_can_mark_done(day, completion, leave),
        **(progress or {}),
    )


def _cover_item(leave) -> ScheduleItem:
    """A covered visit, as it appears on the *replacement's* own schedule.

    Built from the leave request rather than from an engagement the replacement
    has no part in. It carries the engagement's id as ``source_id`` on purpose:
    the gate (Module 7) matches an arrival against the engagement being served,
    not against whose calendar it was read from.
    """
    engagement = leave.engagement
    timing = _timing_for(engagement)

    return ScheduleItem(
        source="engagement",
        source_id=engagement.pk,
        date=leave.leave_date,
        start_time=engagement.start_time,
        duration_minutes=engagement.expected_duration_minutes,
        title=engagement.service_type.name if engagement.service_type_id else "Cover visit",
        worker_id=leave.replacement_id,
        worker_name=leave.replacement.user.get_full_name(),
        resident_id=engagement.resident_id,
        resident_name=engagement.resident.user.get_full_name(),
        flat_label=str(engagement.resident.flat),
        status=engagement.status,
        is_confirmed=True,
        expected_arrival=timing.arrival if timing else engagement.start_time,
        grace_minutes=timing.arrival_grace_minutes if timing else 0,
        task_notes=timing.task_notes if timing else "",
        leave_status=leave.status,
        leave_request_id=leave.pk,
        is_cover=True,
        covering_for_name=leave.worker.user.get_full_name(),
        # The replacement is the one actually working this visit, so she is the
        # one who gets to say it is finished.
        can_mark_done=leave.leave_date <= timezone.localdate(),
    )


def _cover_items(days: list[dt.date], **filters) -> list[ScheduleItem]:
    """Confirmed cover visits matching ``filters`` over the window."""
    from .models import LeaveRequest, LeaveStatus

    if not days:
        return []

    rows = (
        LeaveRequest.objects.filter(
            status=LeaveStatus.REPLACEMENT_CONFIRMED,
            leave_date__gte=days[0],
            leave_date__lte=days[-1],
            **filters,
        )
        .select_related(
            "engagement__service_type",
            "engagement__resident__user",
            "engagement__resident__flat__tower",
            "replacement__user",
            "worker__user",
        )
        .prefetch_related("engagement__task_timing")
    )
    return [_cover_item(row) for row in rows]


def _booking_item(booking, progress=None, completion=None) -> ScheduleItem:
    # Computed once. It used to be called twice, one call per tuple element,
    # which meant two payments imports and two rate lookups per booking on every
    # schedule read.
    pay_paise, pay_state = _pay_for(
        "booking", booking, completion, booking.scheduled_date
    )
    return ScheduleItem(
        source="booking",
        source_id=booking.pk,
        date=booking.scheduled_date,
        start_time=booking.start_time,
        duration_minutes=booking.expected_duration_minutes,
        title=booking.category.name if booking.category_id else "One-day booking",
        worker_id=booking.worker_id,
        worker_name=booking.worker.user.get_full_name(),
        resident_id=booking.resident_id,
        resident_name=booking.resident.user.get_full_name(),
        flat_label=str(booking.resident.flat),
        status=booking.status,
        # A pending booking is on the calendar but not yet agreed — it still
        # blocks the slot, and the worker needs to see that it needs answering.
        is_confirmed=booking.status == BookingStatus.CONFIRMED,
        can_respond=booking.is_actionable,
        expected_arrival=booking.start_time,
        task_notes=booking.notes,
        pay_paise=pay_paise,
        pay_state=pay_state,
        # Straight off the booking, so the schedule card and the completion
        # endpoint answer to the same rule. See ``Booking.can_be_completed``.
        can_mark_done=completion is None and booking.can_be_completed,
        settlement="cash" if booking.is_emergency else "app",
        **(progress or {}),
    )


def _assemble(
    engagements, bookings, days: list[dt.date], *, extra: list[ScheduleItem] | None = None
) -> list[ScheduleItem]:
    """Expand recurring engagements over ``days`` and merge in the bookings."""
    engagements = list(engagements)
    bookings = list(bookings)
    leave = _leave_index([e.pk for e in engagements], days)

    # Module 6.6 — two more batched lookups, both constant in the range.
    completions = _completion_index(
        [e.pk for e in engagements], [b.pk for b in bookings], days
    )
    attendance = _attendance_index(
        {e.worker_id for e in engagements} | {b.worker_id for b in bookings}, days
    )

    items: list[ScheduleItem] = []

    for engagement in engagements:
        for day in days:
            if engagement.occurs_on(day):
                items.append(
                    _engagement_item(
                        engagement,
                        day,
                        leave.get((engagement.pk, day)),
                        _visit_progress(
                            completions.get(("engagement", engagement.pk, day)),
                            attendance.get((engagement.worker_id, day)),
                        ),
                        completions.get(("engagement", engagement.pk, day)),
                    )
                )

    items.extend(
        _booking_item(
            booking,
            _visit_progress(
                completions.get(("booking", booking.pk, booking.scheduled_date)),
                # Attendance is recorded per worker per *day*, not per visit —
                # the gate knows somebody came in, not which of their three jobs
                # they came in for. Attributing it to a booking the worker has
                # not even accepted yet produced a card that read "Awaiting your
                # confirmation" and "In progress" at the same time, which is not
                # a state anybody can act on. An unanswered request has no
                # progress to report, so it reports none.
                attendance.get((booking.worker_id, booking.scheduled_date))
                if booking.status != BookingStatus.PENDING
                else None,
            ),
            completions.get(("booking", booking.pk, booking.scheduled_date)),
        )
        for booking in bookings
    )
    if extra:
        items.extend(extra)

    # Chronological, with a stable tie-break so pagination and diffing behave.
    items.sort(key=lambda item: (item.date, item.start_minutes, item.source, item.source_id))

    # 6.7 — one pass over the sorted list, no extra queries.
    return _apply_next_visit(items)


def worker_schedule(worker_id, start: dt.date, end: dt.date) -> list[ScheduleItem]:
    """Module 6.1 — everything one worker is expected at, across both systems.

    Two queries regardless of how many days the range covers.
    """
    days = _date_range(start, end)
    return _assemble(
        _engagement_queryset(worker_id=worker_id),
        _booking_queryset(start, end, worker_id=worker_id),
        days,
        # Days this worker agreed to cover for somebody else. They belong on the
        # worker's own schedule and on nobody else's engagement list, which is
        # why they arrive here rather than through the engagement queryset.
        extra=_cover_items(days, replacement_id=worker_id),
    )


def resident_schedule(resident_id, start: dt.date, end: dt.date) -> list[ScheduleItem]:
    """The same view from the household's side — who is coming, and when."""
    days = _date_range(start, end)
    return _assemble(
        _engagement_queryset(resident_id=resident_id),
        _booking_queryset(start, end, resident_id=resident_id),
        days,
    )


def society_schedule(society_id, start: dt.date, end: dt.date) -> list[ScheduleItem]:
    """Every expected visit in a society. Feeds the gate roster in Module 7."""
    days = _date_range(start, end)
    return _assemble(
        _engagement_queryset(society_id=society_id),
        _booking_queryset(start, end, society_id=society_id),
        days,
        # The gate roster is built from this. A replacement who is not on it
        # arrives to a guard with no record of them and is turned away from a
        # visit the household is expecting — so cover visits are first-class
        # here, alongside the original visit they stand in for.
        extra=_cover_items(days, society_id=society_id),
    )


def worker_day(worker_id, day: dt.date) -> list[ScheduleItem]:
    """One worker, one day — what the app opens on."""
    return worker_schedule(worker_id, day, day)


def find_overlaps(items: list[ScheduleItem]) -> list[tuple[ScheduleItem, ScheduleItem]]:
    """Every colliding pair in an already-sorted schedule.

    Used to surface a double-booking that already exists — Module 6.3 prevents
    new ones, but data predating the check, or created through the admin, still
    has to be visible rather than silently wrong.

    Linear rather than quadratic: the list is sorted by start time, so a pair
    can only collide with what it overlaps in a forward scan.
    """
    clashes: list[tuple[ScheduleItem, ScheduleItem]] = []

    for index, item in enumerate(items):
        for other in items[index + 1 :]:
            if other.date != item.date or other.start_minutes >= item.end_minutes:
                # Sorted, so nothing further can overlap this item.
                break
            clashes.append((item, other))

    return clashes
