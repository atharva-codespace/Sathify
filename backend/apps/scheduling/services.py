"""
Module 6.3 and 6.4 — conflict detection and reminder scheduling.

-------------------------------------------------------------------------------
CONFLICT DETECTION LIVES HERE, NOT IN MODULE 5
-------------------------------------------------------------------------------
Module 5 shipped with its own overlap check because it was the first module that
needed one. Modspec 6.3 assigns it to the calendar layer, and it now belongs to
exactly one module: ``apps.bookings.services`` delegates to
:func:`conflicted_worker_ids`. Two implementations of "is this worker already
busy" would eventually disagree, and the losing side of that disagreement is a
worker told to be in two places at once.

The batched form is the primitive rather than a convenience wrapper, because the
performance-critical caller is Module 5.3's matching, which asks about a whole
page of candidates at once. The single-worker form is built on top of it.

-------------------------------------------------------------------------------
WHY THE WEEKDAY MATCH IS IN PYTHON
-------------------------------------------------------------------------------
An engagement's recurring days live in a ``JSONField``, and the ``__contains``
lookup that would filter them in SQL is unsupported on SQLite — which is what
development and the whole test suite run on, while production is PostgreSQL. A
query that passes CI and fails in production is the worse trade, so the weekday
match happens in Python over a bounded, batched result set.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.db import models, transaction
from django.utils import timezone

from apps.bookings.models import Booking, combine_local, minutes_of, windows_overlap
from apps.hiring.models import Engagement, weekday_of
from apps.payments.models import format_paise

from .models import LeaveRequest, Reminder, ReminderKind, ReminderStatus, TaskTiming
from .schedule import ScheduleItem, worker_day

logger = logging.getLogger(__name__)

#: How far ahead reminders are generated. Two days keeps the table small while
#: comfortably covering the default one-hour lead time.
REMINDER_HORIZON_DAYS = 2


# ---------------------------------------------------------------------------
# 6.3 Conflict detection
# ---------------------------------------------------------------------------


def conflicted_worker_ids(
    worker_ids,
    *,
    on_date: dt.date,
    start_minutes: int,
    duration_minutes: int,
    exclude_booking_id=None,
) -> set:
    """Which of ``worker_ids`` are already committed over the requested window.

    Four sources of conflict, all of which must be honoured or the platform
    will cheerfully double-book someone:

    * another live booking that day,
    * an active engagement whose recurring visit falls on that weekday,
    * a day the worker has taken off (Module 6.5), and
    * a visit the worker has agreed to *cover* for somebody else that day.

    ---------------------------------------------------------------------
    WHY LEAVE IS HERE AND NOT ONLY ON THE CALENDAR
    ---------------------------------------------------------------------
    Leave used to be invisible to this function, which is the one place every
    module asks "is this worker busy". A worker who had taken the day off was
    therefore still offered to residents searching that date, and could be
    booked into a day she had already said she could not work.

    It is scoped to **the hours of the visit the leave is about**, not to the
    whole day. Leave belongs to one engagement — a worker with three
    households who takes Tuesday off from one of them is still working the
    other two, and blocking her whole Tuesday would cost her two days' work to
    record one absence. Both sides of the row are read the same way: the
    worker who is away, and the worker standing in for her, are each committed
    only for that visit's window.

    Within the window the leave is deliberately still honoured even though the
    engagement itself already occupies those hours. The two say different
    things — the engagement is what was agreed, the leave is the worker's own
    statement that she cannot work then — and that statement should not depend
    on a different branch of this function happening to cover it, nor stop
    holding if the engagement is later paused.

    The absence is per worker, so it removes **only** the worker who is away.
    Every other worker in the pool is untouched — which is the difference
    between "Sunita is away that morning" and "nobody is free that day".

    Batched: four queries regardless of pool size.
    """
    worker_ids = list(worker_ids)
    if not worker_ids:
        return set()

    requested = set(worker_ids)
    conflicted: set = set()

    bookings = Booking.objects.live().filter(
        worker_id__in=worker_ids, scheduled_date=on_date
    )
    if exclude_booking_id is not None:
        bookings = bookings.exclude(pk=exclude_booking_id)

    for booking in bookings.only("worker_id", "start_time", "expected_duration_minutes"):
        if windows_overlap(
            start_minutes,
            duration_minutes,
            booking.start_minutes,
            booking.expected_duration_minutes,
        ):
            conflicted.add(booking.worker_id)

    weekday = weekday_of(on_date)
    engagements = Engagement.objects.active().filter(worker_id__in=worker_ids)

    for engagement in engagements.only(
        "worker_id", "days_of_week", "start_time", "expected_duration_minutes"
    ):
        if weekday not in set(engagement.days_of_week):
            continue
        if windows_overlap(
            start_minutes,
            duration_minutes,
            minutes_of(engagement.start_time),
            engagement.expected_duration_minutes,
        ):
            conflicted.add(engagement.worker_id)

    # --- 6.5 leave, and the cover somebody else agreed to work --------------
    #
    # One query answers both, because they are the same row seen from the two
    # ends: the worker who is away, and the worker standing in for them.
    # ``live()`` excludes withdrawn leave — a worker who changed her mind and
    # is coming after all is available, and must not stay blocked out.
    leave_rows = (
        LeaveRequest.objects.live()
        .filter(leave_date=on_date)
        .filter(
            models.Q(worker_id__in=worker_ids)
            | models.Q(replacement_id__in=worker_ids)
        )
        .select_related("engagement")
    )

    for leave in leave_rows:
        # One window governs both sides of the row: the visit that is not
        # being worked by its usual worker. Outside it, neither of them is
        # committed by this leave and both stay bookable.
        if not windows_overlap(
            start_minutes,
            duration_minutes,
            minutes_of(leave.engagement.start_time),
            leave.engagement.expected_duration_minutes,
        ):
            continue

        if leave.worker_id in requested:
            conflicted.add(leave.worker_id)
        if leave.replacement_id in requested:
            conflicted.add(leave.replacement_id)

    return conflicted


@dataclass(frozen=True)
class ConflictReport:
    """What a proposed visit would collide with.

    Carries the colliding items, not just a boolean, because modspec 6.3 allows
    a conflict to be "flagged for manual resolution" as well as rejected — and
    an administrator cannot resolve what they cannot see.
    """

    has_conflict: bool
    clashes: tuple[ScheduleItem, ...] = ()

    @property
    def summary(self) -> str:
        if not self.has_conflict:
            return "No conflicts."
        parts = [
            f"{item.title} at {item.start_time:%H:%M}–{item.end_time:%H:%M} "
            f"({item.flat_label})"
            for item in self.clashes
        ]
        return "Already committed: " + "; ".join(parts)


def check_conflict(
    worker_id,
    *,
    on_date: dt.date,
    start_time: dt.time,
    duration_minutes: int,
    exclude_booking_id=None,
) -> ConflictReport:
    """Module 6.3 — would this visit collide with anything already scheduled?

    Built on the assembled day schedule rather than on raw queries, so what it
    reports is exactly what the worker sees on their calendar.
    """
    start = minutes_of(start_time)
    end = start + duration_minutes

    clashes = [
        item
        for item in worker_day(worker_id, on_date)
        if not (
            exclude_booking_id is not None
            and item.source == "booking"
            and item.source_id == exclude_booking_id
        )
        # A visit that is already finished is not a reason to refuse new work.
        # The day schedule carries completed visits so the worker can still see
        # them (and the gate can still let her out); they are history, not a
        # commitment, and treating them as a clash would make a productive
        # morning into a reason nobody can book her that afternoon.
        and not item.is_complete
        and item.start_minutes < end
        and start < item.end_minutes
    ]

    return ConflictReport(has_conflict=bool(clashes), clashes=tuple(clashes))


# ---------------------------------------------------------------------------
# 6.2 Task timing
# ---------------------------------------------------------------------------


def effective_timing(engagement) -> dict:
    """The arrival and departure expectations in force for an engagement.

    Always answers, whether or not the resident has set a :class:`TaskTiming`.
    Every consumer — attendance, reminders, the app — should come through here
    rather than branching on ``None`` themselves, so "no timing set" means the
    same thing everywhere.
    """
    timing = getattr(engagement, "task_timing", None)

    if timing is None:
        base = dt.datetime.combine(dt.date.min, engagement.start_time)
        departure = (
            base + dt.timedelta(minutes=engagement.expected_duration_minutes)
        ).time()
        return {
            "expected_arrival": engagement.start_time,
            "arrival_grace_minutes": 0,
            "expected_departure": departure,
            "departure_grace_minutes": 0,
            "task_notes": "",
            "reminders_enabled": True,
            "reminder_lead_minutes": 60,
            "is_customised": False,
        }

    return {
        "expected_arrival": timing.arrival,
        "arrival_grace_minutes": timing.arrival_grace_minutes,
        "expected_departure": timing.departure,
        "departure_grace_minutes": timing.departure_grace_minutes,
        "task_notes": timing.task_notes,
        "reminders_enabled": timing.reminders_enabled,
        "reminder_lead_minutes": timing.reminder_lead_minutes,
        "is_customised": True,
    }


# ---------------------------------------------------------------------------
# 6.4 Reminders
# ---------------------------------------------------------------------------


def _reminder_lead_minutes(item: ScheduleItem) -> int | None:
    """Lead time for an item, or ``None`` when reminders are switched off."""
    if item.source != "engagement":
        return 60  # Bookings use the default; they have no TaskTiming.

    timing = TaskTiming.objects.filter(engagement_id=item.source_id).first()
    if timing is None:
        return 60
    return timing.reminder_lead_minutes if timing.reminders_enabled else None


@transaction.atomic
def ensure_reminders_for_worker(worker, *, days_ahead: int = REMINDER_HORIZON_DAYS) -> int:
    """Queue reminders for a worker's upcoming visits (Module 6.4).

    Idempotent: ``get_or_create`` on (recipient, kind, event_at) means calling
    this on every schedule read cannot produce duplicates. That is what lets
    generation be lazy, which it has to be — there is no scheduler on the free
    tier to generate them on a timer.

    Returns how many were newly created.
    """
    today = timezone.localdate()
    items = worker_day(worker.pk, today)
    for offset in range(1, days_ahead + 1):
        items += worker_day(worker.pk, today + dt.timedelta(days=offset))

    created = 0
    now = timezone.now()

    for item in items:
        lead = _reminder_lead_minutes(item)
        if lead is None:
            continue

        event_at = combine_local(item.date, item.expected_arrival or item.start_time)
        if event_at <= now:
            # Already happened or happening — a reminder now would only confuse.
            continue

        kind = (
            ReminderKind.UPCOMING_ENGAGEMENT
            if item.source == "engagement"
            else ReminderKind.UPCOMING_BOOKING
        )

        _, was_created = Reminder.objects.get_or_create(
            recipient=worker.user,
            kind=kind,
            event_at=event_at,
            defaults={
                "society_id": worker.user.society_id,
                "engagement_id": item.source_id if item.source == "engagement" else None,
                "booking_id": item.source_id if item.source == "booking" else None,
                "send_after": event_at - dt.timedelta(minutes=lead),
                "title": f"{item.title} at {item.flat_label}",
                "body": (
                    f"You are expected at {item.flat_label} at "
                    f"{item.start_time:%H:%M} today."
                ),
            },
        )
        created += int(was_created)

    if created:
        logger.info("Queued %s reminder(s) for worker %s", created, worker.pk)
    return created


def due_reminders(*, society_id=None, recipient=None):
    """Reminders ready for Module 10 to deliver.

    Stale ones are swept first: a reminder about a visit that has already
    happened is worse than no reminder, so it is cancelled rather than sent late.
    """
    stale = Reminder.objects.stale()
    if society_id is not None:
        stale = stale.filter(society_id=society_id)
    swept = stale.update(status=ReminderStatus.CANCELLED, updated_at=timezone.now())
    if swept:
        logger.info("Cancelled %s stale reminder(s)", swept)

    queryset = Reminder.objects.due().select_related("recipient")
    if society_id is not None:
        queryset = queryset.filter(society_id=society_id)
    if recipient is not None:
        queryset = queryset.filter(recipient=recipient)

    return queryset


# ---------------------------------------------------------------------------
# 6.5 Urgent leave ("chutti")
# ---------------------------------------------------------------------------
#
# The flow, end to end:
#
#   worker asks ──► APPROVED (instantly, no review) ──► resident is told
#                                                            │
#                              ┌─────────────────────────────┴──────┐
#                    "I'll manage"                        "send someone"
#                              │                                    │
#                           WAIVED                    REPLACEMENT_REQUESTED
#                                                                   │
#                                              ┌────────────────────┴────────┐
#                                     someone assigned            nobody, day passed
#                                              │                             │
#                                  REPLACEMENT_CONFIRMED                 UNFILLED
#                                              │
#                                    replacement is paid
#
# Every terminal state settles. WAIVED and UNFILLED both mean nobody came, and
# they are kept apart on purpose: the first is a household that chose to manage,
# the second is the platform failing to deliver, and only the second is worth
# measuring.


class LeaveError(Exception):
    """Base class for the leave workflow's refusals."""

    code = "leave_error"


