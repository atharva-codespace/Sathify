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
│   ├── requirements/        base / dev / prod / cv / ml
│   ├── conftest.py          shared pytest fixtures (one per role)
│   └── .env.example
├── mobile/                  Flutter application
│   ├── lib/
│   │   ├── core/            config, network, storage, theme, routing
│   │   ├── features/        one folder per module (data/domain/presentation)
│   │   └── shared/          cross-feature widgets and models
│   ├── android/             committed and hand-configured — do NOT regenerate
│   └── .env.example
├── scripts/setup.py         one-command setup for a fresh clone
├── .github/                 CI, PR/issue templates, CODEOWNERS
├── CONTRIBUTING.md          how four people share this repo
└── docs/
```

Each Django app maps 1:1 to a Flutter feature folder and to a module in the
Module & Sub-Module Specification, so work can be divided cleanly across the
team without two people editing the same files. Who owns which module — and the
short list of shared files that belong to nobody — is in
**[CONTRIBUTING.md](CONTRIBUTING.md)**. Read it before your first pull request.

---

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.13 (3.10+ works) | matches `PYTHON_VERSION` in `render.yaml` |
| Flutter | 3.27 or newer | `pubspec.yaml` will refuse to resolve below this |
| JDK | **21** | see the warning below — this one catches people out |
| Android SDK | via Android Studio or `cmdline-tools` | plus one emulator or a device |

> **JDK 21, not 24 or 25.** `mobile/android/` is committed and pinned to AGP
> 9.0.1 / Kotlin 2.3.20 / Gradle 9.1, none of which support the newer JDKs. If
> your machine has several installed, point Flutter at the right one explicitly:
>
> ```bash
> flutter config --jdk-dir "<path-to-jdk-21>"
> flutter doctor -v          # confirm the Java version it reports
> ```
>
> A wrong JDK shows up as an opaque Gradle failure during `flutter run`, not as
> anything mentioning Java, so check this first when an Android build breaks.

Only Python is needed for backend work. Flutter, the JDK and the Android SDK
are needed only to run the app.

---

## Quick start

```bash
git clone <this-repo> && cd sathify
python scripts/setup.py
```

That creates the virtualenv, installs backend dependencies, writes both `.env`
files from their templates, migrates the database, and seeds a complete demo
society with one working login per role. It is idempotent — re-run it any time,
it never overwrites an existing `.env`.

Then, in two terminals:

```bash
# terminal 1 — the API
cd backend
.venv/Scripts/activate       # Windows;  source .venv/bin/activate on macOS/Linux
python manage.py runserver

# terminal 2 — the app (emulator or device already running)
cd mobile
flutter run
```

Demo logins, all with password `Sathify@123`:

| Role | Phone |
| --- | --- |
| Society admin | `9800000001` |
| Resident | `9800000002` |
| Worker | `9800000003` |
| Guard | `9800000004` |

The step-by-step equivalents are below, for when something goes wrong or you
only want one half of the stack.

### Everyone is on one shared database

The API is deployed at **<https://sathify-api.onrender.com/api/v1>**, backed by
a shared Supabase Postgres database, and `mobile/.env.example` already points at
it. So for app work you do **not** need terminal 1 above at all:

```bash
git clone <this-repo> && cd sathify/mobile
cp .env.example .env
flutter run
```

Every teammate and every phone then sees the same accounts and the same data,
on any network, with nobody's laptop running and **no database credentials**.
Log in with any demo account from the table above.

You still want a local backend when you are changing backend code. Run it as in
terminal 1 and point the app at it for that run only, without editing any file:

```bash
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
```

Running the Django server locally against the *shared* database additionally
needs `DATABASE_URL` in `backend/.env` — that value is a secret, is gitignored,
and comes from a teammate privately, never from the repo. Setup, credential
handling and free-tier caveats: **[docs/cloud-database.md](docs/cloud-database.md)**.

---

## Getting started — backend (step by step)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
python -m pip install -r requirements/dev.txt

cp .env.example .env              # works unedited: falls back to SQLite

python manage.py migrate
python manage.py seed_demo        # a demo society + one login per role
python manage.py runserver
```

- API docs: <http://127.0.0.1:8000/api/docs/>
- Django admin: <http://127.0.0.1:8000/admin/>
- Health check: <http://127.0.0.1:8000/health/>

> On Windows PowerShell the activate line is `.venv\Scripts\Activate.ps1`, and
> it may be blocked by the execution policy. Either
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for that session,
> or skip activation entirely and call `.venv\Scripts\python.exe manage.py ...`.

