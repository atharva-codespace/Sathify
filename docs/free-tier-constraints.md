# Free-tier constraints and the decisions they forced

Sathify is an unfunded academic project: every service must be genuinely free —
no card on file, no trial that expires, no "free credits" bought with usage
data. That rule is satisfiable, but it is not free of consequences. This
document records each constraint we hit, the decision it forced, and the
workaround, so nobody re-litigates a settled trade-off three modules later.

> Figures below were accurate on **30 July 2026**. Free tiers change. Re-check
> before relying on any specific number.

---

## 1. Media storage: **Supabase Storage**, not `media/worker_images/`

**Decision: worker profile photos and Aadhaar uploads go to Supabase Storage in
production; the local filesystem is used only in development.**

The deciding factor is not capacity, it is persistence. **Render's filesystem is
ephemeral.** Anything written to `MEDIA_ROOT` is destroyed on every deploy,
every restart, and every wake-from-sleep. Since a free Render service sleeps
after 15 minutes idle, that is not a rare event — it is close to daily. Storing
worker photos on disk means the registered face photo silently disappears, and
gate verification then fails for every worker at once.

Supabase's free tier includes 1 GB of file storage, which is ample: a compressed
profile photo plus an Aadhaar scan is roughly 300–500 KB per worker, so 1 GB
covers on the order of 2,000 workers.

Implementation: `config/settings/prod.py` swaps `STORAGES["default"]` to
`django-storages`' S3 backend pointed at Supabase's S3-compatible endpoint,
with `default_acl = "private"` and signed URLs that expire in 5 minutes. KYC
documents must never be publicly readable. Development keeps
`FileSystemStorage`, so no credentials are needed to run locally.

---

## 2. Render free tier sleeps — and the SRS wants 2-second gate verification

| Property | Free tier value |
| --- | --- |
| Instance hours | 750/month, account-wide |
| Sleep | after 15 minutes with no inbound request |
| Cold start | roughly 50 seconds |
| RAM | 512 MB |

SRS 5.1 requires gate verification within 2 seconds. A cold start is ~25× that.
**These cannot both be true, so the architecture must not depend on the server
being awake at the gate.**

**Workaround, in order of importance:**

1. **The guard app is offline-first (Module 13).** Today's approved bookings are
   cached in SQLite on the guard's device, QR scanning runs entirely on-device
   via ML Kit, and every scan is written locally with a client-generated UUID
   then synced to an idempotent endpoint later. An entry decision never waits on
   the network. This is the real fix; the rest are optimisations.
2. **Keep-alive ping.** A month is 744 hours and the free allowance is 750, so
   *one* always-on service fits within the free tier. A free scheduler
   (cron-job.org) hitting `/health/` every 10 minutes keeps the instance warm.
   This works only while Sathify runs a single free service — a second one would
   blow the 750-hour budget and take both down.
3. **Warn in the UI.** If a request exceeds ~5 s, the Flutter client shows
   "waking up the server" rather than a spinner that looks like a hang.

---

## 3. 512 MB RAM will not hold the CV stack — the one genuinely unsolved item

PaddleOCR (PaddlePaddle) and DeepFace (TensorFlow) each load hundreds of
megabytes of weights. Django plus either one exceeds 512 MB; both together are
far past it. **OCR and face verification cannot run in the Render free web
service.** This is a hard limit, not a tuning problem.

Three genuinely free options, in the order we recommend considering them:

1. **Run the CV work locally for development and demo.** For an academic
   deliverable this is usually sufficient and costs nothing: the marker sees the
   full pipeline working on a laptop. The API contract is identical, so nothing
   about the code changes.
2. **Hugging Face Spaces (free CPU tier)** as a small OCR/face microservice that
   Django calls over HTTP. Genuinely free, no card, and far more RAM than
   Render's free tier. The trade-off is a second cold-start path and a public
   endpoint that must be token-protected.
3. **Swap the heavy models for light ones on the deployed instance only.**
   OpenCV's bundled SFace recogniser is a few megabytes and needs no TensorFlow.
   Accuracy is lower than Facenet/ArcFace, so this is a deployment-profile
   fallback, not a replacement for the DeepFace path the specification asks for.

Both the OCR and face services are written behind a single interface each, so
whichever option is chosen is a configuration change rather than a rewrite.

---

## 4. Supabase free tier pauses after 7 days of inactivity

500 MB database, 1 GB storage, 5 GB egress — all comfortable at pilot scale.
The risk is the **7-day inactivity pause**: a project that goes quiet between
demos gets suspended and needs a manual dashboard click to restore. The
keep-alive ping in §2 also exercises the database, which prevents this.

Use the **session pooler** connection string (port 6543), not the direct
connection (5432). Free Render instances restart often and the pooler tolerates
churn far better.

---

## 5. AI provider ceilings drive the four-tier fallback