class LeaveNotActionable(LeaveError):
    """The request is not in a state where this step makes sense."""

    code = "leave_not_actionable"


class DuplicateLeave(LeaveError):
    """Leave already exists for this engagement on this date."""

    code = "duplicate_leave"


class LeaveDateInvalid(LeaveError):
    """The date is in the past, or the engagement does not call for a visit."""

    code = "leave_date_invalid"


#: How far ahead leave may be requested. Urgent leave is for tomorrow, not for
#: next quarter — a month's notice is a conversation about the engagement, not an
#: absence, and modelling it here would put planned time off into a workflow
#: designed around emergencies.
MAX_LEAVE_LEAD_DAYS = 14


def _notify_leave(recipient, *, title: str, body: str, leave, society=None) -> None:
    """Tell someone about a leave request. Never raises — see Module 10."""
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    notify(
        recipient=recipient,
        category=NotificationCategory.URGENT_LEAVE,
        title=title,
        body=body,
        # "/schedule" is a route the app actually has (see Routes in
        # mobile/lib/core/routing/app_router.dart). A route the client cannot
        # match is a crash on tapping the notification.
        data={"route": "/schedule", "leave_request": leave.pk},
        society=society or leave.society,
    )


@transaction.atomic
def request_leave(engagement, *, leave_date: dt.date, reason: str = "", by=None):
    """Module 6.5 — a worker takes a day off. Approved on creation, always.

    Raises :class:`DuplicateLeave` rather than creating a second row, which is
    what makes a double-tap on a bad connection safe: the same request twice is
    the same absence, not two of them.
    """
    from .models import LeaveRequest, LeaveStatus

    today = timezone.localdate()
    if leave_date < today:
        raise LeaveDateInvalid("Leave cannot be taken for a day that has passed.")
    if (leave_date - today).days > MAX_LEAVE_LEAD_DAYS:
        raise LeaveDateInvalid(
            f"Leave can be requested up to {MAX_LEAVE_LEAD_DAYS} days ahead. "
            "For anything longer, talk to the household directly."
        )
    if not engagement.occurs_on(leave_date):
        raise LeaveDateInvalid(
            "This engagement does not call for a visit on that day, so there is "
            "nothing to take leave from."
        )

    existing = LeaveRequest.objects.filter(
        engagement=engagement, leave_date=leave_date
    ).first()
    if existing is not None:
        if existing.status == LeaveStatus.WITHDRAWN:
            # A withdrawn day may be taken again — the worker changed their mind
            # twice, which is allowed and is not worth a new row.
            existing.status = LeaveStatus.APPROVED
            existing.reason = reason[:200]
            existing.resident_responded_at = None
            existing.save(
                update_fields=["status", "reason", "resident_responded_at", "updated_at"]
            )
            leave = existing
        else:
            raise DuplicateLeave("Leave is already recorded for that day.")
    else:
        leave = LeaveRequest.objects.create(
            society=engagement.society,
            engagement=engagement,
            worker=engagement.worker,
            leave_date=leave_date,
            reason=reason[:200],
        )

    worker_name = engagement.worker.user.get_full_name()
    _notify_leave(
        engagement.resident.user,
        title=f"{worker_name} cannot come on {leave_date:%d %b}",
        body=(
            f"{reason.strip() or 'They have asked for the day off.'} "
            "Tap to say whether you need someone else that day."
        ),
        leave=leave,
    )
    logger.info(
        "Leave requested: engagement=%s worker=%s date=%s",
        engagement.pk, engagement.worker_id, leave_date,
    )
    return leave


