# Ending an engagement, and pausing one for a day

Two flows that look alike and are not. **Notice** ends a standing arrangement and
needs ten days of warning. **Urgent leave — "chutti"** removes one day from it and
is approved instantly, because a worker with a sick child is not in a position to
negotiate. Both end up in the same place: money that was agreed monthly has to be
apportioned over days actually worked.

This document specifies both. Where the code already exists it says so and points
at it; where it does not, it gives the implementation.

---

## What already exists

| Piece | Where | State |
| --- | --- | --- |
| `Engagement.terminate(reason, note, by)` | `apps/hiring/models.py:453` | Built — ends immediately, no notice concept |
| `EngagementStatus.TERMINATED`, `ended_at` | `apps/hiring/models.py` | Built |
| `RecurringTerms.occurs_on(day)` | `apps/hiring/models.py:142` | Built — the basis of every calculation below |
| `ReplacementSplit` + `split(day_rate_paise)` | `apps/payments/models.py:331` | Built — how a replacement's day is divided |
| `split_for_replacement(engagement, day_rate_paise)` | `apps/payments/services.py:385` | Built |
| `PaymentKind.REPLACEMENT`, `PaymentKind.TIP` | `apps/payments/models.py:68` | Built |
| `NotificationCategory.URGENT_LEAVE` (unmutable) | `apps/notifications/models.py:46` | Built |
| `daily_rate_paise(engagement)` | `apps/payments/services.py:398` | Built |
| `salary_basis` — attendance pro-rating | `apps/payments/services.py:111` | Built |
| **`LeaveRequest` + the chutti workflow** | `apps/scheduling/` | **Built — Part B** |
| **The notice period** | `apps/hiring/` | **Built — Part A** |

Both are implemented and tested — `apps/hiring/test_notice.py` (21 tests) and
`apps/scheduling/test_leave.py` (36). Migrations `hiring.0002` and
`scheduling.0002` are applied; both are additive.

The sections below describe what is built. Where the code and this document ever
disagree, the code is right and this file is stale.

---

## Currency, once

`RecurringTerms.monthly_rate` is **rupees**. Everything in `apps/payments` is
**paise**. `rupees_to_paise()` (`apps/payments/models.py:48`) is the single
crossing point and every function below stays on the paise side of it. Do not
introduce a second crossing.

---

# Part A — The ten-day notice period

## The rule, stated precisely

> A worker (or a resident) ending a standing engagement must give **ten clear
> days'** notice. The engagement stays **active** through those ten days: the
> schedule still generates visits, attendance is still recorded, and the worker is
> paid **for the days they actually work**, at the same rate, up to and including
> the last working day.

Two things this rule does **not** say, deliberately:

- It does not pay out the notice period. Days not worked are not paid.
- It does not fine anyone for leaving early. See "The thing not to build" below.

## The API

| Method | Path | What |
| --- | --- | --- |
| `POST` | `hiring/engagements/<id>/notice/` | `{"reason": ..., "last_working_day": ...?}`. Omit the date for the earliest permitted. A shorter one returns `notice_too_short` **with the earliest date in `details`**, so the client can correct itself rather than guess. |
| `POST` | `hiring/engagements/<id>/notice/withdraw/` | Both sides changed their mind |

Deliberately *not* another action on `engagements/<id>/transition/`. Notice is
the ordinary way an arrangement ends; `terminate` is the exceptional one — abuse,
safety, mutual consent — and giving them the same shape is how the exceptional
path gets taken by accident. The client mirrors this: "Give notice" is a plain
button, "End today, without notice" is a quiet text link behind a confirmation
that says what it costs the worker.

## Schema

