# Contributing to Sathify

Four people, twelve modules, one repository. This file is the short version of
how that works without anyone standing on anyone else's changes.

Setup lives in [README.md](README.md) — start there if you have not run
`python scripts/setup.py` yet.

---

## The one rule everything else follows from

**A module is one Django app plus one Flutter feature folder, and it has one
owner.**

```
Module 5 — Bookings  =  backend/apps/bookings/  +  mobile/lib/features/bookings/
```

Work inside your own module and you will almost never hit a merge conflict.
That is the whole design: the repository is laid out so four people can work
at once without coordinating every hour.

Fill in the owners here at your first meeting, and mirror them in
[.github/CODEOWNERS](.github/CODEOWNERS) so GitHub requests the right reviewer
automatically:

| Module | Django app | Flutter feature | Owner |
| --- | --- | --- | --- |
| 1 — Identity & Access | `accounts` | `auth` | |
| 2 — Society & Resident Onboarding | `societies` | `societies` | |
| 3 — Worker Onboarding & KYC | `workers` | `workers` | |
| 4 — Discovery & Hiring | `hiring` | `hiring` | |
| 5 — One-Day Service Booking | `bookings` | `bookings` | |
| 6 — Scheduling & Task Management | `scheduling` | `scheduling` | |
| 7 — Attendance & Gate Verification | `attendance` | `attendance` | |
| 8 — Payments & Payouts | `payments` | `payments` | |
| 9 — Ratings & Trust Score | `ratings` | `ratings` | |
| 10 — Notifications | `notifications` | `notifications` | |
| 11 — Admin, Reporting & Complaints | `administration` | `administration` | |
| 12 — AI Layer | `ai_services` | `ai` | |

### The shared files

These belong to nobody, and they are where every conflict comes from. Say so in
your PR when you touch one, and give the others a heads-up first if the change
is large:

```
backend/apps/core/          backend/config/settings/    backend/config/urls.py
backend/requirements/       backend/conftest.py         mobile/pubspec.yaml
mobile/lib/core/            mobile/lib/shared/          .github/
```

Never reach into another module's folder. If you need something from Module 4,
either call its API or ask its owner to expose what you need — do not import
across feature folders. Genuinely shared code goes in `apps/core/` or
`lib/core/`, and that is a conversation, not a unilateral move.

---

## Day-to-day

### Claim the work first

Open an issue (the **Task** template) and assign yourself *before* you start.
Two people independently building the same screen is the most expensive thing
that can happen on a four-person project, and it is completely avoidable.

### Branch

Never commit to `main`. Branch names carry the module so `git branch -a` reads
as a status board:

```
git switch -c bookings/slot-picker
git switch -c attendance/fix-offline-queue
git switch -c core/error-envelope        # shared code — flag it
```

`<module>/<short-description>`, lowercase, hyphens.

### Before you push

```bash
# backend changes
cd backend
python manage.py makemigrations --check --dry-run   # forgot a migration?
python -m pytest -m "not ml"

# mobile changes
cd mobile
flutter analyze
flutter test
```

CI runs exactly these on every pull request, so running them locally just means
finding out in thirty seconds instead of three minutes.

### Pull request

Open one even for small things — it is the only place the other three see what
changed. Fill in the template, keep it to one module where you can, and get one
review before merging. Squash-merge, so `main` reads one commit per change.

### Keep up with `main`

```bash
git switch main && git pull
git switch your-branch && git rebase main
```

Rebase daily-ish. A branch that has drifted for a week is where the painful
conflicts live.

---

## Things that bite on this project specifically

### Migrations

- One migration per pull request, and let Django name it.
- **Never edit or delete a migration that is already on `main`** — everyone
  else has applied it. Fix it forward with a new one.
- If two branches both add a migration to the same app, the second to merge
  rebases and regenerates. Do not hand-edit the dependency graph.
- CI fails the build when models and migrations disagree, which is the usual
  way this gets caught.

### `.env`

- `.env` files are git-ignored and personal. Never commit one.
- **Add every new setting to `.env.example`** with a comment saying what it is
  and where to get it. A setting that exists only in your `.env` works
  perfectly for you and breaks for the other three.
- Every value must have a working default, so a fresh clone runs with an
  unedited `.env`. CI checks this on every PR.
- `mobile/.env` is bundled into the APK and is readable by anyone who downloads
  it. **No secrets there, ever** — only the API base URL and the Razorpay
  *public* key id.

### The heavy CV stack

`requirements/ml.txt` is several GB and only Modules 3 and 7 need it. Everyone
else works without it: OCR and face verification report themselves unavailable
and fall back to manual entry, which is a supported state, not a broken one.

Working on OCR Stages 1, 2 or 5-8? `requirements/cv.txt` is the ~60 MB OpenCV
half on its own.

### Never let an automated check refuse a person

A face match below threshold, a GPS fix outside the geofence, an unreadable
Aadhaar scan — all of these produce *"a human decides"*, never a denial. This
is not a style preference. The measurements are not accurate enough to cost
somebody a day's wages, and the people they misread most are the ones with the
least recourse. Any code that turns a low confidence score into a rejection
will be sent back.

### Graceful degradation is written at the same time

Every AI, OCR and face call ships with its manual fallback in the same pull
request. `ai_services.degradation.with_fallback` takes the fallback as a
*required* argument, so this is enforced rather than remembered.

---

## Protecting `main` (do this once, together)

Nothing above stops someone force-pushing over `main` at 2am. Ten minutes in
**Settings → Branches → Add branch ruleset** does:

- Require a pull request before merging — 1 approval.
- Require status checks to pass: `Backend (Django)` and `Mobile (Flutter)`.
- Require branches to be up to date before merging.
- Block force pushes and deletions.

Optionally "Require review from Code Owners" once CODEOWNERS has real usernames
in it. Keep "Allow specified actors to bypass" empty — including for whoever
owns the repository, since the whole point is that nobody merges a red build by
accident at midnight before a demo.

---

## Code style

Match what is already there. Both halves of this codebase explain *why* a
decision was made rather than restating what the line does, and that convention
is worth more than any linter here — a comment saying "pinned <2.3 for
paddle/TF compatibility" saves the next person an afternoon.

- **Python** — Django and DRF conventions, four spaces, type hints on new
  functions. Business rules live in `services.py`, not in views or serializers.
- **Dart** — `flutter analyze` is the arbiter; `analysis_options.yaml` already
  makes the rules that matter fatal. Feature folders keep the
  `data/` / `domain/` / `presentation/` split.
- **Tests** — a module's tests live with the module. `backend/conftest.py`
  already gives you an authenticated client for any of the four roles in one
  line; use those fixtures rather than building users by hand.