@transaction.atomic
def respond_to_leave(leave, *, needs_replacement: bool, by=None):
    """The household's answer: do you need somebody else that day?

    Idempotent for the same answer, so a retried tap does not reopen a settled
    day. Changing the answer is allowed while nobody has been assigned.
    """
    from .models import LeaveStatus

    if leave.status in {LeaveStatus.REPLACEMENT_CONFIRMED, LeaveStatus.WITHDRAWN}:
        raise LeaveNotActionable(
            "This day has already been settled and cannot be changed."
        )

    leave.status = (
        LeaveStatus.REPLACEMENT_REQUESTED if needs_replacement else LeaveStatus.WAIVED
    )
    leave.resident_responded_at = timezone.now()
    leave.save(update_fields=["status", "resident_responded_at", "updated_at"])

    if not needs_replacement:
        # Nothing more will happen to this day, so settle it now rather than
        # leaving a row that looks unfinished forever.
        settle_leave(leave)
        _notify_leave(
            leave.worker.user,
            title="Your leave is confirmed",
            body=(
                f"The household does not need cover on {leave.leave_date:%d %b}. "
                "Enjoy your day."
            ),
            leave=leave,
        )
    else:
        _notify_leave(
            leave.worker.user,
            title="Your leave is confirmed",
            body=(
                f"The household is arranging cover for {leave.leave_date:%d %b}. "
                "That day will not be counted as attended."
            ),
            leave=leave,
        )

    return leave