```python
# apps/hiring/models.py — on Engagement

#: Ten clear days. A constant rather than a settings value: this is a term of
#: the arrangement both sides agreed to, not a knob to tune per deployment.
NOTICE_PERIOD_DAYS = 10


class Engagement(...):
    ...
    notice_given_at = models.DateTimeField(null=True, blank=True, db_index=True)
    notice_given_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="engagement_notices",
    )
    #: The last day this engagement calls for a visit. Set when notice is given;
    #: the engagement remains ACTIVE until it passes.
    last_working_day = models.DateField(null=True, blank=True, db_index=True)

    @property
    def is_serving_notice(self) -> bool:
        return self.last_working_day is not None and self.status == EngagementStatus.ACTIVE

    @property
    def notice_days_remaining(self) -> int:
        if self.last_working_day is None:
            return 0
        return max(0, (self.last_working_day - timezone.localdate()).days)
```

## State machine

```
                  give_notice(by, reason, last_day)
                  ├─ last_day < today + 10  ──► NoticeTooShort (400, refused)
                  │
   ACTIVE ────────┴────────────────────────►  ACTIVE + serving notice
     │                                            │
     │                                            │  every day the schedule
     │                                            │  still produces visits,
     │                                            │  attendance still counts
     │                                            │
     │                                            ▼  today > last_working_day
     │                                       TERMINATED
     │                                            │
     │                                            ▼
     │                              final payment = prorated to days worked
     │
     └── terminate(reason)  ──►  TERMINATED       (the existing escape hatch:
                                                   abuse, safety, mutual consent —
                                                   no notice, settle immediately)
```

`terminate()` stays exactly as it is. Notice is the *ordinary* path; termination
is the exceptional one, and collapsing them would mean a worker reporting
harassment has to keep turning up for ten days.

## Final pay — "in full, for the days worked"

This is where the rule could quietly have broken its own promise, so it is worth
stating what the trap was.

`payments.services.salary_basis` pro-rates a month from **attended** visits,
counting expected visits out of Module 6's derived schedule. That schedule
expands **active** engagements only. So the moment an engagement closes at the
end of its notice, the schedule reports *nothing scheduled* for that month — and
`salary_basis`'s existing "nothing scheduled, so the full rate stands and a
person decides" fallback would have handed over a whole month's pay for a
fortnight of work.

The fix is one narrow fallback in the one place salary is computed, rather than a
second proration module that would eventually disagree with the first:

```python
# apps/payments/services.py

def _visits_from_terms(engagement, period_start, period_end) -> int:
    """Visits the terms call for, bounded by the engagement's own lifetime.

    Never before ``started_on``, never after ``last_working_day`` (or
    ``ended_at`` for an engagement terminated without notice).
    """
    ...


def salary_basis(engagement, *, period_start, period_end):
    expected = [...from worker_schedule...]
    expected_count = len(expected)

    # The one case the derived schedule cannot answer for.
    if expected_count == 0 and engagement.status != EngagementStatus.ACTIVE:
        expected_count = _visits_from_terms(engagement, period_start, period_end)
    ...
```

Additive: an active engagement takes exactly the path it always did, which
`test_an_active_engagement_is_unaffected` pins.

**No separate proration module.** An earlier draft of this document proposed
`apps/hiring/proration.py` with its own month-exact day rate. It was not built,
because the codebase already had two ways to divide a monthly rate
(`salary_basis` by attendance, `daily_rate_paise` per visit) and a third would
have been the one that eventually disagreed with a payslip.

## Worked example

Weekdays, ₹4,000/month. Notice given on the 1st, last working day the 10th.

| Step | Result |
| --- | --- |
| Expected visits, 1st–10th | up to 8 (weekdays only) |
| Expected visits, 11th–month end | **0** — clipped at the last working day |
| Attended, from the gate log | say 8 |
| Suggested | `400000 × 8 // 8` = full rate **for that period** |

Ask for the whole month instead and the denominator is still 8, because that is
all the engagement ever called for. The worker is paid for every day they worked
and not for days after they left — which is the rule, arithmetically.

## The service

