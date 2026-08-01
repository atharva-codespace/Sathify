# Sathify

**Smart Society Domestic Workforce Management System** — a mobile platform for
managing domestic workers (maids, cooks, cleaners) hired by residents of Indian
apartment societies: verified onboarding, hiring, attendance, payments and trust
scoring.

> **Note on the SRS.** `Sathify_SRS.pdf` describes a responsive *web* application
> (Bootstrap/Tailwind, browser compatibility, and so on). That is superseded:
> Sathify is a **native/cross-platform mobile app** built in Flutter, backed by a
> JSON API. Every web-specific instruction in the SRS should be read as
> obsolete. All other SRS content — features, entities, non-functional
> requirements — remains authoritative.

---

## Stack

| Layer | Choice |
| --- | --- |
| Mobile | Flutter (single codebase, Android first) |
| Backend | Django 5.2 LTS + Django REST Framework (JSON API, no templates) |
| Auth | JWT (`djangorestframework-simplejwt`) with RBAC across 4 roles |
| Database | PostgreSQL on Supabase free tier |
| Media | Supabase Storage — Render's disk is ephemeral (see docs) |
| Hosting | Render free tier |
| Notifications | Firebase Cloud Messaging |
| AI | One provider-agnostic layer: Gemini → Groq → OpenRouter → Hugging Face |
| OCR | PaddleOCR primary, EasyOCR automatic fallback |
| Face | DeepFace (`DeepFace.verify()`) |
| Payments | Razorpay, **test mode only** |

Everything above is free with no card on file. The consequences of that
constraint are documented in **[docs/free-tier-constraints.md](docs/free-tier-constraints.md)** —
read it before changing infrastructure.

---

## Repository layout

```
sathify/
├── backend/                 Django REST API
│   ├── config/
│   │   ├── settings/        base.py / dev.py / prod.py
│   │   └── urls.py          module routes mounted under /api/v1/
│   ├── apps/                one Django app per module
│   │   ├── core/            shared base models, pagination, errors, mixins,
│   │   │                    and Module 13's resilience conventions + their
│   │   │                    conformance tests (test_resilience.py)
│   │   ├── accounts/        Module 1  — Identity & Access
│   │   ├── societies/       Module 2  — Society & Resident Onboarding
│   │   ├── workers/         Module 3  — Worker Onboarding & KYC (OCR)
│   │   ├── hiring/          Module 4  — Discovery & Hiring
│   │   ├── bookings/        Module 5  — One-Day Service Booking
│   │   ├── scheduling/      Module 6  — Scheduling & Task Management
│   │   ├── attendance/      Module 7  — Attendance & Gate Verification
│   │   ├── payments/        Module 8  — Payments & Payouts
│   │   ├── ratings/         Module 9  — Ratings & Trust Score
│   │   ├── notifications/   Module 10 — Notifications
│   │   ├── administration/  Module 11 — Admin, Reporting & Complaints
│   │   └── ai_services/     Module 12 — AI Layer (4-tier fallback)
│   ├── requirements/        base / dev / prod / ml
│   ├── conftest.py          shared pytest fixtures (one per role)
│   └── .env.example
├── mobile/                  Flutter application
│   ├── lib/
│   │   ├── core/            config, network, storage, theme, routing
│   │   ├── features/        one folder per module (data/domain/presentation)
│   │   └── shared/          cross-feature widgets and models
│   └── .env.example
└── docs/
```

Each Django app maps 1:1 to a Flutter feature folder and to a module in the
Module & Sub-Module Specification, so work can be divided cleanly across the
team without two people editing the same files.

---

## Getting started — backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
python -m pip install -r requirements/dev.txt

cp .env.example .env              # works unedited: falls back to SQLite

python manage.py migrate
python manage.py createsuperuser  # prompts for a phone number, not a username
python manage.py runserver
```

- API docs: <http://127.0.0.1:8000/api/docs/>
- Django admin: <http://127.0.0.1:8000/admin/>
- Health check: <http://127.0.0.1:8000/health/>

Run the tests:

```bash
python -m pytest              # everything that needs no external service
python -m pytest -m "not ml"  # skip tests needing requirements/ml.txt
```

The heavy CV stack is installed separately, only when working on Module 3 or 7:

```bash
python -m pip install -r requirements/ml.txt   # several GB; see docs first
```

## Getting started — mobile

```bash
cd mobile
flutter create --platforms=android .   # generates android/ without touching lib/
flutter pub get
cp .env.example .env
flutter run
```

The Android emulator reaches your host machine at `10.0.2.2`, not `localhost` —
`.env.example` is already set up for this.

### Push notifications (optional)

The app runs without any Firebase setup. `Firebase.initializeApp()` failing is
handled: push is switched off, and the in-app notification centre — which is the
system of record on both sides — carries everything regardless. Set this up only
when you actually need alerts on a device.

1. Create a free Firebase project and add an Android app with the applicationId
   from `android/app/build.gradle`.
2. Drop `google-services.json` into `mobile/android/app/`. It is gitignored, so
   each developer uses their own project.
3. Add the Google services Gradle plugin, as `firebase_core`'s README describes
   for your Gradle version.
4. On the server, point `FCM_SETTINGS` at the same project's service-account
   JSON and set `FCM_ENABLED=true` (see `backend/.env.example`).

Steps 1–3 are client-side and step 4 is server-side; without step 4 the phone
registers a token that nothing ever pushes to.

---

## Conventions

- **Module ownership.** One Django app + one Flutter feature folder per module.
  Shared code goes in `apps/core/` or `lib/core/`, never into another module.
- **Society scoping.** Any model holding society data inherits
  `SocietyScopedModel`, and its viewset uses `SocietyScopedQuerysetMixin`. This
  is what stops one society reading another's records.
- **Errors.** The API returns one envelope for every failure
  (`{"error": {"code", "message", "details"}}`), so the Flutter client parses a
  single shape.
- **Graceful degradation.** Every AI, OCR and face-verification call ships with
  its manual fallback written at the same time — not added after the first
  outage (SRS 2.5, 5.3). `ai_services.degradation.with_fallback` takes the
  fallback as a *required* argument, so the AI half cannot be written alone.
- **Resilience (Module 13).** Four conventions — client-generated UUIDs on
  anything queueable, idempotent sync endpoints, three tiers of attendance
  evidence, and the AI fallback rule above. They are stated in
  `apps/core/resilience.py` and **checked** by `apps/core/test_resilience.py`;
  conventions that live only in prose decay.
- **No automated tier may refuse a person.** A face check below threshold, a
  GPS fix outside the geofence and an unreadable register photo all produce
  "a human decides", never a denial. The measurements are not good enough to
  cost somebody a day's wages, and the people they misread are the ones with
  the least recourse.
- **Secrets.** Only `.env.example` is committed. Note that the Flutter `.env` is
  bundled into the APK and is therefore public: no secret may ever go in it.