def replacement_candidates(leave, *, limit: int = 10):
    """Workers who could cover this visit, best first.

    Reuses Module 4.3's scorer rather than inventing a second notion of "good
    match" — a worker the platform ranks highly for a hire is the same worker it
    should suggest for a day of cover. The only additions are the two conditions
    specific to cover: they must be free at that hour, and they must not be the
    person who is away.
    """
    from apps.hiring.services import rank_workers, searchable_workers

    engagement = leave.engagement
    pool = searchable_workers(leave.society_id).exclude(pk=leave.worker_id)

    if engagement.service_type_id:
        # Somebody who cooks is not cover for somebody who cleans.
        pool = pool.filter(service_types__id=engagement.service_type_id)

    pool = list(pool.distinct()[: max(limit * 4, 40)])
    if not pool:
        return []

    busy = conflicted_worker_ids(
        [w.pk for w in pool],
        on_date=leave.leave_date,
        start_minutes=minutes_of(engagement.start_time),
        duration_minutes=engagement.expected_duration_minutes,
    )
    free = [w for w in pool if w.pk not in busy]
    if not free:
        return []

    ranked = rank_workers(free, resident_society=engagement.society)
    return ranked[:limit]


@transaction.atomic
def assign_replacement(leave, replacement, *, by=None):
    """Confirm who is covering, and settle the money in the same transaction.

    Settling here rather than on the day is deliberate: the replacement agreed to
    work on the strength of a stated amount, and that amount should not be able
    to move afterwards because the month's rate was recalculated.
    """
    from .models import LeaveStatus

    if leave.status not in {LeaveStatus.APPROVED, LeaveStatus.REPLACEMENT_REQUESTED}:
        raise LeaveNotActionable(
            "A replacement can only be assigned while cover is still being sought."
        )
    if replacement.pk == leave.worker_id:
        raise LeaveNotActionable("A worker cannot cover their own absence.")
    if replacement.user.society_id != leave.society_id:
        raise LeaveNotActionable("A replacement must belong to the same society.")

    busy = conflicted_worker_ids(
        [replacement.pk],
        on_date=leave.leave_date,
        start_minutes=minutes_of(leave.engagement.start_time),
        duration_minutes=leave.engagement.expected_duration_minutes,
    )
    if busy:
        raise LeaveNotActionable(
            f"{replacement.user.get_full_name()} is already booked at that time."
        )

    leave.replacement = replacement
    leave.status = LeaveStatus.REPLACEMENT_CONFIRMED
    leave.replacement_confirmed_at = timezone.now()
    leave.save(
        update_fields=[
            "replacement", "status", "replacement_confirmed_at", "updated_at",
        ]
    )

    settle_leave(leave)

    _notify_leave(
        replacement.user,
        title=f"You are covering a visit on {leave.leave_date:%d %b}",
        body=(
            f"{leave.engagement.resident.flat} at "
            f"{leave.engagement.start_time:%H:%M}. "
            f"You will be paid {format_paise(leave.replacement_paise)}."
        ),
        leave=leave,
    )
    _notify_leave(
        leave.engagement.resident.user,
        title="Cover arranged",
        body=(
            f"{replacement.user.get_full_name()} will come on "
            f"{leave.leave_date:%d %b}."
        ),
        leave=leave,
    )
    _notify_leave(
        leave.worker.user,
        title="Cover arranged for your leave",
        body=(
            f"{replacement.user.get_full_name()} is covering "
            f"{leave.leave_date:%d %b}."
        ),
        leave=leave,
    )
    return leave


