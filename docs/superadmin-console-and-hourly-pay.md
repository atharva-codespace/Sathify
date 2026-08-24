# The Superadmin console, per-flat attendance, and paying by the hour

Three pieces of product that only work together. A **cross-society web console**
for whoever runs the platform; a **per-flat work session** record, because the
gate log physically cannot say who a worker was working for; and a **two-part
wage engine** — a fixed visit fee plus an hourly rate — that pays the same
effective rate for a one-hour job as for a four-hour one.

This is a requirements document, not a retrospective. Where the code already
exists it says so and points at it; where it does not, it gives the design and
the arithmetic. Section 2 is the important one: four places where these
requirements collide with decisions this repo has already shipped.

> A rendered version with the wireframes laid out side by side is published at
> <https://claude.ai/code/artifact/32d37b96-180f-4804-8fa4-3e8608ed75ae>.
> This file is the source of record.

---

## Contents

1. [Where this sits today](#1-where-this-sits-today)
2. [Four collisions with the existing build](#2-four-collisions-with-the-existing-build)
3. [Superadmin console — web, desktop only](#3-superadmin-console--web-desktop-only)
4. [Console layouts](#4-console-layouts)
5. [Attendance — the flat problem](#5-attendance--the-flat-problem)
6. [Mobile screen flows](#6-mobile-screen-flows)
7. [The billing engine](#7-the-billing-engine)
8. [End to end — mark, calculate, reflect](#8-end-to-end--mark-calculate-reflect)
9. [Schema, rollout, decisions](#9-schema-rollout-decisions)

---

## 1. Where this sits today

This is not a greenfield spec. A working backend already models most of this
domain, and three of its decisions are load-bearing enough that the requirements
have to be designed *against* them rather than on top of them.

What exists and should be reused as-is:

- **`apps/attendance`** — `AttendanceEvent` is offline-first by schema: a
  client-generated UUID primary key makes `/attendance/sync/` idempotent, and
  `occurred_at` is kept separate from `recorded_at` so a batch synced at 6pm does
  not read as forty people arriving at once. Six verification methods already
  exist, including `RESIDENT_SCAN`, which §5 leans on heavily.
- **`apps/payments`** — money is integer paise throughout, with
  `rupees_to_paise` as the single crossing point. `Payment` carries
  `receipt_number`, `due_at`, `period_start/end`, the Razorpay order/payment/
  signature columns, and `settled_via`. A payment reaches `PAID` only through a
  signature-verified message.
- **`apps/scheduling`** — `TaskTiming` already holds `expected_arrival`,
  `expected_departure`, `arrival_grace_minutes`, `departure_grace_minutes` and a
  `lateness_minutes()` helper. The engine in §7 is largely a consumer of this
  model rather than a replacement for it.
- **`apps/administration/reports.py`** — `attendance_report`, `payment_report`,
  `complaint_report`, plus `render_csv` and `render_pdf`. The report bodies are
  reusable; their *signature* is not (§3.3).

> **Design principle inherited from the codebase.** `AttendanceEvent` is
> described as "append-only in spirit: a wrong entry is corrected by a
> superseding one, never by editing history." Every new model here follows the
> same rule — including invoices, which correct by adjustment line rather than by
> edit (§7.7).

---

## 2. Four collisions with the existing build

Each of these is a real conflict between a requested feature and a decision
already shipped. None is a blocker, but each changes what "build the Superadmin
dashboard" or "add hourly payments" actually costs.

### 2.1 There is no platform-operator role

`accounts.Role` is exactly `resident`, `worker`, `guard`, `society_admin`, and
`create_superuser` defaults a new superuser to `SOCIETY_ADMIN`.

Superadmin is therefore a new role plus a separate auth surface. It cannot be
"society_admin with more rows": the permission classes read `role`, so widening
society_admin would widen it for every society's own committee too.

### 2.2 Everything is society-scoped

Domain models inherit `SocietyScopedModel`; querysets assume a single society. A
cross-society console inverts the core invariant. It needs an explicit,
separately-audited unscoped read path — not a permission bypass sprinkled
through existing views (§3.6).

### 2.3 Pay is monthly, not hourly

`RecurringTerms.monthly_rate` is "Agreed monthly pay in INR", whole rupees. Both
`LeaveRequest.day_rate_paise` and `ReplacementSplit.split(day_rate_paise)` assume
a derivable day rate.

Hourly is a second terms *type*, not an edit — and it needs a companion
`visit_fee`, or short jobs underpay the worker by a third (§7.2). Day rate
becomes **derived** so leave and replacement math keeps working under both
models (§7.8).

### 2.4 Gate events cannot bill hourly

One `AttendanceEvent` pair covers a worker's whole society visit. `engagement` is
nullable and matched with a ±120-minute window (`VISIT_MATCH_WINDOW_MINUTES`).

A worker serving four flats in one visit produces one entry and one exit. You
cannot divide that into four billable sessions without inventing data. This
requires a new per-engagement session record (§5).

### 2.5 Revenue is not a cut of wages

`docs/monetisation.md` is explicit: *"Commission on recurring wages — Do not
build this."* Consistently, `Payment.platform_fee_paise` is zero on every row
today.

So the financial module must show **two numbers that are never summed**:

| | What it is | Does the platform earn it? |
| --- | --- | --- |
| **GMV** | wages flowing resident → worker | No |
| **Revenue** | society subscriptions, booking convenience fees, verification | Yes |

Every layout in §4 keeps them visually separate. A dashboard that renders one
"Total ₹" invites the whole company to optimise the wrong line.

---

## 3. Superadmin console — web, desktop only

The operator persona is two or three people running the whole platform. They
answer "is a society about to churn", "did this ₹4,200 payment actually land",
and "who suspended that worker and why". **Optimise for the second question** —
reconciliation is the daily work; the bird's-eye view is the weekly work.

### 3.1 Overview

Not a vanity wall. The default screen is a **work queue**: things that are stuck,
ambiguous, or about to break. Trend tiles sit above it because they frame the
queue, not because they are the point. Four tiles, and the top two deliberately
do not add up: *MRR* and *GMV settled*.

### 3.2 Transactions and financials

The table is the product here. The single most valuable filter is one the schema
already anticipates — `settled_via` exists so that *"which payments rest on a
person's word rather than a signature?"* is answerable with a filter rather than
a code review. That question gets a saved view of its own: UPI settlements
confirmed by a society admin against a bank statement, where the platform holds a
UTR string and a human's assertion, not an HMAC.

- Ledger of every `Payment` across all societies, with kind, status, settlement
  path, and overdue age.
- **Reconciliation view** — payments whose Razorpay webhook never arrived, and
  `WebhookEvent` rows with `signature_valid = false` or `processed = false`.
- **Payouts** — what each worker is owed vs paid this cycle, from
  `worker_receives_paise`, which excludes platform-charge kinds.
- Refunds and disputes, with the invoice adjustment (§7.7) that resolved each.

### 3.3 Reports

The report *bodies* in `administration/reports.py` are reusable. Their signature
is `build(kind, society, *, start, end)` — single society, synchronous, returning
a rendered table. Cross-society reporting over a year of gate events will not
survive a request timeout.

So: keep the renderers, wrap them in an async job. Superadmin selects scope (all
/ tier / region / explicit set), the job fans out per society, merges, and emits
CSV or PDF to a signed, expiring download. The UI shows a job list with state,
not a spinner. Scheduled recurring exports use the same queue.

### 3.4 Societies

Onboard, inspect, tier-change, suspend. Detail view shows subscription tier
against `TIER_LIMITS` usage (worker count vs cap is the upgrade trigger), gates,
admin accounts, and a health strip.

> **"Suspend" must not mean "stop the gate."** The monetisation doc's constraint
> is a hard product rule: *"Locking a society out of its own attendance record
> for an unpaid invoice would put workers' wages behind a billing dispute."*
> Suspension therefore narrows the **reporting and onboarding** surface only.
> Gate checks, attendance writes, and complaint intake keep working at all times.
> The confirm dialog states this in words, so no operator believes they are
> pulling a bigger lever than they are.

### 3.5 Activity log

Two surfaces off one store. A **live feed** for ambient awareness, and a
**sensitive-actions** tab that is the actual audit trail: impersonation,
suspension, tier change, refund, manual settlement, face-match override,
backdated attendance. Every row carries actor, role, target, society, IP, and a
mandatory reason string. Retention matches the SRS's three-year requirement on
attendance evidence.

### 3.6 Permissions and the scoping bypass

Because §2.2 makes this genuinely dangerous, the console's data access is built
as one narrow seam rather than many:

- A single `PlatformScoped` manager path, used only by console endpoints, that
  reads across societies and writes an access record for every query touching
  resident or worker PII.
- **Read-wide, write-narrow.** Superadmin sees everything; mutations to a
  society's operational data go through impersonation, which is time-boxed,
  reason-gated, banner-visible in the impersonated session, and logged as its own
  sensitive action.
- Two Superadmin sub-roles — *Support* (read + impersonate) and *Finance* (read +
  refund + manual settlement). Nobody holds both by default.

### 3.7 Explicitly not in the console

No per-society daily operations. The society admin's queues, gate log, and
complaint SLA already exist in the mobile/admin product and are what the
subscription sells. Rebuilding them on web would fork the workflow and quietly
become the reason committees ask for a web app they were never sold.

---

## 4. Console layouts

Desktop only, minimum 1280px. Fixed 18ch sidebar, persistent search in the top
bar, and a right-hand detail drawer rather than row navigation — an operator
reconciling forty payments should never lose the list.

### Plate 01 — Overview: shell, tiles, work queue (`/ops`)

```
┌─────────────────┬──────────────────────────────────────────────────────────────────────────────────────────┐
│ SATHIFY OPS     │ [search: society · user · receipt no. · UTR ..................]  (!) 4   FINANCE · AD  v │
│ platform admin  ├──────────────────────────────────────────────────────────────────────────────────────────┤
├─────────────────┤                                                                                          │
│ > Overview      │  OVERVIEW                              scope: all societies v    period: last 30 days v  │
│   Transactions  │                                                                                          │
│   Activity      │  ┌──── PLATFORM REVENUE ────┬──── GMV SETTLED ─────────┬─── SOCIETIES ───┬─── WORKERS ──┐│
│   Reports       │  │ ₹ 1,84,500 /mo           │ ₹ 47,20,880              │ 128 active      │ 3,914 paid   ││
│   Societies     │  │ subscriptions + fees     │ wages resident -> worker │ 9 free -> paid  │ 214 unpaid   ││
│   Users         │  │ ^ 6.2% vs Jul            │ platform earns nil on it │ 2 suspended     │ cycle: Aug   ││
│                 │  └──────────────────────────┴──────────────────────────┴─────────────────┴──────────────┘│
├─────────────────┤   these two are never summed  ^                                                          │
│   Platform cfg  │                                                                                          │
│   Audit exports │  ┌─ NEEDS ATTENTION ──────────────────────────────────────────────── 11 open ──────────┐ │
├─────────────────┤  │ CRIT  6 payments PAID at gateway, no webhook >24h        ₹18,240   [Reconcile >]   │  │
│  Aug 2026       │  │ CRIT  1 society over worker cap on FREE tier (Sunview)   cap 25/31  [Review >]      │ │
│  build 0.9.4    │  │ WARN  38 work sessions auto-closed, unreviewed (7d)      4 socs      [Open queue >]  ││
│  status: ok     │  │ WARN  2 invoices disputed past 48h review window         ₹3,110     [Arbitrate >]   │ │
│                 │  │ INFO  9 societies end trial in 14 days                   ₹13,500 ARR [Contact >]    │ │
│                 │  └──────────────────────────────────────────────────────────────────────────────────────┘│
│                 │                                                                                          │
│                 │  ┌─ REVENUE BY TIER ─────────────────┐  ┌─ LIVE ACTIVITY ──────────────── streaming ──┐  │
│                 │  │ PLUS      ####################  42 │  │ 14:22  payment.paid   Rec#SA-9931  ₹4,200  │  │
│                 │  │ STANDARD  ##############        71 │  │ 14:22  session.close  W#812 Flat B-704     │  │
│                 │  │ FREE      #####                 15 │  │ 14:21  face.pending   Gate 2, Palm Grove   │  │
│                 │  │  MRR ₹1,84,500  ARPS ₹1,441       │  │ 14:19  invoice.issue  ENG#4417   ₹9,840    │   │
│                 │  │  churn 1.4%/mo  net rev retn 108% │  │ 14:18  user.signup    resident, Sunview    │   │
│                 │  └───────────────────────────────────┘  └──────────────────────── [full log >] ───────┘  │
└─────────────────┴──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Why the queue outranks the chart.** The tiles answer a weekly question; the
queue is Monday morning's job list. Each row states the money or the count at
risk, so triage happens without opening anything. The two revenue tiles carry an
explicit note that they are not additive — the one piece of chrome earning its
space.

### Plate 02 — Transactions: ledger, filters, detail drawer (`/ops/transactions`)

```
┌─────────────────┬──────────────────────────────────────────────────────────────────────────────────────────┐
│ SATHIFY OPS     │ [search: receipt no. · UTR · razorpay id ....................]  (!) 4   FINANCE · AD  v  │
├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
│   Overview      │  TRANSACTIONS                                                                            │
│ > Transactions  │  Saved: [ All ] [ Unsigned settlements * ] [ Webhook gaps ] [ Overdue 7d+ ] [ Refunds ]  │
│   Activity      │  society: all v   kind: all v   status: all v   settled via: all v   Aug 1 - Aug 13 v    │
│   Reports       │  ──────────────────────────────────────────────────────────────────────────────────────  │
│   Societies     │  raised ₹52,10,400   settled ₹47,20,880   open ₹4,64,220   overdue ₹25,300   refund ₹0   │
│   Users         │  ──────────────────────────────────────────────────────────────────────────────────────  │
│                 │  RECEIPT     DATE   SOCIETY     RESIDENT -> WORKER      KIND       AMOUNT  STATUS  VIA   │
├─────────────────┤  ─────────────────────────────────────────────────────────────────────────────────────   │
│   Platform cfg  │  SA-9931   Aug 13  Palm Grove  B-704 -> Sunita D.    salary      ₹4,200  PAID    sig     │
│   Audit exports │  SA-9930   Aug 13  Sunview     A-102 -> Rekha M.     salary      ₹3,860  PAID    sig     │
├─────────────────┤ >SA-9928   Aug 12  Palm Grove  C-201 -> Anita K.     salary      ₹6,700  PAID    UTR *   │
│  legend         │  SA-9927   Aug 12  Lake Vista  D-403 -> Meena P.     booking       ₹640  OPEN    -       │
│  sig = razorpay │  SA-9925   Aug 11  Sunview     -     -> (platform)   emergency     ₹150  PAID    sig     │
│  UTR = admin    │  SA-9924   Aug 11  Palm Grove  A-905 -> Sunita D.    settlement  ₹2,310  FAILED  -       │
│        asserted │  SA-9921   Aug 09  Green Acre  B-118 -> Laxmi S.     salary      ₹4,480  OVERDUE -  4d   │
│  * = rests on a │  ─────────────────────────────────────────────────────────────────────────────────────   │
│      person,    │                                        showing 7 of 1,284    [ CSV ]  [ PDF ]  < 1 2 3 > │
│      not an     │                                                                                          │
│      HMAC       │  ┌─ SA-9928 ─────────────────────────────────────────────────────────── [x] close ─────┐ │
│                 │  │ ₹6,700.00   PAID   settled via UPI-UTR   Aug 12, 19:04 IST                          │ │
│                 │  │ ───────────────────────────────────────────────────────────────────────────────────│  │
│                 │  │ period      Jul 16 - Aug 15      engagement  ENG#4417                               │ │
│                 │  │ resident    C-201, Palm Grove    worker      Anita K.  (W#812)                      │ │
│                 │  │ base        ₹6,700  platform fee ₹0  ->  worker receives ₹6,700                     │ │
│                 │  │ ───────────────────────────────────────────────────────────────────────────────────│  │
│                 │  │ ! No gateway signature. Confirmed by S. Rao (society_admin) against statement.      │ │
│                 │  │   UTR SBIN9920034812   amount seen ₹6,700   matches invoice total                   │ │
│                 │  │ ───────────────────────────────────────────────────────────────────────────────────│  │
│                 │  │ invoice INV-4417-08  26 sessions  42h15m + 26 visits  [ view sessions > ]            ││
│                 │  │ [ Refund ]  [ Flag for review ]  [ Open audit trail ]                               │ │
│                 │  └──────────────────────────────────────────────────────────────────────────────────────┘│
└─────────────────┴──────────────────────────────────────────────────────────────────────────────────────────┘
```

**The `*` is the whole design.** Two settlement paths are HMAC-verified and one
is an administrator's word against a bank statement. The ledger marks that
difference in the row, in the legend, and again in the drawer — because a finance
operator scanning 1,284 rows needs to know which numbers are evidence and which
are testimony.

### Plate 03 — Activity: sensitive actions (`/ops/activity?tab=sensitive`)

```
┌─────────────────┬───────────────────────────────────────────────────────────────────────────────────────────┐
│   Overview      │  ACTIVITY          [ Live feed ]  [ Sensitive actions * ]  [ Auth events ]  [ Webhooks ]  │
│   Transactions  │  actor: all v  action: all v  society: all v  Aug 1 - Aug 13 v          [ Export audit ]  │
│ > Activity      │  ──────────────────────────────────────────────────────────────────────────────────────   │
│   Reports       │  TIME         ACTOR / ROLE           ACTION             TARGET            SOCIETY   IP    │
│   Societies     │  ───────────────────────────────────────────────────────────────────────────────────────  │
│   Users         │  Aug 13 11:04 A.Deshmukh superadmin  impersonate.start  S.Rao soc_admin   Palm Gr  10.2.. │
│                 │    reason: "resident reports invoice shows 2 extra sessions, INV-4417-08"                 │
│                 │    ended 11:19 (15m)  ·  4 records read  ·  0 writes                                      │
│                 │  ───────────────────────────────────────────────────────────────────────────────────────  │
│                 │  Aug 13 09:41 M.Iyer   superadmin    society.suspend    Green Acre        Green A  10.2.. │
│                 │    reason: "non-payment, 62 days, 3 contacts unanswered"                                  │
│                 │    scope: reporting + onboarding disabled · gate log and attendance UNAFFECTED            │
│                 │  ───────────────────────────────────────────────────────────────────────────────────────  │
│                 │  Aug 12 19:04 S.Rao    soc_admin     payment.settle     SA-9928 ₹6,700    Palm Gr  49.3.. │
│                 │    reason: "UPI to society account, UTR SBIN9920034812, verified on statement"            │
│                 │  ───────────────────────────────────────────────────────────────────────────────────────  │
│                 │  Aug 12 08:12 Guard#31 guard         face.override      W#812 entry       Palm Gr  dev-22 │
│                 │    match 0.61 (below 0.75) -> ALLOWED   reason: "known worker, poor light at gate 2"      │
│                 │  ───────────────────────────────────────────────────────────────────────────────────────  │
│                 │  Aug 11 16:50 M.Iyer   superadmin    payment.refund     SA-9902 ₹1,240    Lake Vi  10.2.. │
│                 │    reason: "duplicate charge after webhook replay; adjustment on INV-3390-08"             │
│                 │  ───────────────────────────────────────────────────────────────────────────────────────  │
│                 │                                             retention 3 years · append-only · 41 events   │
└─────────────────┴───────────────────────────────────────────────────────────────────────────────────────────┘
```

**Reason strings are mandatory and rendered inline, not hidden behind a hover.**
An audit trail whose justification takes a click is an audit trail nobody reads.
Note the suspension row restating its own blast radius — the log is where a
future operator learns what the lever actually did.

### Plate 04 — Reports: async cross-society builder (`/ops/reports`)

```
┌─────────────────┬──────────────────────────────────────────────────────────────────────────────────────────┐
│   Overview      │  REPORTS                                                                                 │
│   Transactions  │  ┌─ BUILD ─────────────────────────────────┐ ┌─ RECENT JOBS ────────────────────────────┐│
│   Activity      │  │ kind      (o) financials                │ │ Jul financials, all socs   READY  4.1 MB ││
│ > Reports       │  │           ( ) attendance                │ │   [ CSV ]  [ PDF ]  expires in 6d        ││
│   Societies     │  │           ( ) society growth            │ │ ---------------------------------------- ││
│   Users         │  │           ( ) worker payouts            │ │ Attendance Q2, PLUS tier   RUNNING       ││
│                 │  │           ( ) billing integrity         │ │   ############------  62%  ~3m left      ││
│                 │  │                                         │ │ ---------------------------------------- ││
│                 │  │ scope     ( ) all societies (128)       │ │ Growth Jan-Jun, all       FAILED         ││
│                 │  │           (o) by tier: [PLUS] [STD]     │ │   3 societies timed out  [ Retry these ] ││
│                 │  │           ( ) select societies...       │ │ ---------------------------------------- ││
│                 │  │                                         │ │ Payouts Jul, Palm Grove    READY  180 KB ││
│                 │  │ period    [ 2026-07-01 ] [ 2026-07-31 ] │ └──────────────────────────────────────────┘│
│                 │  │ group by  (o) society ( ) tier ( ) month│ ┌─ SCHEDULED ──────────────────────────────┐│
│                 │  │ columns   [x] receipt  [x] society      │ │ Monthly financials   1st, 06:00  -> 3 ops││
│                 │  │           [x] kind     [x] settled_via  │ │ Weekly integrity     Mon, 07:00  -> ops  ││
│                 │  │           [x] amount   [ ] resident PII │ │ [ + new schedule ]                       ││
│                 │  │           [x] status   [ ] worker PII   │ └──────────────────────────────────────────┘│
│                 │  │                                         │                                             │
│                 │  │ format    [x] CSV   [x] PDF             │  ! PII columns are logged per export and    │
│                 │  │ deliver   (o) download  ( ) email       │    require a stated reason before the job   │
│                 │  │                                         │    is queued.                               │
│                 │  │ est. 128 societies, ~340k rows, ~4 min  │                                             │
│                 │  │           [ Queue report ]              │                                             │
│                 │  └─────────────────────────────────────────┘                                             │
└─────────────────┴──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Jobs, not spinners.** Cross-society builds run for minutes and partially fail —
one society timing out must not void the other 127, so the failed job offers a
targeted retry. PII columns are opt-in and independently audited, since a full
export is the single largest privacy surface the console has.

### Plate 05 — Societies and Users (`/ops/societies`, `/ops/users`)

```
┌─────────────────┬──────────────────────────────────────────────────────────────────────────────────────────┐
│ > Societies     │  SOCIETIES                         tier: all v   health: all v      [ + Onboard society ]│
│                 │  NAME         CITY      FLATS  WORKERS/CAP  TIER      MRR     HEALTH        RENEWS       │
│                 │  ────────────────────────────────────────────────────────────────────────────────────────│
│                 │  Palm Grove   Pune       214   61 / inf     PLUS     ₹4,000  ok   92%      Sep 01        │
│                 │  Sunview      Pune        88   31 / 25 (!)  FREE         ₹0  cap exceeded  -             │
│                 │  Lake Vista   Mumbai     160   44 / inf     STANDARD ₹1,500  ok   88%      Aug 22        │
│                 │  Green Acre   Nashik      96   28 / inf     STANDARD ₹1,500  SUSPENDED     overdue 62d   │
│                 │  ────────────────────────────────────────────────────────────────────────────────────────│
│                 │  ┌─ SUNVIEW ───────────────────────────────────────────────────────── [x] close ───────┐ │
│                 │  │ FREE tier · 88 flats · 2 gates · 1 admin (S. Kulkarni) · joined 12 Mar 2026         │ │
│                 │  │ workers 31 of 25 cap  ############################!!!!!!                            │ │
│                 │  │ history 30d (FREE)  ·  reports locked  ·  attendance writes: WORKING                │ │
│                 │  │ 30d: 1,842 gate events · 96.1% sessions from tier 1-2 · 4 disputes · SLA 91%        │ │
│                 │  │ [ Upgrade to STANDARD ]  [ Contact admin ]  [ Suspend... ]  [ Impersonate admin ]   │ │
│                 │  └──────────────────────────────────────────────────────────────────────────────────────┘│
├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────┤
│ > Users         │  USERS       [search: name · phone · flat · worker id .........]  role: all v            │
│                 │  NAME           ROLE          PHONE        SOCIETY      STATE            LAST SEEN       │
│                 │  ────────────────────────────────────────────────────────────────────────────────────────│
│                 │  Sunita Devi    worker        +91 98xxx..  Palm Grove   approved · KYC   Aug 13 07:12    │
│                 │  Rekha More     worker        +91 97xxx..  Sunview      pending approval Aug 12 18:40    │
│                 │  A. Sharma      resident      +91 99xxx..  Palm Grove   active           Aug 13 09:55    │
│                 │  S. Rao         society_admin +91 90xxx..  Palm Grove   active · 2 devs  Aug 13 11:19    │
│                 │  ────────────────────────────────────────────────────────────────────────────────────────│
│                 │  ! Phone numbers masked by default. Reveal is a logged action with a required reason.    │
│                 │    Worker detail shows engagements, sessions, earnings and disputes — never a bank a/c;  │
│                 │    the platform holds no bank or card data (Razorpay does).                              │
└─────────────────┴──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Attendance — the flat problem

A worker enters Palm Grove at 07:02 and leaves at 13:40. In between she worked
four flats. The gate log holds exactly two events. Billing four residents hourly
from two events is not a rounding problem — it is missing data.

The existing schema half-anticipates this: `AttendanceEvent.engagement` is
nullable, and matching uses a generous ±120-minute window because "a worker who
turns up an hour early is still coming for the 9am job." Generous matching is
correct for *gate access*. It is unusable for *billing*, where an event within
two hours of three scheduled visits gives you three equally plausible answers.

### 5.1 The new record

Introduce **`attendance.WorkSession`** — one row per `(engagement, date)`,
holding `started_at`, `ended_at`, `source_tier`, `opened_by`, `closed_by`, and a
`needs_review` flag.

Gate events remain what they are: the society's access record, and the evidence a
session is checked against when disputed. **They are never the billing source.**

### 5.2 Capture tiers

The tiering mirrors the fallback ladder `VerificationMethod` already encodes.
Each session records which tier produced it, because that number is a
platform-health metric (§3.1) and the first thing to look at in a dispute.

| Tier | Mechanism | Reuses | When it fires | Trust |
| --- | --- | --- | --- | --- |
| 1 | Worker taps *Start* in-app, geofenced to the society | `SELF_CHECKIN` | She has a smartphone and data. The default path. | High |
| 2 | Resident scans the worker's printed card at the door | `RESIDENT_SCAN` | No smartphone, or no signal indoors. Both parties present. | Highest |
| 3 | Resident approves a push prompt | new | Worker started but geofence failed; resident confirms. | High |
| 4 | Derived from gate events | `AttendanceEvent` | **Only** when exactly one engagement is plausible in the window. Never when ambiguous. | Medium |
| 5 | Society admin enters it manually, with reason | `REGISTER` | Everything else failed. Logged as a sensitive action. | Low |

> **A failed capture never costs a worker her day.** This extends the rule the
> codebase already states about face matching — a below-threshold match yields
> `PENDING_REVIEW`, never `DENIED`, because "a false rejection means someone
> loses a day's pay for a model's mistake." Same logic: a session that cannot be
> captured is `needs_review` and goes to the resident and the society admin. It
> is never silently a no-show, and never zero. The failure mode of a geofence is
> a notification, not an unpaid day.

### 5.3 The auto-close rule

Forgetting to check out is the most common failure in any attendance product. A
session open past `expected_departure + 90 min` auto-closes **at the expected
departure time**, is flagged `needs_review`, and notifies both parties. It never
bills open-ended, and it never bills more than scheduled. Overtime requires an
affirmative approval (§7.4 rule 5), so the failure mode of forgetfulness is "you
were paid your scheduled hours", not "you billed until midnight."

---

## 6. Mobile screen flows

Flutter, both apps. The worker's app is designed for a mid-range Android on
patchy indoor signal, with a user who may read slowly in English — large targets,
one decision per screen, money and time always in numerals, every state legible
without colour alone.

### 6.1 Worker — Today, running session, month

The screen is a **stack of flats, not a single clock**. This is the direct
consequence of §5: attendance is per engagement, so the primary screen must be
too. One card per flat scheduled today, in time order, each with its own state
and its own single action.

```
┌────────────────────────────────────────┐
│  Today, Thu 13 Aug          [ = ]      │
│  Namaste, Sunita                       │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │ EARNED TODAY        3h 45m       │  │
│  │ ₹ 570                 of 4 flats │  │
│  │ ##########------  2 done, 1 now  │  │
│  └──────────────────────────────────┘  │
│                                        │
│  DONE                                  │
│  ┌──────────────────────────────────┐  │
│  │ A-102  Sharma          ✓ done    │  │
│  │ 07:10 - 09:05          2h 00m    │  │
│  │ ₹ 240 work + ₹60 visit = ₹300    │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │ B-704  Mehta           ✓ done    │  │
│  │ 09:20 - 11:05          1h 45m    │  │
│  │ ₹ 210 work + ₹60 visit = ₹270    │  │
│  └──────────────────────────────────┘  │
│                                        │
│  NOW                                   │
│  ┌──────────────────────────────────┐  │
│  │ C-201  Kulkarni      ● running   │  │
│  │ started 11:30 · 42m so far       │  │
│  │ ends 13:30                       │  │
│  │ ┌──────────────────────────────┐ │  │
│  │ │      STOP WORK  (C-201)      │ │  │
│  │ └──────────────────────────────┘ │  │
│  └──────────────────────────────────┘  │
│                                        │
│  NEXT                                  │
│  ┌──────────────────────────────────┐  │
│  │ D-403  Patil        14:00-16:00  │  │
│  │ ┌──────────────────────────────┐ │  │
│  │ │        START WORK            │ │  │
│  │ └──────────────────────────────┘ │  │
│  │ [ can't go today - ask leave ]   │  │
│  └──────────────────────────────────┘  │
├────────────────────────────────────────┤
│  [Today] [History] [Money] [Me]        │
└────────────────────────────────────────┘
```

```
┌────────────────────────────────────────┐
│  < C-201  Kulkarni                     │
├────────────────────────────────────────┤
│                                        │
│            ●  WORKING                  │
│                                        │
│              1h 58m                    │
│           started 11:30                │
│                                        │
│      scheduled 11:30 - 13:30           │
│      you are 2 minutes over            │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │           STOP WORK              │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  Staying longer?                 │  │
│  │  Ask Mrs Kulkarni to approve     │  │
│  │  extra time before you work it.  │  │
│  │  Extra time without approval is  │  │
│  │  recorded but not paid.          │  │
│  │  ┌────────────────────────────┐  │  │
│  │  │   ASK FOR EXTRA TIME       │  │  │
│  │  └────────────────────────────┘  │  │
│  │   +15m   +30m   +1h              │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ───────────────────────────────────── │
│  checked in by: your phone (GPS ok)    │
│  if this is wrong, tell the guard or   │
│  tap below - you will not lose pay     │
│  [ report a problem with this ]        │
│                                        │
│                                        │
├────────────────────────────────────────┤
│  offline - saved on phone, will sync   │
└────────────────────────────────────────┘
```

```
┌────────────────────────────────────────┐
│  < My work            August 2026  v   │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │ THIS MONTH                       │  │
│  │ 62h 15m worked        ₹ 7,470    │  │
│  │ 48 visits x ₹60       ₹ 2,880    │  │
│  │                      ─────────   │  │
│  │ expected             ₹ 10,350    │  │
│  │ paid on 15 Aug                   │  │
│  └──────────────────────────────────┘  │
│                                        │
│  BY FLAT            hours  visits  pay │
│  A-102 Sharma     24h 00m   22  ₹4,200 │
│  B-704 Mehta      18h 30m   16  ₹3,180 │
│  C-201 Kulkarni   15h 45m    8  ₹2,370 │
│  D-403 Patil       4h 00m    2    ₹600 │
│                                        │
│  DAYS                                  │
│  ┌──────────────────────────────────┐  │
│  │ M  T  W  T  F  S  S              │  │
│  │ .  .  .  .  .  1  2              │  │
│  │ 3  4  5  6  7  8  9              │  │
│  │ #  #  #  #  #  #  -              │  │
│  │ 10 11 12 13                      │  │
│  │ #  #  L  ●                       │  │
│  │                                  │  │
│  │ # worked   L leave   - off       │  │
│  │ ● today    ! needs check         │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ ! 12 Aug, B-704                  │  │
│  │ You did not stop work. We used   │  │
│  │ your usual time, 09:20-11:20.    │  │
│  │ Is that right?                   │  │
│  │  [ yes, correct ]  [ no, fix ]   │  │
│  └──────────────────────────────────┘  │
│                                        │
├────────────────────────────────────────┤
│  [Today] [History] [Money] [Me]        │
└────────────────────────────────────────┘
```

**Three deliberate choices.** Earnings appear on every screen — the worker's
reason to keep using the app rather than reverting to a cash arrangement.
Overtime is framed as *ask before you work it*, so the app never lets someone
work unpaid hours believing otherwise. And the auto-close prompt asks a yes/no
question about her own day rather than presenting a correction she must dispute.

### 6.2 Worker — money, leave, gate card

```
┌────────────────────────────────────────┐
│  < Money                    August  v  │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │ COMING TO YOU                    │  │
│  │ ₹ 10,350                         │  │
│  │ for August · most by 20 Aug      │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ A-102 Sharma            ₹4,200   │  │
│  │ ○ bill sent 15 Aug · due 20 Aug  │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │ B-704 Mehta             ₹3,180   │  │
│  │ ● paid 16 Aug · UPI              │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │ C-201 Kulkarni          ₹2,370   │  │
│  │ ! ₹240 of this is being checked  │  │
│  │                                  │  │
│  │ You get ₹2,130 now. The rest     │  │
│  │ comes when 08 Aug is sorted.     │  │
│  │ You have not lost it.            │  │
│  │ [ see what is being checked ]    │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │ D-403 Patil               ₹600   │  │
│  │ ○ bill sent 15 Aug              │   │
│  └──────────────────────────────────┘  │
│                                        │
│  EARLIER                               │
│  July     ₹9,880    all paid 18 Jul    │
│  June     ₹9,240    all paid 19 Jun    │
│  [ all payments ]                      │
├────────────────────────────────────────┤
│  [Today] [History] [Money] [Me]        │
└────────────────────────────────────────┘
```

```
┌────────────────────────────────────────┐
│  < Ask for leave                       │
├────────────────────────────────────────┤
│  Which day?                            │
│  ┌──────────────────────────────────┐  │
│  │ Thu 14 Aug            [ change ] │  │
│  └──────────────────────────────────┘  │
│                                        │
│  Which homes?                          │
│  [x] A-102 Sharma       07:00-09:00    │
│  [x] B-704 Mehta        09:15-11:00    │
│  [ ] C-201 Kulkarni     11:30-13:30    │
│  [ ] D-403 Patil        14:00-16:00    │
│                                        │
│  Why? (you do not have to say)         │
│  ┌──────────────────────────────────┐  │
│  │ going to my village              │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ WHAT THIS MEANS                  │  │
│  │ 2 homes. You will not earn ₹570  │  │
│  │ that day - ₹450 time and ₹120    │  │
│  │ visit fees.                      │  │
│  │                                  │  │
│  │ Send someone in your place and   │  │
│  │ she is paid instead. You keep    │  │
│  │ the work.                        │  │
│  │ [ suggest a replacement ]        │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │           ASK FOR LEAVE          │  │
│  └──────────────────────────────────┘  │
│  They are told now. If nobody answers  │
│  by tonight, it counts as approved.    │
│  Leave never counts against you.       │
├────────────────────────────────────────┤
│  [Today] [History] [Money] [Me]        │
└────────────────────────────────────────┘
```

```
┌────────────────────────────────────────┐
│  < My card                             │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │          SUNITA DEVI             │  │
│  │        Palm Grove · W#812        │  │
│  │                                  │  │
│  │      ███████ ▄▄█ ███████         │  │
│  │      █ ▄▄▄ █ █▄ █ ▄▄▄ █          │  │
│  │      █ ███ █ ▄█▄ █ ███ █         │  │
│  │      █ ▀▀▀ █ █▄▄ █ ▀▀▀ █         │  │
│  │      ███████ █ ▄ █ ██████        │  │
│  │      ▄▄▄▄ ▄█▄▄█▄█▄█ ▄▄▄▄▄        │  │
│  │      █▄█▄██▄ █▄▄ ▄█▄██▄█▄        │  │
│  │                                  │  │
│  │   approved · works here since    │  │
│  │   12 Mar 2026                    │  │
│  └──────────────────────────────────┘  │
│                                        │
│  Show this at the gate.                │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ NO SIGNAL INSIDE THE BUILDING?   │  │
│  │ Ask the resident to scan this    │  │
│  │ card at their door. It starts    │  │
│  │ your work the same way, and it   │  │
│  │ counts the same for your pay.    │  │
│  └──────────────────────────────────┘  │
│                                        │
│  This code works without internet.     │
│                                        │
│  [ I lost my printed card ]            │
│    a new one is made and the old one   │
│    stops working straight away         │
├────────────────────────────────────────┤
│  [Today] [History] [Money] [Me]        │
└────────────────────────────────────────┘
```

**The Money screen is where a disputed session has to behave well.** C-201 shows
the undisputed ₹2,130 arriving on time and the contested ₹240 held separately, in
her own words — *"You have not lost it."* A worker who believes a query freezes
her whole month will stop raising them, and the dispute channel dies quietly. The
gate card carries the tier-2 fallback from §5: her laminated card and the
resident's phone need no signal between them.

### 6.3 Resident — attendance, session detail, invoice

The resident's job is verification, not administration. Their screen answers one
question — *did she come, and for how long* — and makes the month's arithmetic
inspectable down to the individual session, because an invoice you cannot audit
is an invoice you argue about over WhatsApp.

```
┌────────────────────────────────────────┐
│  < Sunita Devi         August 2026  v  │
│    Cook & cleaning · Mon-Sat           │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │ ● WORKING NOW                    │  │
│  │ arrived 11:30 (on time)          │  │
│  │ 1h 58m · ends about 13:30        │  │
│  │ verified by her phone at your    │  │
│  │ door (GPS)                       │  │
│  └──────────────────────────────────┘  │
│                                        │
│  THIS MONTH SO FAR                     │
│  ┌────────────┬─────────────────────┐  │
│  │ 11 days    │  15h 45m worked     │  │
│  │ of 12      │  scheduled 16h 00m  │  │
│  ├────────────┴─────────────────────┤  │
│  │ 1 leave (approved, 12 Aug)       │  │
│  │ 0 missed                         │  │
│  │ on time 10 of 11 days            │  │
│  └──────────────────────────────────┘  │
│                                        │
│  AUGUST                                │
│  ┌──────────────────────────────────┐  │
│  │  M   T   W   T   F   S   S       │  │
│  │  .   .   .   .   .   1   2       │  │
│  │  3   4   5   6   7   8   9       │  │
│  │  #   #   #   #   ~   #   -       │  │
│  │ 10  11  12  13                   │  │
│  │  #   #   L   ●                   │  │
│  │  # full  ~ short  L leave  - off │  │
│  └──────────────────────────────────┘  │
│                                        │
│  RECENT                                │
│  11 Aug  11:28 - 13:31   2h 00m   >    │
│  10 Aug  11:31 - 13:29   2h 00m   >    │
│  08 Aug  11:52 - 13:30   1h 30m ~ >    │
│  07 Aug  11:30 - 13:30   2h 00m   >    │
│                                        │
│  [ Full history ]   [ August bill > ]  │
├────────────────────────────────────────┤
│  [Home] [Help] [Bills] [Me]            │
└────────────────────────────────────────┘
```

```
┌────────────────────────────────────────┐
│  < Fri 08 Aug            Sunita Devi   │
├────────────────────────────────────────┤
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ SHORT DAY          1h 30m billed │  │
│  │ ₹ 240                            │  │
│  └──────────────────────────────────┘  │
│                                        │
│  WHAT HAPPENED                         │
│  ┌──────────────────────────────────┐  │
│  │ scheduled     11:30 - 13:30      │  │
│  │ arrived       11:52   22m late   │  │
│  │ left          13:30   on time    │  │
│  │ ─────────────────────────────────│  │
│  │ worked        1h 38m             │  │
│  │ rounded       1h 30m  (15m step) │  │
│  │ rate          ₹120 / hour        │  │
│  │ ─────────────────────────────────│  │
│  │ time          ₹180               │  │
│  │ visit fee      ₹60               │  │
│  │ billed        ₹240               │  │
│  │ full day would be ₹300           │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ You are charged for time worked. │  │
│  │ There is no late fee or penalty  │  │
│  │ on top - the ₹60 difference is   │  │
│  │ simply the 30 minutes not worked.│  │
│  │                                  │  │
│  │ The visit fee covers her travel  │  │
│  │ and the time your slot commits.  │  │
│  │ It does not change with the      │  │
│  │ length of the visit.             │  │
│  └──────────────────────────────────┘  │
│                                        │
│  HOW THIS WAS RECORDED                 │
│  in   11:52  her phone, at your door   │
│  out  13:30  her phone                 │
│  gate entry 11:44 · exit 15:10         │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │      THIS DOESN'T LOOK RIGHT     │  │
│  └──────────────────────────────────┘  │
│  Sunita sees the same evidence and     │
│  can accept your version in one tap.   │
│  She is paid the undisputed amount     │
│  now - only the ₹240 is held.          │
├────────────────────────────────────────┤
└────────────────────────────────────────┘
```

```
┌────────────────────────────────────────┐
│  < August bill         Sunita Devi     │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │ INV-4417-08   REVIEW · 42h 15m   │  │
│  │ ₹ 6,700                          │  │
│  │ 16 Jul - 15 Aug                  │  │
│  │ due 20 Aug                       │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ You have until 15 Aug, 6pm to    │  │
│  │ check this. Ask about anything   │  │
│  │ before you pay.                  │  │
│  └──────────────────────────────────┘  │
│                                        │
│  TIME                                  │
│  regular      41h 15m  x ₹120  ₹4,950  │
│  approved     01h 00m  x ₹120    ₹120  │
│    extra time (11 Aug, you approved)   │
│                                        │
│  VISITS                                │
│  visit fee      26 days  x ₹60 ₹1,560  │
│    covers her travel and the time      │
│    your slot commits - same every      │
│    visit, long or short                │
│  ───────────────────────────────────── │
│  subtotal                      ₹6,630  │
│  advance paid 02 Aug             -₹0   │
│  adjustment: Jul session removed  +₹70 │
│    (query #221, resolved 03 Aug)       │
│  ───────────────────────────────────── │
│  TOTAL                         ₹6,700  │
│  Sunita receives               ₹6,700  │
│  platform fee                     ₹0   │
│                                        │
│  DAYS BILLED                26 of 26   │
│  full 23 · short 2 · extra 1 · leave 1 │
│  unbilled extra time    38m (not       │
│  approved - not charged)               │
│  [ see every session > ]               │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │           PAY ₹6,700             │  │
│  └──────────────────────────────────┘  │
│  UPI · card · netbanking (Razorpay)    │
│  [ Ask about this bill ]               │
├────────────────────────────────────────┤
└────────────────────────────────────────┘
```

**The sentence that prevents most disputes** is the one in the middle screen:
*"There is no late fee or penalty on top."* Residents assume a deduction is
punitive and workers fear the same; stating the arithmetic in words, at the
moment the smaller number appears, is cheaper than arbitrating it later. The
invoice likewise surfaces *unbilled* extra time — the resident sees goodwill they
did not pay for, and the worker sees that the app noticed.

### 6.4 Resident — approve extra time, query a session

```
┌────────────────────────────────────────┐
│  ← Sathify                       13:32 │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │ Sunita is asking to stay longer  │  │
│  │ C-201 · she is still working     │  │
│  └──────────────────────────────────┘  │
│                                        │
│  She was due to finish at 13:30.       │
│  She has asked for 30 more minutes.    │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ IF YOU APPROVE                   │  │
│  │ 30 min  x ₹120          + ₹60    │  │
│  │ no second visit fee        ₹0    │  │
│  │ ─────────────────────────────────│  │
│  │ today becomes            ₹360    │  │
│  │   ₹240 time · ₹60 visit · ₹60 extra││
│  └──────────────────────────────────┘  │
│                                        │
│  ┌────────────────┬─────────────────┐  │
│  │  APPROVE 30m   │   APPROVE 15m   │  │
│  └────────────────┴─────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │            NO, THANK YOU         │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ If you do not answer, she is     │  │
│  │ not charged for the extra time   │  │
│  │ - and she is told that before    │  │
│  │ she works it, not after.         │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ [ ] always approve up to 30 min  │  │
│  │     for Sunita                   │  │
│  └──────────────────────────────────┘  │
├────────────────────────────────────────┤
└────────────────────────────────────────┘
```

```
┌────────────────────────────────────────┐
│  < Ask about 08 Aug                    │
├────────────────────────────────────────┤
│  Fri 08 Aug · Sunita Devi · ₹240       │
│                                        │
│  What looks wrong?                     │
│  ( ) She did not come at all           │
│  (o) The times are wrong               │
│  ( ) She left much earlier             │
│  ( ) Something else                    │
│                                        │
│  What do you think happened?           │
│  ┌──────────────────────────────────┐  │
│  │ She reached about 11:40, not     │  │
│  │ 11:52. The lift was out.         │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │ WHAT HAPPENS NEXT                │  │
│  │                                  │  │
│  │ 1  You both see the gate record  │  │
│  │    for that day.                 │  │
│  │    gate entry 11:44              │  │
│  │                                  │  │
│  │ 2  Sunita can agree in one tap,  │  │
│  │    and it is corrected on next   │  │
│  │    month's bill. Most queries    │  │
│  │    end here.                     │  │
│  │                                  │  │
│  │ 3  If you still disagree after   │  │
│  │    48 hours, your society admin  │  │
│  │    decides. Sathify does not.    │  │
│  │                                  │  │
│  │ She is paid the rest of the      │  │
│  │ month now. Only this ₹240 waits. │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │            SEND QUERY            │  │
│  └──────────────────────────────────┘  │
├────────────────────────────────────────┤
└────────────────────────────────────────┘
```

**These two screens are where the billing engine actually lives.** The approval
prompt is the only thing standing between §7.4 rule 5 and a worker doing unpaid
overtime, so it states the resulting total before the tap and shows that no
second visit fee applies — she is already there. The query screen publishes the
escalation ladder in advance, including the sentence that keeps the platform out
of the facts: *"your society admin decides. Sathify does not."*

---

## 7. The billing engine

Two rules govern the module.

**Pay tracks time worked, and nothing else** — lateness reduces pay only by the
minutes not worked, with no fine, multiplier or penalty line anywhere. A platform
that levies wage fines on domestic workers is inventing a liability it does not
need.

**Every visit carries a fixed fee alongside the hourly rate**, because her cost
of turning up does not shrink when the job is short. The first rule keeps the
engine defensible; the second, derived below, is what stops it quietly underpaying
short jobs.

### 7.1 Terms

`RecurringTerms` gains a `rate_basis` of `monthly` or `hourly`, plus
`hourly_rate` and `visit_fee` (whole rupees, matching the existing convention
that residents and workers agree in rupees out loud). Existing engagements stay
`monthly` and are untouched. Nothing is migrated silently.

Why there are *two* numbers rather than one is the next section, and it is the
most important decision in this module.

### 7.2 The short-visit problem

A flat hourly rate prices the resident's value correctly and the worker's cost
incorrectly. Her cost per visit is not proportional to its length. Travel,
waiting at the gate, getting into and out of the building, and the slot she has
committed are all **fixed** — they cost the same whether the job is one hour or
four.

Put numbers on it. At ₹120/hour, with roughly 30 minutes of travel and entry
overhead per visit:

| Job length | She is paid | Her committed time | Effective rate |
| --- | ---: | ---: | ---: |
| 1 hour | ₹120 | 1h 30m | **₹80/hr** |
| 2 hours | ₹240 | 2h 30m | ₹96/hr |
| 3 hours | ₹360 | 3h 30m | ₹103/hr |
| 4 hours | ₹480 | 4h 30m | ₹107/hr |

So the short job is not merely worth less in total — it is worth less *per hour
of her day*, and the gap is large. A one-hour engagement pays her a third less
per committed hour than a four-hour one at exactly the same advertised rate.

The second-order effect is worse than the unfairness itself. She cannot see the
mechanism, but she can feel the outcome, so she rationally deprioritises short
engagements — arrives late to them, drops them first when a longer job appears,
quietly stops showing up. **The resident experiences a pricing failure as
unreliability**, blames the worker, and the platform's own attendance metrics
record it as her fault.

#### Three ways to fix it

| Option | Mechanism | Why not / why yes |
| --- | --- | --- |
| Minimum billable duration | Every visit bills at least 2 hours | Blunt. Kills genuinely short jobs outright, and a resident who paid for two hours will demand two hours of work whether or not there is any. |
| Tapered hourly rate | ₹200 for the first hour, ₹100 thereafter | Right shape, wrong ergonomics — neither party can compute their own bill, which is fatal for a product whose main job is making the number arguable-with. |
| **Two-part tariff** *(recommended)* | A fixed **visit fee** per visit, plus the hourly rate for time worked | Matches the actual cost structure exactly, stays trivially explicable, and can be calibrated so the unfairness disappears rather than merely shrinks. |

#### Calibrating the visit fee

The fee is not a guess. Let **R** be the hourly rate, **T** the fixed overhead per
visit in hours, **H** the hours worked, and **F** the visit fee. Her effective
earnings per hour of committed time are:

```
effective_rate(H) = (F + R·H) / (H + T)
```

Require that to equal **R** for every **H** — that is, require her effective rate
to be independent of how long the job is:

```
F + R·H  =  R·(H + T)
F + R·H  =  R·H + R·T
      <b>F  =  R × T</b>
```

Not an approximation. Setting the visit fee to **the hourly rate times the fixed
overhead** makes her effective rate exactly constant across every job length. At
₹120/hour and 30 minutes of overhead, the visit fee is **₹60**:

| Job length | Visit fee | Time | Total | Committed | Effective rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 hour | ₹60 | ₹120 | ₹180 | 1h 30m | **₹120/hr** |
| 2 hours | ₹60 | ₹240 | ₹300 | 2h 30m | **₹120/hr** |
| 3 hours | ₹60 | ₹360 | ₹420 | 3h 30m | **₹120/hr** |
| 4 hours | ₹60 | ₹480 | ₹540 | 4h 30m | **₹120/hr** |

The resident needing one hour now pays ₹180 rather than ₹120. That is the correct
price, and it is the same structure a plumber's call-out charge or a taxi's
flagfall encodes — nobody finds either mysterious.

> **Set T per society, never per worker.** The obvious refinement — scale the fee
> by how far each worker travels — must be rejected. It would make workers who
> live further away more expensive to hire, and in this market the workers who
> live furthest out are almost always the poorest. A distance-priced visit fee
> would quietly convert a worker's address into a hiring penalty. **T is a single
> society-level constant** (default 30 minutes, set at onboarding from how far
> the society's staff typically travel), so every worker at that society carries
> the same fee.

#### Density is hers to keep

Recall §5: she enters the society once and serves four flats. She therefore
incurs **T** once but earns four visit fees, and her effective rate on a dense day
rises well above R. Keep it. Do not claw it back.

- It is precisely the platform's value proposition to her — *we fill your day
  inside one building* — and the strongest reason she has not to drift back to a
  cash arrangement.
- Clawing it back would make resident A's bill depend on whether resident B
  booked that morning. Bills stop being predictable, and the mechanism is
  immediately gameable.

Config `VISIT_FEE_POLICY` allows `first_of_day` or `shared_pro_rata` for
societies that insist, but the default and the recommendation is
`per_engagement`.

#### What the visit fee replaces

It subsumes the minimum-session rule, and does it better:

- **Cancelled at the door** — visit fee owed, no hourly. She travelled; that is
  exactly what the fee is for.
- **Cancelled with ≥12 hours' notice** — nothing owed. She can refill the slot.
- **Worker no-show** — nothing owed, and the fee is forfeit with the time.
- **Very late arrival** — fee still owed in full. She travelled regardless, and
  the hourly shortfall (rule 2) is already the whole of the correction.

> **Call it a visit fee, never a travel charge.** "Travel charge" invites
> haggling about distance and turns the worker's home address into a negotiating
> position — the same harm as per-worker pricing, arriving through the copy
> instead of the config. The resident-facing label is **Visit fee**, with the
> sub-line *"covers her travel and the time your slot commits."* It appears as
> its own line on every invoice, never folded into an hourly figure.

### 7.3 Inputs per session

All from models that already exist, except the session itself:

| Input | Source | Default |
| --- | --- | --- |
| `expected_arrival` / `expected_departure` | `scheduling.TaskTiming` | from engagement |
| `arrival_grace_minutes` | `scheduling.TaskTiming` | 10 |
| `departure_grace_minutes` | `scheduling.TaskTiming` | 10 |
| `started_at` / `ended_at` | `attendance.WorkSession` *(new)* | — |
| `hourly_rate` | `hiring.RecurringTerms` *(new)* | — |
| `visit_fee` | `hiring.RecurringTerms` *(new)* | R × T |
| `VISIT_OVERHEAD_MINUTES` (T) | society config | 30 |
| `VISIT_FEE_POLICY` | society config | `per_engagement` |
| `ROUND_MINUTES` | society config | 15 |
| `OT_MULTIPLIER_BP` | society config, basis points | 10000 (1.0×) |

### 7.4 The nine rules

1. **Grace absorbs the gate.** If `started_at <= expected_arrival + grace`,
   billing starts at `expected_arrival`. A queue at the gate and a slow lift are
   not the worker's fault.
2. **Past grace, the clock is honest.** Billing starts at `started_at`. The
   shortfall *is* the deduction. Nothing further is applied.
3. **Early arrival does not start the clock** unless the resident approves it,
   using the same approval flow as overtime. She is not paid for it, and equally
   not penalised.
4. **Early departure is symmetric** to rule 2 — billing ends at `ended_at`.
5. **Overtime must be approved before it is worked.** Requested from the worker's
   app, or auto-prompted to the resident 10 minutes after `expected_departure`.
   Approved OT bills at `OT_MULTIPLIER_BP`. Unapproved extra time is recorded,
   shown to both parties, and not billed.
6. **Round once, at the session.** Nearest `ROUND_MINUTES`, half-up. Never at the
   month, never twice. Regular and OT round separately.
7. **Every visit carries the visit fee.** One `visit_fee` per session,
   unconditional on how long she stayed or how late she arrived, and **never
   rounded or pro-rated** — a fee for a fixed cost that is scaled by duration is
   just an hourly rate wearing a disguise.
8. **Cancellation splits on notice.** Cancelled at the door bills the visit fee
   alone. Cancelled with ≥12 hours' notice bills nothing.
9. **No show, no bill.** No session and no leave request is `NO_SHOW`: zero
   billable including the fee, counted against reliability. Approved leave is
   zero billable with no reliability effect, and settles through the existing
   `LeaveRequest` path.

> **On rounding direction.** Default is **nearest**, not up. Nearest is symmetric
> — neither side can claim the app leans against them — and it is the only mode
> where a genuine shortfall actually registers, which is what the deduction
> requirement asks for. Societies may set `ROUNDING_MODE = UP` as a
> worker-favouring option; at ₹120/hour it costs a resident at most ₹30 per
> session and removes a class of "the app shaved my time" arguments. Surface it
> during society onboarding as an explicit committee choice rather than a buried
> default.

### 7.5 The arithmetic, in paise

Integers only, consistent with the module's stated rule that floats drift and
eventually fail to reconcile against Razorpay. Minutes are rounded first, then
converted, with an explicit half-up on the final division. The visit fee is added
last and untouched by rounding:

```python
# apps/payments/hourly.py

ROUND_MINUTES = 15
HALF = ROUND_MINUTES // 2

def round_minutes(m: int) -> int:
    """Nearest ROUND_MINUTES, half-up. Applied once, per session."""
    return ((m + HALF) // ROUND_MINUTES) * ROUND_MINUTES

def session_paise(minutes: int, hourly_rate_paise: int, *, multiplier_bp: int = 10_000) -> int:
    """Minutes -> paise. Half-up on the division so no fraction is silently lost."""
    numerator = hourly_rate_paise * minutes * multiplier_bp
    denominator = 60 * 10_000
    return (numerator + denominator // 2) // denominator

def billable(session, timing, terms, cfg) -> tuple[int, int, int]:
    """Returns (regular_minutes, approved_ot_minutes, unbilled_extra_minutes)."""
    start = session.started_at
    if start <= timing.arrival + timedelta(minutes=timing.arrival_grace_minutes):
        start = timing.arrival                      # rule 1
    start = max(start, timing.arrival)              # rule 3

    end = min(session.ended_at, timing.departure)   # rule 4
    regular = max(0, minutes_between(start, end))

    over = max(0, minutes_between(timing.departure, session.ended_at))
    approved = min(over, session.approved_ot_minutes)
    unbilled = over - approved                      # rule 5: recorded, not charged

    regular = round_minutes(regular)                # rule 6
    approved = round_minutes(approved)
    return regular, approved, unbilled

def calibrated_visit_fee(hourly_rate_paise: int, overhead_minutes: int) -> int:
    """F = R x T. The fee that makes her effective rate independent of job length."""
    return (hourly_rate_paise * overhead_minutes + 30) // 60

def session_total_paise(session, timing, terms, cfg) -> int:
    regular, approved, _unbilled = billable(session, timing, terms, cfg)
    rate = rupees_to_paise(terms.hourly_rate)

    total = session_paise(regular, rate)
    total += session_paise(approved, rate, multiplier_bp=cfg.OT_MULTIPLIER_BP)

    # rule 7: one fee per visit, flat, never pro-rated by duration.
    # rule 8: at-the-door cancellation reaches here with zero worked minutes
    # and still owes the fee; a >=12h cancellation never creates a session.
    if session.status != SessionStatus.NO_SHOW:      # rule 9
        total += rupees_to_paise(terms.visit_fee)
    return total
```

### 7.6 Worked example

Sunita, C-201, ₹120/hour (`12000` paise), visit fee ₹60 (= ₹120 × 30 min).
Scheduled 09:00–12:00, 10-minute grace. She arrives 09:14, leaves 12:41. The
resident approved 30 minutes of extra time at 11:55.

| Step | Working | Result |
| --- | --- | ---: |
| Arrival vs grace | 09:14 > 09:10 → past grace, clock starts at actual (rule 2) | start 09:14 |
| Regular window | 09:14 → 12:00 | 166 min |
| Round regular | nearest 15 → 165 is 1 min away, 180 is 14 away | 165 min |
| Extra time worked | 12:00 → 12:41 | 41 min |
| Approved portion | resident approved 30 → 11 min unbilled, shown to both | 30 min |
| Regular pay | `(12000 × 165 × 10000 + 300000) // 600000` | 33,000 p · ₹330.00 |
| Overtime at 1.0× | `(12000 × 30 × 10000 + 300000) // 600000` | 6,000 p · ₹60.00 |
| Visit fee | flat, unrounded, unaffected by her lateness (rule 7) | 6,000 p · ₹60.00 |
| **Session total** | — | **45,000 p · ₹450.00** |

The scheduled three hours would have billed ₹420 (₹360 + ₹60 fee). She was 14
minutes late, which cost ₹30 — exactly the quarter-hour she did not work — and
earned ₹60 for approved extra time, netting ₹450. The 11 unapproved minutes
appear on both apps as extra time worked and not charged. **No line item anywhere
is a penalty**, and the fee she is owed for turning up does not shrink because
she turned up late.

### 7.7 Invoice lifecycle

New model `payments.Invoice`, one per `(engagement, billing period)`, with
`InvoiceLine` rows pointing at sessions. It is a wrapper around the existing
`Payment`, not a replacement — on issue it creates exactly one `Payment` of kind
`ENGAGEMENT_SALARY`, inheriting `receipt_number`, `due_at`, `period_start/end`,
the Razorpay flow, and `settled_via` unchanged.

- `DRAFT` — accrues sessions through the period. Visible live to both parties; no
  amount is a surprise on the last day.
- `REVIEW` — 48-hour window at period close. Either party may flag any session;
  flags run the three-stage ladder in §9.4a, and the undisputed remainder is
  issued and payable immediately.
- `ISSUED` — sessions frozen. A `Payment` exists and is payable.
- `SETTLED` — mirrors the `Payment`, which reaches `PAID` only via a
  signature-verified message or an admin-confirmed UTR.

> **Corrections never edit history.** After `ISSUED`, a correction is an
> **adjustment line on the next invoice**, carrying the query id that produced it
> — visible on the resident's invoice screen as *"adjustment: Jul session removed
> +₹70 (query #221)"*. This follows the rule `AttendanceEvent` already sets: a
> wrong entry is corrected by a superseding one. It also means a resident who
> queries a three-month-old charge is shown the number that actually happened.

### 7.8 Keeping the day-rate models working

`LeaveRequest.day_rate_paise` and `ReplacementSplit.split(day_rate_paise)` both
assume a day rate exists. Under hourly terms it must be *derived*, not stored a
second time — and it must include the visit fee, or a replacement worker covering
a single day would be paid for her hours but not her journey, reintroducing
exactly the unfairness §7.2 removes:

```python
def day_rate_paise(engagement, day) -> int:
    if engagement.rate_basis == RateBasis.MONTHLY:
        return existing_monthly_day_rate(engagement, day)   # unchanged
    # hourly: that day's scheduled minutes, plus the one visit it requires
    minutes = engagement.scheduled_minutes_on(day)
    rate = rupees_to_paise(engagement.hourly_rate)
    return session_paise(minutes, rate) + rupees_to_paise(engagement.visit_fee)
```

One function, two branches, called by both leave settlement and replacement
split. Neither of those modules learns that hourly exists.

---

## 8. End to end — mark, calculate, reflect

One day, one session, from the worker's tap to the row a Superadmin reconciles
three weeks later.

1. **She taps Start at C-201** *(worker app)* — the card for C-201 is already on
   her Today screen because `Engagement.occurs_on(day)` put it there. The tap
   writes a `WorkSession` locally with a client-generated UUID, exactly as gate
   events do: the record exists before the server has heard of it, so nothing
   depends on signal at a stairwell.

2. **Capture tier is resolved** *(device → server)* — GPS inside the society
   geofence gives `source_tier = 1`. Outside it, or GPS unavailable, the app falls
   through: the resident scans her card (tier 2), or gets a push to approve
   (tier 3). If none resolve, the session still opens, flagged `needs_review`.
   **She is never blocked from starting work.**

3. **The session syncs and is matched** *(backend)* — on reconnect the queued
   session replays against an idempotent endpoint; the UUID makes a duplicate
   replay a no-op. The server links it to the engagement and, where one exists, to
   the day's gate `AttendanceEvent` — as corroboration, not as the source.
   Divergence is recorded, not resolved automatically.

4. **She taps Stop, or the system closes it** *(worker app / job)* — `ended_at`
   is written. If she forgets, the nightly job closes it at `expected_departure`
   and flags `needs_review`, and the "is that right?" prompt appears on her
   History screen. Overtime already approved by the resident carries
   `approved_ot_minutes`; anything past it is recorded as unbilled.

5. **Billable minutes are computed** *(payments.hourly)* — rules 1–9 run once, at
   session close, and the outcome is stored on the session, not recomputed on
   read. A resident opening a session from six weeks ago sees the arithmetic that
   was applied, even if the society has since changed its rounding config.

6. **The line lands on the draft invoice** *(payments.Invoice)* — the `DRAFT`
   invoice for the period gains an `InvoiceLine`. Both apps show the running total
   the same day, which is what stops the month-end number from being a
   negotiation.

7. **Period closes, review window opens** *(both apps)* — 48 hours in `REVIEW`. A
   flagged session runs the three-stage ladder in §9.4a. Crucially the
   **undisputed remainder issues and pays on time**; only the contested lines
   wait.

8. **Invoice issues, Payment is created** *(payments)* — sessions freeze. One
   `Payment` row: `kind = ENGAGEMENT_SALARY`, `amount_paise` = sum of time lines
   plus one visit fee per session, `platform_fee_paise = 0`, so
   `worker_receives_paise` is the full wage. `due_at` from the existing
   `payment_due_at` helper.

9. **The resident pays** *(razorpay)* — status reaches `PAID` only through a
   signature-verified checkout response or webhook, or, on the UPI path, a society
   admin confirming a UTR against a bank statement, which stamps
   `settled_via = UTR` and is the flag the console filters on.

10. **It surfaces on the Superadmin console** *(web)* — three places, immediately:
    the *Transactions* ledger as a row with its settlement path marked (Plate 02);
    *GMV settled* on Overview, never Revenue, since the platform earns nothing on
    wages; and the *Activity* feed as `payment.paid`. If the webhook never lands,
    it appears instead in *Needs attention* as a reconciliation gap. If the session
    was tier 4 or 5, or auto-closed, it feeds the **billing integrity** metric —
    the leading indicator of whether the platform's wage numbers are trusted, and
    the one number worth watching weekly.

---

## 9. Schema, rollout, decisions

### 9.1 Changes required

| App | Change | Kind | Notes |
| --- | --- | --- | --- |
| `accounts` | `Role.SUPERADMIN` + `SuperadminProfile` (Support / Finance) | New | Fix `create_superuser`, which currently defaults to `SOCIETY_ADMIN`. |
| `accounts` | `ImpersonationGrant` — time-boxed, reason-gated, logged | New | The only write path into a society's data. |
| `core` | `PlatformScoped` access path + `PlatformAccessLog` | New | One seam through `SocietyScopedModel`, not many. |
| `attendance` | `WorkSession` + `SessionFlag` | New | Client-generated UUID PK, mirroring `AttendanceEvent`. |
| `hiring` | `RecurringTerms.rate_basis`, `.hourly_rate`, `.visit_fee` | Migration | Needs `db_default` — see the hazard note below. |
| `payments` | `Invoice`, `InvoiceLine`, `hourly.py` | New | Wraps `Payment`; does not replace it. |
| `payments` | `day_rate_paise()` made basis-aware | Refactor | Called by leave settlement and replacement split. |
| `payments` | `WageFloor` — statutory minimum per state | New | Blocks terms below the floor at creation (§9.4c). |
| `payments` | `DisputeFlag` + partial-issue on `Invoice` | New | Lets the undisputed remainder pay on time (§9.4a). |
| `societies` | `SocietyBillingConfig` — `VISIT_OVERHEAD_MINUTES`, `ROUND_MINUTES`, `OT_MULTIPLIER_BP`, `VISIT_FEE_POLICY` | New | One row per society. Set at onboarding, versioned — a config change must never retroactively alter a settled invoice. |
| `administration` | `reports.build()` wrapped in an async multi-society job | Extend | Keep the renderers; change the caller. |

> **Migration hazard, already learned the hard way.** `payments/models.py`
> documents an outage: columns were added without a database-level default on a
> database shared between a developer machine and the deployed instance, and every
> payment insert failed — which meant every emergency request failed. All three
> new columns on `RecurringTerms` must carry `db_default`. This is not a style
> preference; it is a repeat of a known incident.

### 9.2 Sequencing

Four shippable phases, each independently useful.

**Phase 1 — Console, read-only.** Superadmin role, scoping seam, activity log,
transactions ledger, societies and users. No impersonation, no writes. Delivers
the reconciliation workflow, which is the daily pain, and validates the scoping
seam under real load before anything can mutate through it.

**Phase 2 — WorkSession.** Per-engagement sessions on existing **monthly**
engagements, where they change no money. Attendance transparency ships to
residents, capture-tier distribution becomes measurable, and the billing engine
gets months of real data before a rupee depends on it.

**Phase 3a — Hourly on bookings.** Terms, engine, visit fee, invoices. Ships
first on **one-day bookings**, where jobs are short, the visit fee matters most,
and there is no ongoing relationship to disintermediate. The lowest-risk place to
learn whether two-part pricing reads as fair.

**Phase 3b — Hourly on engagements.** Opt-in per engagement, only in societies
clearing 90% tier-1/2 capture in Phase 2, and only with both parties agreeing
in-app. Monthly stays the default and nothing migrates silently. Gated on the
90-day survival comparison in §9.4b.

Phase 2 before Phase 3 is the load-bearing decision. Shipping hourly billing on a
session record with unmeasured capture reliability means discovering the
geofence's false-negative rate through wage disputes — and the party who pays for
that discovery is the worker.

### 9.3 Success measures

- **Capture quality** — tier 1+2 share of sessions. Target >90%. Below that,
  hourly billing is not trustworthy in that society and should not be enabled.
- **Auto-close rate** — sessions closed by the job, not a human. Target <5%.
  Rising means the stop-work flow is failing, usually indoor signal.
- **Dispute rate** — flagged sessions per 100. Target <1.5%, and falling after
  month two.
- **Reconciliation lag** — median hours from gateway paid to webhook processed.
  The console's whole reason for existing.
- **Effective-rate spread** — the gap in ₹/committed-hour between the shortest and
  longest engagement decile. The direct measure of whether §7.2 worked; target
  under 5%, and near zero if **T** is calibrated correctly. Rising spread means
  the society's `VISIT_OVERHEAD_MINUTES` no longer matches how far its staff
  actually travel.
- **Worker earnings visibility** — share of workers opening the Money tab weekly.
  The proxy for whether the app is worth keeping over a cash arrangement.

### 9.4 Decisions

Four questions this document previously left open, now resolved. Each records the
call and the reasoning, so a future reader can tell a decision from an accident.

#### 9.4a — A disputed session is never arbitrated by the platform

Putting a volunteer committee member between a resident and a worker over money
is a real cost, so the design's job is to make sure almost nothing reaches them.
Three stages, in order:

1. **Evidence, shown to both.** If the flagged session is tier 1 or 2 *and*
   corroborated by a gate event, both parties see the same record side by side.
   Most flags are a misremembered time and end here without anyone deciding
   anything.
2. **Bilateral acceptance.** Either party can accept the other's version in one
   tap; the correction becomes an adjustment line on the next invoice (§7.7). No
   third party is involved, and both sides keep their standing.
3. **Society admin, after 48 hours.** Only unresolved flags escalate. They already
   hold the complaint SLA queue, they are physically present, and this is the
   surface the subscription already sells.

> **The rule that makes the ladder safe.** A dispute over one session must never
> freeze a month's wages. The invoice **issues and pays the undisputed remainder
> on schedule**; only the contested lines are held (`DisputeFlag` plus
> partial-issue on `Invoice`). A worker who believes raising a query risks her
> whole month will simply never raise one — and then the dispute channel is
> decorative, the numbers stop being challenged, and the platform's record
> quietly stops being true. §6.2 states this to her in her own screen: *"You have
> not lost it."*

#### 9.4b — Hourly ships on bookings first; monthly stays the default

The monetisation doc's observation stands: recurring domestic work
disintermediates fast, and per-session billing adds friction to a relationship
that already wants to become cash. So hourly is **not** a migration.

- **Phase 3a — one-day bookings.** Genuinely transactional, usually short, no
  relationship to lose, and the segment where the visit fee does the most work. If
  two-part pricing is going to read as unfair, it shows up here first and cheaply.
- **Phase 3b — engagements, opt-in only**, in societies clearing 90% tier-1/2
  capture, with both parties agreeing in-app.
- **Kill criterion, set in advance:** compare 90-day engagement survival for
  hourly vs monthly cohorts. If hourly churns materially faster, stop at 3a and
  keep engagements monthly. Deciding this now is what stops it being re-argued
  from whichever number looks better later.

#### 9.4c — The wage floor is enforced in code, on the effective rate

A platform that computes wages sits closer to the calculation than one recording
a figure two people agreed, so the exposure is real and is handled structurally
rather than in a disclaimer:

- A `WageFloor` per state; terms below it cannot be created, with the block shown
  at the point of agreement rather than as a later rejection.
- The check runs on the **effective** rate — which is exactly why §7.2's
  calibration matters. With `F = R × T`, effective rate equals **R** at every job
  length, so one comparison answers compliance for every engagement. Under a flat
  hourly rate, short jobs could sit below the floor on an effective basis while
  the stored number looked compliant.
- The agreed rate is stored on the engagement and **frozen onto every invoice**,
  so a rate change never rewrites history.
- Counsel review is a gate on Phase 3b, not 3a — bookings carry a fraction of the
  exposure.

#### 9.4d — PII stays masked, and societies can watch us look

Confirmed as the standard, on the reasoning that this is materially harder to
tighten later than to loosen: phone numbers masked by default, reveal is a logged
action requiring a stated reason, and the reveal expires with the session rather
than persisting. Report exports treat PII columns as separately audited opt-ins.

One addition beyond the original question: **a society can see its own
`PlatformAccessLog`** — when platform staff read its residents' or workers'
records, and why. It costs one read-only view, and it converts the console's most
uncomfortable capability into something a committee can verify rather than trust.
Given that §3.6 grants Superadmin cross-society read on a schema built to prevent
exactly that, being watchable is the honest price of the bypass.
