# Shared cloud database (Supabase + Render)

Moving off local SQLite so that every teammate and every phone reads and writes
**one** database. Until you do this, each developer has their own
`backend/db.sqlite3`, so an account registered on one laptop simply does not
exist on anyone else's — which shows up in the app as
"incorrect phone number or password".

## Why Supabase

This repository was built for it. `render.yaml`, `config/settings/base.py:113`
and the media-storage block in `config/settings/prod.py` already reference
Supabase by name, and the three packages needed to talk to it are already
pinned in `requirements/base.txt`:

```
django-environ==0.12.0    # 12-factor .env loading
dj-database-url==3.0.1    # parses DATABASE_URL into Django config
psycopg[binary]==3.2.9    # PostgreSQL driver
```

So there is **no code to write and no dependency to add** — the whole switch is
one environment variable. Neon or Railway would work as Postgres too, but you
would give up the Supabase Storage bucket that `prod.py` already uses for worker
photos and Aadhaar uploads, and you would be rewiring config that currently
works.

Free tier: 500 MB database, 1 GB file storage, no credit card. Ample for this
project. The one catch is listed under [Free-tier gotchas](#free-tier-gotchas).

## What the setup looks like afterwards

```
teammate's phone ─┐
teammate's phone ─┼──> https://sathify-api.onrender.com ──> Supabase Postgres
teammate's phone ─┘         (one deployed API)               (one database)

backend developers ────> localhost:8000 ─────────────────────────┘
                         (own server, same cloud database)
```

**This is live.** The API is deployed at
<https://sathify-api.onrender.com/api/v1> and `mobile/.env.example` already
points at it, so a fresh clone needs no configuration at all.

Two payoffs worth being explicit about:

- **App developers need no database credentials at all.** They clone, copy
  `mobile/.env.example`, and run. The API URL is baked into the template.
- **No laptop has to be running** for someone to test on a phone, and phones no
  longer need to be on the same Wi-Fi as anybody.

---

## Part 1 — Create the Supabase project

Done **once, by one person**. Everyone else skips to [Part 5](#part-5--what-every-teammate-does).

1. Sign up at <https://supabase.com> (GitHub login is fine) → **New project**.
2. Fill in:
   - **Name**: `sathify`
   - **Database Password**: generate a strong one and **save it** — it appears
     inside the connection string and Supabase will not show it again.
   - **Region**: `South Asia (Mumbai)` — closest to you, and it matches the
     `ap-south-1` default already in `SUPABASE_STORAGE_REGION`.
3. Wait ~2 minutes for provisioning.
4. Go to **Project Settings → Database → Connection string → URI**, and switch
   the tab to **Session pooler**.

   You want the **pooler** (port `6543`), not the direct connection (`5432`).
   Render's free instances restart often and the pooler tolerates that far
   better; direct connections exhaust Supabase's low free-tier connection
   ceiling quickly.

5. Copy the URI. It looks like:

   ```
   postgresql://postgres.abcdefghijklmnop:YOUR-PASSWORD@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
   ```

   Replace `[YOUR-PASSWORD]` with the password from step 2 if Supabase shows it
   as a placeholder. Paste the URI **exactly as given** — both `postgresql://`
   and `postgres://` parse correctly here, so there is no scheme to rewrite.

## Part 2 — Point the backend at it and load the data

Still on the machine of whoever did Part 1.

1. Open `backend/.env` and set the line that is currently empty:

   ```ini
   DATABASE_URL=postgresql://postgres.abcdefghijklmnop:YOUR-PASSWORD@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
   ```

   `backend/.env` is gitignored (`.gitignore:6`), so this never reaches GitHub.
   That is deliberate — see [Sharing the credential](#sharing-the-credential-safely).

2. Confirm Django is now resolving to Postgres rather than SQLite:

   ```bash
   cd backend
   .venv/Scripts/python.exe -c "import django,os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev'); django.setup(); from django.conf import settings; d=settings.DATABASES['default']; print(d['ENGINE']); print(d['HOST'])"
   ```

   Expect `django.db.backends.postgresql` and a `...pooler.supabase.com` host.
   If it still says `sqlite3`, the `.env` line did not take — check for a stray
   `#` or quotes around the value.

3. Create the schema and the demo accounts **in the cloud database**:

   ```bash
   python manage.py migrate
   python manage.py seed_demo
   ```

   `seed_demo` is idempotent and wrapped in a transaction, so it is safe to
   re-run. Note that it resets the six demo passwords to `Sathify@123` every
   time by design — a forgotten demo password is one re-run away. It does not
   touch accounts you registered yourself through the app.

4. Sanity-check by running the server and logging in as `9800000001`:

   ```bash
   python manage.py runserver
   ```

### About your existing local data

The 8 accounts in `backend/db.sqlite3` are almost all `seed_demo` output, which
step 3 recreates in the cloud. Anything you registered by hand does not carry
over. If you want it, export before switching and import after:

```bash
# BEFORE editing .env — reads SQLite
python manage.py dumpdata --natural-foreign --natural-primary \
  -e contenttypes -e auth.Permission -e admin.logentry -e sessions \
  --indent 2 -o ../backup-sqlite.json

# AFTER editing .env and running migrate — writes Supabase
python manage.py loaddata ../backup-sqlite.json
```

Otherwise just re-register those accounts. `db.sqlite3` is gitignored and is
simply ignored once `DATABASE_URL` is set — you can delete it once you have
confirmed the cloud database works, or leave it as an offline fallback.

## Part 3 — Deploy the API to Render

`render.yaml` is already written for this; you are filling in its blanks.

1. Push your branch to GitHub first — Render deploys from the repo.
2. At <https://render.com>, sign up and choose
   **New → Blueprint**, then connect this GitHub repository. Render reads
   `render.yaml` and proposes the `sathify-api` service.
3. It will prompt for every variable marked `sync: false`. At minimum set:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the same Session Pooler URI from Part 1 |

   The rest (`SUPABASE_STORAGE_*`, `GEMINI_API_KEY`, `RAZORPAY_*`) can stay
   empty for now — the app degrades gracefully without them. Fill in the
   storage keys when you need uploaded photos to survive a redeploy, since
   Render's disk is wiped on every restart.

4. Deploy. The build runs `collectstatic` and `migrate` automatically
   (`render.yaml:20-23`), so the schema is applied for you.

   `seed_demo` is deliberately **not** in the build command — otherwise every
   deploy would reset the demo passwords. You already seeded the same database
   in Part 2, so the demo accounts are there.

5. Note your URL — something like `https://sathify-api.onrender.com`. Check
   `https://<your-url>/health/` in a browser before going further.

`ALLOWED_HOSTS` needs no attention: `config/settings/prod.py:22-25` appends
Render's own hostname at runtime.

## Part 4 — Point the app at the deployed API

**Already done** — `mobile/.env.example` ships with:

```ini
API_BASE_URL=https://sathify-api.onrender.com/api/v1
```

This one *is* committed, deliberately. It is not a secret: `mobile/.env` is
bundled as a Flutter asset, so its contents ship readable inside the APK
regardless. Having it in the template is exactly what makes teammate onboarding
zero-config.

Anyone who wants to develop against their own laptop overrides it per-run
without editing any file:

```bash
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
```

Note the value is read at **build** time. Anyone whose `mobile/.env` predates
this change still has an old local address in it and must re-copy the template
(`cd mobile && cp .env.example .env`), then rebuild — `scripts/setup.py` never
overwrites an existing `.env`.

## Part 5 — What every teammate does

This is the whole onboarding after the above is done:

```bash
git clone <this-repo> && cd sathify
python scripts/setup.py
cd mobile && flutter run
```

They log in as `9800000003` / `Sathify@123` and see the same data as everyone
else. **No database password, no Supabase account, no Render account.**

Only someone who needs to run the Django server locally — to work on backend
code — additionally pastes `DATABASE_URL` into their own `backend/.env`.

> Remind teammates that `mobile/.env` is created from the template **once**.
> Someone who cloned before Part 4 will still have an old `.env` pointing at
> `10.0.2.2`; `scripts/setup.py` never overwrites an existing one. They should
> re-copy it: `cd mobile && cp .env.example .env`.

## Sharing the credential safely

`DATABASE_URL` contains your database password, which grants full read/write on
every table. **Do not commit it** — not to `.env`, not to `render.yaml`, not in
a code comment. A credential pushed to GitHub is compromised even if you delete
it in the next commit, because it stays in the history and GitHub's public
event feed is actively scraped.

The repository is already structured to prevent this: `.env` is gitignored, and
every secret in `render.yaml` is marked `sync: false` so it lives only in the
Render dashboard.

Send it to teammates through something private — a password manager shared
vault, or a direct message — not the group chat you also use for screenshots,
and not the repo. If it does leak, rotate it in
**Supabase → Project Settings → Database → Reset database password**, then
update Render and each teammate's `.env`.

## Free-tier gotchas

- **Supabase pauses a free project after 7 days with no queries.** It does not
  delete anything, but the first request afterwards fails and someone must hit
  **Restore** in the dashboard. If your team goes quiet for a week, expect this.
- **Render free instances sleep after 15 minutes idle**, and the first request
  after that takes ~50 seconds. `API_TIMEOUT_SECONDS=60` in `mobile/.env`
  already accounts for it. See [free-tier-constraints.md](free-tier-constraints.md).
- **750 Render instance-hours per month, account-wide.** Keep this to one
  service, as `render.yaml` warns.
- **Supabase's connection ceiling is low.** Always use the pooler URI (port
  `6543`). `DB_CONN_MAX_AGE=60` in `.env` already reuses connections.

## Going back to SQLite

Blank the line out:

```ini
DATABASE_URL=
```

`config/settings/base.py:124` treats empty and absent identically and falls
straight back to `sqlite:///backend/db.sqlite3`. Useful on a plane, or when
Supabase is paused and you just want to run the tests. The test suite always
uses its own throwaway database, so it is unaffected either way.