@transaction.atomic
def withdraw_leave(leave, *, by=None):
    """The worker can come after all.

    Refused once a replacement is confirmed: somebody else has rearranged their
    day around it, and cancelling them by surprise is the same harm this module
    exists to prevent, pointed the other way.
    """
    from .models import LeaveStatus

    if not leave.can_withdraw:
        raise LeaveNotActionable(
            "This leave can no longer be withdrawn — cover has already been "
            "arranged. Please speak to the household."
        )

    leave.status = LeaveStatus.WITHDRAWN
    leave.save(update_fields=["status", "updated_at"])

    _notify_leave(
        leave.engagement.resident.user,
        title=f"{leave.worker.user.get_full_name()} can come after all",
        body=f"Their leave for {leave.leave_date:%d %b} has been withdrawn.",
        leave=leave,
    )
    return leave


@transaction.atomic
def settle_leave(leave):
    """Work out and record the money for one day of leave. Idempotent.

    ---------------------------------------------------------------------------
    WHY THERE IS NO DEDUCTION HERE
    ---------------------------------------------------------------------------
    The obvious implementation subtracts a day's pay from the absent worker's
    salary. It would be wrong, and wrong in the direction that costs a worker
    money: ``payments.services.salary_basis`` already pro-rates the month by
    *attended* visits taken from the gate log. A day not worked is already a day
    not paid. Deducting again here would dock the same absence twice.

    So this records the **transfer** — what the replacement is owed, which is
    exactly what the original worker forgoes — and freezes the arithmetic that
    produced it. The default rule sends the whole day to whoever worked it, and
    the two halves then net out exactly against the attendance pro-rating.

    Where an engagement carries a ``ReplacementSplit`` below 100%, the original
    worker is meant to keep a share of a day they did not work. Attendance
    pro-rating cannot express that, so ``forgone_paise`` records the difference
    for the receipt and a person applies it. That seam is deliberate and narrow;
    silently paying the wrong number would not be.
    """
    from apps.payments.models import PaymentKind
    from apps.payments.services import create_payment, daily_rate_paise, split_for_replacement

    from .models import SETTLEABLE_LEAVE_STATUSES, LeaveStatus

    if leave.is_settled or leave.status not in SETTLEABLE_LEAVE_STATUSES:
        return leave

    rate = daily_rate_paise(leave.engagement)
    to_replacement = 0
    forgone = rate

    if leave.status == LeaveStatus.REPLACEMENT_CONFIRMED and leave.replacement_id:
        to_replacement, retained = split_for_replacement(
            leave.engagement, day_rate_paise=rate
        )
        forgone = rate - retained

        if to_replacement > 0:
            create_payment(
                resident=leave.engagement.resident,
                worker=leave.replacement,
                society=leave.society,
                kind=PaymentKind.REPLACEMENT,
                amount_paise=to_replacement,
                engagement=leave.engagement,
                note=(
                    f"Covering {leave.worker.user.get_full_name()} on "
                    f"{leave.leave_date:%d %b %Y}"
                ),
            )

    leave.day_rate_paise = rate
    leave.forgone_paise = forgone
    leave.replacement_paise = to_replacement
    leave.settled_at = timezone.now()
    leave.save(
        update_fields=[
            "day_rate_paise", "forgone_paise", "replacement_paise",
            "settled_at", "updated_at",
        ]
    )
    logger.info(
        "Leave %s settled: day_rate=%s to_replacement=%s status=%s",
        leave.pk, rate, to_replacement, leave.status,
    )
    return leave