| Tier | Provider | Free ceiling | Card? |
| --- | --- | --- | --- |
| 1 | Google Gemini Flash / Flash-Lite | ~1,500 requests/day | No |
| 2 | Groq | generous per-day limits, rate-limited per minute | No |
| 3 | OpenRouter (`:free` models) | **20/min and 50/day** | No |
| 4 | Hugging Face Inference API | credit-limited monthly | No |

Tier 3 is the tightest by an order of magnitude. **We design and test against
the 50/day ceiling**, and the AI layer enforces it locally so the tier fails
over cleanly to Tier 4 instead of returning provider errors.

Two ongoing maintenance notes:

- **OpenRouter's free catalogue rotates.** A model that is free today may be
  withdrawn or made paid. `OPENROUTER_MODEL` is therefore read from `.env` and
  will need updating periodically — unlike Tiers 1 and 2, it is not
  set-and-forget.
- **xAI's Grok is deliberately excluded.** As of mid-2026 xAI publishes no
  durable no-strings free API tier; its free credits require opting into a
  data-sharing programme, which means paying with usage data. That fails the
  zero-budget rule as we have defined it. If a confirmed genuinely-free key
  turns up later, it appends as a fifth tier with no other code change.

---

## 6. Razorpay stays in test mode

Test mode is free and unlimited, and issues no real charges. Live mode requires
business verification (PAN, bank account) and takes 2% + GST per transaction.
`RAZORPAY_TEST_MODE` defaults to `True` and the payments module refuses to
initiate a live transaction while it is set.

---

## 7. There is no Celery, and no scheduler

Render's free plan runs **one web service**. There is no worker dyno, and there
is no Redis or RabbitMQ on any free tier we are using. That removes two things
the module specification assumes:

- **Celery**, which Module 12.3 specifies for the Aadhaar OCR pipeline.
- **Cron**, which several modules assume for deadlines and reminders.

Adding Celery anyway would be worse than not having it: tasks would be accepted
into a queue nothing drains, the client would poll a "processing" state forever,
and no error would ever explain why. So OCR runs inline, bounded by a timeout,
and returns a result whose `needs_manual_entry` flag is the fallback the spec
asks for.

Everything that would have been a scheduled job is instead an **idempotent,
bounded sweep with three triggers** — a read that naturally passes it, an
endpoint the external uptime pinger (§2) can call, and a management command:

| What | Sweep |
| --- | --- |
| 4.4 | `HireRequest.objects.expire_lapsed()` |
| 5.2 | `Booking.objects.expire_stale()` |
| 5.5 | `emergency.expire_unclaimed()` — closes a broadcast nobody claimed, **and refunds its surcharge** |
| 6.4 | `due_reminders()` → Module 10's `deliver_due_reminders()` |
| 9.3 | `recompute_trust_scores` command |
| 11.3 | `escalate_overdue()` |
| 12 | `AiUsageCounter.prune()` |

Every one is safe to run twice. That is the property that makes "whoever happens
to load the screen" an acceptable trigger.

---

## 8. No Channels, no Redis — so "real time" is a bounded poll

Module 5.5 broadcasts one emergency request to several workers at once, and the
instant one of them accepts, the card has to disappear from everybody else's
screen. That is a socket-shaped requirement, and §7's constraint rules a socket
out: Django Channels needs ASGI plus a channel layer, the only free channel
layer worth having is Redis, and there is no second service to run either in.

**Decision: a small dedicated endpoint (`/bookings/emergency/live/`), polled
only while a request is actually in flight.**

The cost is controlled by *when* it runs rather than by how often:

| State | Interval |
| --- | --- |
| A worker has an open offer, or a resident has an unclaimed request | 5 s |
| Signed in, nothing in flight | 30 s |
| App backgrounded | not at all |

An emergency lives for at most ten minutes (`emergency.OFFER_WINDOW`), so the
5-second rate applies to a few minutes a day at most, and the endpoint itself
returns one indexed lookup's worth of rows plus a `version` stamp the client
uses to skip rebuilds. The 30-second idle rate is what notices a *new* request
on a phone whose push never arrived — which, in any build without
`google-services.json`, is every phone.

If Channels ever becomes affordable, the client contract does not change: the
same payload can be pushed instead of pulled, and `EmergencyLiveRefresher` is
the only thing that has to know the difference.

---

## Summary

| Constraint | Status |
| --- | --- |
| Ephemeral disk on Render | **Solved** — Supabase Storage |
| Cold starts vs 2 s gate SLA | **Solved** — offline-first guard app + keep-alive |
| Supabase 7-day pause | **Solved** — same keep-alive ping |
| OpenRouter 50/day | **Solved** — locally enforced cap, fails over to Tier 4 |
| No Celery, no cron | **Solved** — inline OCR + idempotent sweeps, see §7 |
| 512 MB RAM vs PaddleOCR/DeepFace | **Open decision** — see §3; needs your call |