```python
# apps/hiring/services.py

class NoticeTooShort(WorkerError):
    """Raised when a requested last working day is inside the notice period."""


@transaction.atomic
def give_notice(engagement, *, by, reason: str, requested_last_day=None):
    """Start the notice period. Returns the engagement.

    The engagement stays ACTIVE. Nothing about the schedule, the gate or
    attendance changes until ``last_working_day`` passes — a worker serving
    notice is still working, and treating them as already gone would strand
    them at a gate that no longer recognises them.
    """
    if engagement.status != EngagementStatus.ACTIVE:
        raise WorkerError("Only an active engagement can be given notice.")
    if engagement.last_working_day is not None:
        raise WorkerError("Notice has already been given on this engagement.")

    today = timezone.localdate()
    earliest = today + dt.timedelta(days=NOTICE_PERIOD_DAYS)
    last_day = requested_last_day or earliest

    if last_day < earliest:
        raise NoticeTooShort(
            f"The agreed notice period is {NOTICE_PERIOD_DAYS} days, so the "
            f"earliest last day is {earliest:%d %b %Y}."
        )

    engagement.notice_given_at = timezone.now()
    engagement.notice_given_by = by
    engagement.last_working_day = last_day
    engagement.end_reason = reason
    engagement.save(update_fields=[
        "notice_given_at", "notice_given_by", "last_working_day",
        "end_reason", "updated_at",
    ])

    notify(...)  # both sides, category=HIRE
    return engagement


def close_engagements_past_notice(*, today=None) -> int:
    """Flip engagements whose last working day has passed. Returns how many.

    There is no Celery and no scheduler (docs/free-tier-constraints.md §7), so
    this is idempotent and safe to call from anywhere — hang it off the same
    daily cadence as ``escalate_complaints``, and call it lazily from the
    engagement list view so a demo without cron still behaves correctly.
    """
    today = today or timezone.localdate()
    due = Engagement.objects.filter(
        status=EngagementStatus.ACTIVE,
        last_working_day__isnull=False,
        last_working_day__lt=today,
    )
    count = 0
    for engagement in due:
        engagement.status = EngagementStatus.TERMINATED
        engagement.ended_at = timezone.now()
        engagement.save(update_fields=["status", "ended_at", "updated_at"])
        count += 1
    return count
```

## The Dart side

The client's job is to stop an impossible date being submitted at all — an error
that arrives *after* someone has chosen a date is a worse experience than one
that was never selectable.

```dart
/// Module 4.5 — the ten-day notice rule, client side.
///
/// Mirrors `NOTICE_PERIOD_DAYS` on the server. The server is the authority and
/// refuses anything shorter with `notice_too_short`; this exists so the date
/// picker cannot offer a day the server will reject.
class NoticePeriod {
  const NoticePeriod._();

  static const int days = 10;

  /// The earliest last working day, from [today].
  ///
  /// Date arithmetic via `DateTime(y, m, d + n)` rather than `add(Duration)`:
  /// Duration is absolute and a DST or clock change can land the result on the
  /// wrong calendar day. India has no DST, but the phone's timezone is the
  /// user's to change and this costs nothing to get right.
  static DateTime earliestLastDay(DateTime today) =>
      DateTime(today.year, today.month, today.day + days);

  static bool isPermitted(DateTime candidate, {required DateTime today}) =>
      !candidate.isBefore(earliestLastDay(today));

  /// What the worker is told before they confirm. Concrete, not a rule.
  static String summary(DateTime lastDay, {required int scheduledDaysLeft}) =>
      'You will keep working until ${_format(lastDay)} '
      '($scheduledDaysLeft more ${scheduledDaysLeft == 1 ? "visit" : "visits"}), '
      'and be paid for every day you work.';
}

// In the screen:
final earliest = NoticePeriod.earliestLastDay(DateTime.now());
final picked = await showDatePicker(
  context: context,
  initialDate: earliest,
  firstDate: earliest,          // nothing shorter is selectable
  lastDate: DateTime(earliest.year + 1),
);
```

## The thing not to build

The obvious "enforcement" for a notice period is to withhold pay from a worker
who leaves early. **Do not.**