def close_lapsed_leave(*, today: dt.date | None = None) -> int:
    """Mark unanswered cover requests as unfilled once the day has passed.

    There is no scheduler on the free tier (docs/free-tier-constraints.md §7),
    so this is idempotent and cheap enough to call from a read path — the leave
    list view does exactly that. Returns how many were closed.
    """
    from .models import LeaveRequest, LeaveStatus

    today = today or timezone.localdate()
    lapsed = LeaveRequest.objects.filter(
        status__in=[LeaveStatus.APPROVED, LeaveStatus.REPLACEMENT_REQUESTED],
        leave_date__lt=today,
    )

    closed = 0
    for leave in lapsed:
        leave.status = LeaveStatus.UNFILLED
        leave.save(update_fields=["status", "updated_at"])
        settle_leave(leave)
        closed += 1

    if closed:
        logger.info("Closed %s lapsed leave request(s) as unfilled", closed)
    return closed


# ---------------------------------------------------------------------------
# 6.6 Marking a day's work done
# ---------------------------------------------------------------------------
#
#   resident requests ──► worker accepts ──► [Module 4/5, already built]
#            │
#            ▼
#   gate ENTRY logged  ──────────────────►  IN_PROGRESS   (Module 7, reused)
#            │
#            ▼
#   worker marks the task done  ─────────►  COMPLETE      (this section)
#            │
#            ▼
#   guard scans them out (Direction.EXIT) ► departure confirmed
#                                            (Module 7 again — the same scan
#                                             screen, the other direction)
#
# Only the middle step is new. The two gate steps already exist and are reused
# rather than reimplemented: a second check-in mechanism would eventually
# disagree with the first about whether somebody was at work.


