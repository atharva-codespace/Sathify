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


def _engagement_item(engagement, day: dt.date) -> ScheduleItem:
    timing = _timing_for(engagement)
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
    )


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


def _assemble(engagements, bookings, days: list[dt.date]) -> list[ScheduleItem]:
    """Expand recurring engagements over ``days`` and merge in the bookings."""
    items: list[ScheduleItem] = []

    for engagement in engagements:
        for day in days:
            if engagement.occurs_on(day):
                items.append(_engagement_item(engagement, day))

    items.extend(_booking_item(booking) for booking in bookings)

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