Wages for days already worked are earned. Withholding them as a penalty for
short notice is, at best, legally exposed under the Payment of Wages Act, and it
falls hardest on exactly the workers least able to contest it — which is the
population this platform exists to serve. It would also be trivially
counterproductive: a worker who knows leaving costs them a week's pay does not
give notice, they simply stop turning up, and the resident gets *less* warning.

Enforce it where it belongs, on reputation:

- Notice given and served → nothing happens, which is the point.
- Left without notice → a factual, non-punitive entry on the trust score
  (`apps/ratings/trust.py` already models this), decaying over time.
- The resident is notified the moment notice is given, so they have ten days to
  find someone — which is the actual purpose of the rule.

---

# Part B — Urgent leave ("chutti")

## State machine

```
 worker taps "I cannot come tomorrow"
            │
            ▼
     ┌──────────────┐   auto-approved, no review, no counter-offer
     │  REQUESTED   │   (a worker asking for a day is reporting a fact)
     └──────┬───────┘
            │  instantly
            ▼
     ┌──────────────┐   ── notify resident, category=URGENT_LEAVE
     │   APPROVED   │      (unmutable — apps/notifications/models.py:59)
     └──────┬───────┘
            │
            ▼   the resident answers, in the app, within the notification
     ┌──────────────────────────────────┐
     │  "Do you need someone tomorrow?" │
     └───────┬──────────────────┬───────┘
             │                  │
    "No, I'll manage"     "Yes, send a replacement"
             │                  │
             ▼                  ▼
     ┌──────────────┐    ┌──────────────────┐
     │   WAIVED     │    │ REPLACEMENT_     │  match on the existing scorer
     │              │    │ REQUESTED        │  (apps/hiring/scoring.py), one day
     └──────┬───────┘    └────────┬─────────┘
            │                     │
            │            ┌────────┴─────────┐
            │            │                  │
            │      someone accepts    nobody accepts by cutoff
            │            │                  │
            │            ▼                  ▼
            │    ┌──────────────┐   ┌──────────────┐
            │    │ REPLACEMENT_ │   │ UNFILLED     │  ── tell the resident
            │    │ CONFIRMED    │   │              │     early enough to plan
            │    └──────┬───────┘   └──────┬───────┘
            │           │                  │
            ▼           ▼                  ▼
     ┌────────────────────────────────────────────┐
     │  SETTLED — the day's money is apportioned  │
     └────────────────────────────────────────────┘
```

`WAIVED` and `UNFILLED` are different states on purpose even though both mean
"nobody came". One is a household that chose to manage; the other is the
platform failing to deliver. Collapsing them hides the second, which is the one
worth measuring — and it is already what `UnmetDemand`
(`apps/administration/models.py`) exists to record.

## Schema

Built, in `apps/scheduling/models.py`. Abridged here — the module carries the
full reasoning:

```python
class LeaveStatus(models.TextChoices):
    APPROVED = "approved", _("Approved")
    WAIVED = "waived", _("Resident needs no replacement")
    REPLACEMENT_REQUESTED = "replacement_requested", _("Looking for a replacement")
    REPLACEMENT_CONFIRMED = "replacement_confirmed", _("Replacement confirmed")
    UNFILLED = "unfilled", _("No replacement found")
    WITHDRAWN = "withdrawn", _("Withdrawn by the worker")


class LeaveRequest(SocietyScopedModel, TimeStampedModel):
    """Module 6.4 — one worker, one engagement, one day off.

    There is no PENDING state. The modspec approves urgent leave instantly and
    that is the right call: a worker who has to justify a sick child to an app
    before they can stay home with them will simply not turn up instead, and the
    resident finds out at 7am. Instant approval buys the notice.
    """

    engagement = models.ForeignKey(
        "hiring.Engagement", on_delete=models.CASCADE, related_name="leave_requests"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="leave_requests"
    )
    leave_date = models.DateField(db_index=True)
    reason = models.CharField(max_length=200, blank=True)   # optional, always
    status = models.CharField(
        max_length=30, choices=LeaveStatus.choices,
        default=LeaveStatus.APPROVED, db_index=True,
    )

    replacement = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="replacement_assignments",
    )
    replacement_confirmed_at = models.DateTimeField(null=True, blank=True)
    resident_responded_at = models.DateTimeField(null=True, blank=True)

    #: Frozen at settlement. The day rate moves with the engagement's terms, and
    #: a receipt that silently disagrees with the payment it explains is worse
    #: than no receipt.
    day_rate_paise = models.PositiveIntegerField(default=0)
    #: What the original worker forgoes — equal to what the replacement receives.
    forgone_paise = models.PositiveIntegerField(default=0)
    replacement_paise = models.PositiveIntegerField(default=0)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["engagement", "leave_date"],
                name="one_leave_request_per_engagement_day",
            ),
            # A worker cannot stand in for their own absence.
            models.CheckConstraint(
                condition=~models.Q(replacement=models.F("worker")),
                name="replacement_is_not_the_absent_worker",
            ),
        ]
```