class VisitNotFound(LeaveError):
    """No such visit on that date for this worker."""

    code = "visit_not_found"


@transaction.atomic
def mark_task_complete(
    *, worker, visit_date: dt.date, engagement=None, booking=None, note: str = "",
    photo=None,
):
    """The worker says the day's work is done. Idempotent.

    Deliberately **not** conditional on a check-in having been recorded. A
    broken gate scanner, a guard on a break, or a GPS fix that would not settle
    are none of them the worker's fault, and none should be able to stop her
    saying she finished the job. The household sees arrival and completion as
    separate facts and can ask about a mismatch.

    Re-marking returns the existing row rather than moving the timestamp: the
    completion time is evidence, and a double tap on a bad connection must not
    quietly rewrite it.
    """
    from .models import TaskCompletion

    if (engagement is None) == (booking is None):
        raise VisitNotFound("A completion belongs to exactly one visit.")

    if engagement is not None:
        if not engagement.occurs_on(visit_date):
            raise VisitNotFound(
                "This engagement does not call for a visit on that day."
            )
        society = engagement.society
        lookup = {"engagement": engagement, "visit_date": visit_date}
    else:
        society = booking.society
        visit_date = booking.scheduled_date
        lookup = {"booking": booking, "visit_date": visit_date}

    completion, created = TaskCompletion.objects.get_or_create(
        **lookup,
        defaults={
            "society": society,
            "worker": worker,
            "note": note[:300],
            "completed_at": timezone.now(),
        },
    )

    if created:
        # A photo is attached after creation so the unique constraint decides
        # first — otherwise a retry uploads a second copy of the same image
        # before discovering the row already exists.
        if photo is not None:
            completion.photo = photo
            completion.save(update_fields=["photo", "updated_at"])

        if booking is not None:
            # A TaskCompletion alone makes the *schedule* say "done" while the
            # booking sits at CONFIRMED — and Module 8 refuses to open a
            # payment until the booking itself reads COMPLETED. Marking a job
            # done therefore has to move both, or the worker finishes, the
            # household is told, and the money is silently unreachable.
            _settle_booking_status(booking)

        _notify_completion(completion)
        logger.info(
            "Task marked complete: worker=%s date=%s engagement=%s booking=%s",
            worker.pk, visit_date, getattr(engagement, "pk", None),
            getattr(booking, "pk", None),
        )

    return completion


