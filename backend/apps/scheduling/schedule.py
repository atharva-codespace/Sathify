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
from dataclasses import dataclass

from django.db.models import Prefetch

from apps.bookings.models import Booking, BookingStatus
from apps.hiring.models import Engagement, EngagementStatus

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


def _booking_queryset(start: dt.date, end: dt.date, **filters):
    return (
        Booking.objects.filter(
            status__in=[BookingStatus.PENDING, BookingStatus.CONFIRMED],
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


def _engagement_item(engagement, day: dt.date, leave=None) -> ScheduleItem:
    timing = _timing_for(engagement)
    cover = leave.replacement if leave is not None and leave.replacement_id else None

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


def _booking_item(booking) -> ScheduleItem:
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
        expected_arrival=booking.start_time,
        task_notes=booking.notes,
    )


def _assemble(
    engagements, bookings, days: list[dt.date], *, extra: list[ScheduleItem] | None = None
) -> list[ScheduleItem]:
    """Expand recurring engagements over ``days`` and merge in the bookings."""
    engagements = list(engagements)
    leave = _leave_index([e.pk for e in engagements], days)

    items: list[ScheduleItem] = []

    for engagement in engagements:
        for day in days:
            if engagement.occurs_on(day):
                items.append(
                    _engagement_item(engagement, day, leave.get((engagement.pk, day)))
                )

    items.extend(_booking_item(booking) for booking in bookings)
    if extra:
        items.extend(extra)

    # Chronological, with a stable tie-break so pagination and diffing behave.
    items.sort(key=lambda item: (item.date, item.start_minutes, item.source, item.source_id))
    return items


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