The unique constraint is load-bearing: a worker tapping "apply" twice on a bad
connection must not deduct two days' pay. This is the same idempotency rule
Module 13.2 applies to attendance sync (`apps/core/resilience.py`).

## The money, and the deduction that must not exist

The obvious implementation of "prorated daily wage deduction" subtracts a day's
pay from the absent worker's salary. **It is wrong, and wrong in the direction
that costs a worker money.**

`payments.services.salary_basis` already pro-rates the month by *attended*
visits, counted from the gate log. A day not worked is already a day not paid.
Deducting again in the leave record would dock the same absence twice, and the
person it would be taken from is the least able to notice.

So `settle_leave` records the **transfer** — what the replacement is owed, which
is exactly what the original worker forgoes — and never touches a salary:

```python
# apps/scheduling/services.py, abridged

@transaction.atomic
def settle_leave(leave):
    if leave.is_settled or leave.status not in SETTLEABLE_LEAVE_STATUSES:
        return leave

    rate = daily_rate_paise(leave.engagement)
    to_replacement, forgone = 0, rate

    if leave.status == LeaveStatus.REPLACEMENT_CONFIRMED and leave.replacement_id:
        # ReplacementSplit models the per-engagement rule and defaults to 100%
        # for the replacement — apps/payments/services.py:385.
        to_replacement, retained = split_for_replacement(
            leave.engagement, day_rate_paise=rate
        )
        forgone = rate - retained

        create_payment(
            resident=leave.engagement.resident,
            worker=leave.replacement,
            society=leave.society,
            kind=PaymentKind.REPLACEMENT,
            amount_paise=to_replacement,
            engagement=leave.engagement,
        )

    leave.day_rate_paise = rate
    leave.forgone_paise = forgone
    leave.replacement_paise = to_replacement
    leave.settled_at = timezone.now()
    leave.save(update_fields=[...])
    return leave
```

With the default 100/0 split the two halves net out exactly: attendance
pro-rating removes the day from her salary, and the replacement is paid that
same day. Nothing has to reconcile them.

`apps/scheduling/test_leave.py::TestSettlement::test_leave_does_not_deduct_from_the_absent_workers_salary`
is what stops this regressing.

**The remaining seam, stated plainly.** Where an engagement carries a
`ReplacementSplit` below 100%, the original worker is meant to keep a share of a
day she did not work. Attendance pro-rating cannot express that — it counts
whole visits — so `forgone_paise` records the difference for the receipt and a
person applies it. Salary adjustment lines are not modelled yet. The seam is
deliberate and narrow; silently paying the wrong number would not be.

## Worked example

Engagement: Mon–Sat, ₹6,000/month. Leave on one Tuesday.

`daily_rate_paise` divides by `len(days_of_week) * 4` — the codebase's existing
day rate, reused rather than duplicated, since a second definition would
eventually disagree with the first.