def _settle_booking_status(booking) -> None:
    """Move a booking to COMPLETED alongside its completion mark.

    An already-completed booking is left alone rather than treated as an error:
    :func:`mark_task_complete` is idempotent by design, and the two rows can
    legitimately be settled by two different routes (here, or the resident
    closing it out from the booking screen).

    ``complete_booking`` decides whether the move is allowed — it owns
    ``Booking.can_be_completed``, and duplicating that test here is how the two
    came to disagree about emergency bookings in the first place.
    """
    from apps.bookings.models import BookingStatus
    from apps.bookings.services import complete_booking

    if booking.status == BookingStatus.COMPLETED:
        return
    complete_booking(booking)


def _notify_completion(completion) -> None:
    """Tell the household. Close to real-time is the point of this step."""
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    engagement = completion.engagement or completion.booking
    resident = getattr(engagement, "resident", None)
    if resident is None:
        return

    notify(
        recipient=resident.user,
        category=NotificationCategory.SCHEDULE,
        title=f"{completion.worker.user.get_full_name()} has finished today",
        body=completion.note or "The day's work is marked complete.",
        data={"route": "/schedule", "visit_date": str(completion.visit_date)},
        society=completion.society,
    )


__all__ = [
    "MAX_LEAVE_LEAD_DAYS",
    "VisitNotFound",
    "mark_task_complete",
    "REMINDER_HORIZON_DAYS",
    "ConflictReport",
    "DuplicateLeave",
    "LeaveDateInvalid",
    "LeaveError",
    "LeaveNotActionable",
    "assign_replacement",
    "check_conflict",
    "close_lapsed_leave",
    "conflicted_worker_ids",
    "due_reminders",
    "effective_timing",
    "ensure_reminders_for_worker",
    "replacement_candidates",
    "request_leave",
    "respond_to_leave",
    "settle_leave",
    "withdraw_leave",
]