**Use `seed_demo`, not `createsuperuser`.** A superuser created the usual way
has no society, and every society-scoped endpoint filters on exactly that — so
it logs in to an app that looks broken rather than empty. `seed_demo` builds a
real society with towers, flats, gates, service types and approved workers, and
adopts any existing superuser into it. It is idempotent; `--reset` rebuilds the
demo accounts from scratch. You can still create a superuser for the Django
admin (it prompts for a phone number, not a username) — just run `seed_demo`
afterwards.

Run the tests:

```bash
python -m pytest -m "not ml"  # the usual command
python -m pytest              # includes tests needing requirements/ml.txt
```

The heavy CV stack is installed separately, only when working on Module 3 or 7:

```bash
python -m pip install -r requirements/cv.txt   # ~60 MB: OpenCV + NumPy only
python -m pip install -r requirements/ml.txt   # several GB; see docs first
```

Everything runs without either. OCR and face verification report themselves
unavailable and fall back to manual entry, which is a supported state — see the
graceful-degradation rule under Conventions.

## Getting started — mobile (step by step)

```bash
cd mobile
cp .env.example .env    # MUST come first — see below
flutter pub get
flutter run
```

> **Do not run `flutter create` here.** `android/` is committed and
> hand-configured (Firebase plugin, core library desugaring, Gradle memory
> limits, the `com.sathify.app` application id). Regenerating it adds a
> duplicate `MainActivity` under the wrong package, a stray `mobile/README.md`,
> and a default `test/widget_test.dart` that fails immediately — and dirties
> `.metadata` and `pubspec.lock` for everyone.

`cp .env.example .env` really does have to come first: `pubspec.yaml` bundles
`.env` as a Flutter asset, and Flutter refuses to build at all when a declared
asset is missing (`No file or variants found for asset: .env`). The file is
git-ignored, so it does not arrive with the clone.

The Android emulator reaches your host machine at `10.0.2.2`, not `localhost` —
`.env.example` is already set up for this, and `AppConfig` derives the same
default per platform if you leave `API_BASE_URL` blank.

### Running on a physical phone

An emulator shares your laptop's network stack; a real phone does not, so three
things have to change together. Missing any one of them shows up in the app as
"No internet connection" even though the phone is plainly online.

1. **Bind the server to your network, not just to loopback.** `runserver`
   listens on `127.0.0.1` by default, which no other device can reach:

   ```bash
   python manage.py runserver 0.0.0.0:8000
   ```

   `ALLOWED_HOSTS` is already `["*"]` in `config/settings/dev.py`, so nothing
   else changes on the server.

2. **Find your laptop's LAN IP**, and put the phone on the same Wi-Fi:

   ```bash
   ipconfig                      # Windows — "IPv4 Address" of your Wi-Fi adapter
   ip addr show                  # Linux
   ipconfig getifaddr en0        # macOS
   ```

   It will look like `192.168.x.x` or `10.x.x.x`. Not `127.0.0.1`.

3. **Point the app at it** — for one run, without touching your `.env`:

   ```bash
   flutter run --dart-define=API_BASE_URL=http://192.168.1.42:8000/api/v1
   ```

   A `--dart-define` beats `.env`, which beats the built-in default. If this is
   how you always work, set `API_BASE_URL` in `mobile/.env` instead.

If it still cannot connect, it is almost always the **firewall** on the laptop
blocking inbound port 8000 — on Windows, allow Python through on Private
networks when prompted, or add the rule by hand. Confirm the path independently
by opening `http://<laptop-ip>:8000/health/` in the *phone's own browser*: if
that fails, the problem is the network or the firewall, not the app.

Plain `http://` over the LAN works because `android/app/src/debug/AndroidManifest.xml`
enables cleartext traffic for **debug builds only** — release builds talk to
Render over HTTPS and keep it blocked.

### If the Android build is slow (Windows)

`optimize-windows-build.ps1` at the repository root adds your build folders to
Windows Defender's exclusion list and enables long path support — on Windows,
antivirus scanning of Gradle's file churn is routinely the difference between a
3-minute and a 30-minute build. Run it once, as Administrator; it discovers your
own tool paths rather than assuming anybody else's. It is entirely optional.

### Push notifications (optional)

The app runs without any Firebase setup. `Firebase.initializeApp()` failing is
handled: push is switched off, and the in-app notification centre — which is the
system of record on both sides — carries everything regardless. Set this up only
when you actually need alerts on a device.

1. Create a free Firebase project and add an Android app with the applicationId
   from `android/app/build.gradle.kts` (`com.sathify.app`).