| Step | Calculation | Result |
| --- | --- | --- |
| Monthly rate in paise | `6000 × 100` | 600,000 p |
| Scheduled visits per month | `6 days × 4 weeks` | 24 |
| Day rate | `600000 // 24` | 25,000 p (₹250.00) |
| Split rule (engagement default) | `replacement_share_percent = 100` | 100 / 0 |
| To the replacement | `25000 × 100 // 100` | 25,000 p |
| She forgoes | — | 25,000 p |
| Her salary that month | attendance pro-rating, 25 of 26 visits | reduced by one visit |

With a `replacement_share_percent` of 60 the replacement receives 15,000 p and
she keeps 10,000 p of that day — the "regular worker sent a substitute"
arrangement `ReplacementSplit`'s docstring describes, and the case where the
seam above applies.

Note that `24` is an approximation of a month: a 6-day week is closer to 26
visits in a 31-day month. That makes the day rate slightly generous, which is
the right direction for an error that lands on a day's wage — but if you want it
exact, change `daily_rate_paise` (one function, one place) rather than
introducing a second day rate here.

## The API

Mounted at `/api/v1/scheduling/`.

| Method | Path | Who | What |
| --- | --- | --- | --- |
| `GET` | `leave/` | any party | Leave you can see. A worker gets their own days off *and* the days they agreed to cover. Sweeps lapsed requests on the way past. |
| `POST` | `leave/` | worker | Take a day off. Approved in the same response. |
| `POST` | `leave/<id>/response/` | resident/admin | `{"needs_replacement": true\|false}` |
| `GET` | `leave/<id>/candidates/` | resident/admin | Free workers, ranked by Module 4.3's scorer |
| `POST` | `leave/<id>/replacement/` | resident/admin | `{"replacement": <worker_id>}` — confirms and settles |
| `POST` | `leave/<id>/withdraw/` | worker | Refused once cover is confirmed |

## The schedule is leave-aware

`schedule.py` reads leave back when it expands engagements, so an absence shows
up everywhere a visit does without becoming a second copy of it:

* the regular worker's item is **marked, not removed** — payroll counts expected
  visits from this same list, and a household that saw the row vanish would
  wonder whether they had misremembered the day;
* the cover visit appears on the **replacement's own** schedule, carrying the
  engagement's id so the gate matches it correctly; and
* it reaches the **society roster**, which is what the gate reads. A replacement
  who is not on it arrives to a guard with no record of them.

That costs two constant queries per schedule read, which is why
`test_query_count_does_not_grow_with_the_range` now compares one day against a
fortnight rather than asserting a fixed number — the property worth protecting
is that cost does not scale with the range, and it still does not.

## Client

| Piece | Where |
| --- | --- |
| `LeaveRequest`, `LeaveStatus`, `ReplacementCandidate` | `mobile/lib/features/scheduling/data/models/schedule_models.dart` |
| Repository + providers | `.../data/repositories/schedule_repository.dart`, `.../presentation/providers/schedule_provider.dart` |
| Worker: take a day off | `.../screens/apply_leave_screen.dart` — `/schedule/leave/new` |
| Household: answer | `.../screens/leave_response_screen.dart` — `/schedule/leave/<id>` |

Two deliberate UI decisions. The worker's entry point is a **floating action
button on their home screen**, not a menu item — every tap between the worker
and that button is time the household does not get to arrange cover. And the
household's two answers are given **equal visual weight**: making "send a
replacement" the prominent one would push households into booking cover they do
not need, which costs them money and costs the absent worker a day's pay she
would otherwise have kept.

---

## Migration note

`0002_leaverequest` is applied. It is additive — one new table, no columns
changed on anything existing.

Part A's notice period is **not** migrated. It adds columns to `Engagement`, in a
**shared Supabase database that the deployed app and every developer's local
backend both use** (`backend/.env` points `DATABASE_URL` at the pooled instance).
Run that one deliberately, not as a side effect of a test run. See
`docs/cloud-database.md`.