2. Drop `google-services.json` into `mobile/android/app/`. It is gitignored, so
   each developer uses their own project.
3. Nothing to add on the Gradle side — the Google services plugin is already
   declared in `android/settings.gradle.kts`, and `android/app/build.gradle.kts`
   applies it automatically as soon as the file from step 2 is present. Do not
   move that apply back into the `plugins {}` block: applied unconditionally, it
   fails the build for everyone who has not done step 2.
4. On the server, point `FCM_SETTINGS` at the same project's service-account
   JSON and set `FCM_ENABLED=true` (see `backend/.env.example`).

Steps 1–3 are client-side and step 4 is server-side; without step 4 the phone
registers a token that nothing ever pushes to.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `No file or variants found for asset: .env` | `mobile/.env` does not exist. `cd mobile && cp .env.example .env`. It is git-ignored, so a fresh clone never has one. |
| `ModuleNotFoundError: No module named 'numpy'` | You are on an old checkout. The OCR stages that need OpenCV are now imported lazily; pull `main`. Nothing outside Modules 3 and 7 requires `requirements/ml.txt`. |
| The app loads nothing, every request times out | `API_BASE_URL` is pointing somewhere the device cannot reach. Emulator → `10.0.2.2`; physical device → your laptop's LAN IP; and the backend must be running. |
| "No internet connection" on a physical phone that is online | The server is bound to loopback. Use `runserver 0.0.0.0:8000`, and check the laptop firewall — see "Running on a physical phone". |
| Gradle fails with a Java/AGP version error | Wrong JDK. This project needs **21** — see Prerequisites. `flutter doctor -v` reports the one Flutter is using. |
| `:app:processDebugGoogleServices` fails with `File google-services.json is missing` | You are on an older checkout. Firebase is optional, and `android/app/build.gradle.kts` now applies the Google services plugin only when that file exists — pull `main`. Not having the file is the expected state: push is simply off and the in-app notification centre carries everything. Add it only if you want push, per "Push notifications (optional)". |
| `flutter test` fails in `test/widget_test.dart` | Someone ran `flutter create` in `mobile/`. Delete `mobile/test/widget_test.dart`, `mobile/README.md`, and `mobile/android/app/src/main/kotlin/com/sathify/sathify/`. |
| First request after a break takes ~50 s | Render's free instance was asleep. Expected — see [docs/free-tier-constraints.md](docs/free-tier-constraints.md) §2. |
| Android builds take tens of minutes on Windows | Antivirus scanning Gradle's file churn. Run `optimize-windows-build.ps1` as Administrator. |
| Superuser logs in and sees an empty app | It has no society. Run `python manage.py seed_demo`, which adopts existing superusers into the demo society. |
| A login that works on one device is "incorrect" on another | The two devices are talking to different databases. Each developer's default SQLite file is separate, so accounts do not carry across. Put everyone on one database — see [docs/cloud-database.md](docs/cloud-database.md). Also check the number is typed bare (`9800000003`), since `+91…` is stored as a *different* account. |
| `connection to server ... failed` / `could not translate host name ...pooler.supabase.com` | Supabase pauses a free project after 7 days idle. Open the Supabase dashboard and hit **Restore**. If it persists, confirm `DATABASE_URL` uses the pooler port `6543`. |

---

## Working together

Four people, twelve modules. **[CONTRIBUTING.md](CONTRIBUTING.md)** is the short
version: who owns what, branch and PR conventions, migration etiquette, and the
ten-minute branch-protection setup that keeps `main` green.

CI runs on every pull request ([.github/workflows/ci.yml](.github/workflows/ci.yml)):
Django system checks, a check that migrations match the models, `pytest -m "not ml"`,
then `flutter analyze` and `flutter test`. Both jobs start by copying
`.env.example` to `.env`, so the "a fresh clone runs with an unedited `.env`"
promise on this page is verified continuously rather than trusted.

You can run the same checks locally in well under a minute:

```bash
cd backend && python manage.py makemigrations --check --dry-run && python -m pytest -m "not ml"
cd mobile  && flutter analyze && flutter test
```

---

## Conventions

- **Module ownership.** One Django app + one Flutter feature folder per module.
  Shared code goes in `apps/core/` or `lib/core/`, never into another module.
  The owner table and the list of shared files are in
  [CONTRIBUTING.md](CONTRIBUTING.md).
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
- **Secrets.** Only `.env.example` is committed. Every new setting goes into it
  with a comment and a working default, so a fresh clone still runs — a setting
  that exists only in your own `.env` breaks the other three. Note that the
  Flutter `.env` is bundled into the APK and is therefore public: no secret may
  ever go in it.
