# Software Requirements Specification

## Sathify — Smart Society Domestic Workforce Management System

*A mobile-first platform for the verified hiring, scheduling, attendance, payment, and trust management of domestic workers within Indian residential societies.*

---

## 1. Introduction

### 1.1 Purpose of the Document

This Software Requirements Specification (SRS) defines the functional behaviour, business rationale, technical architecture, and quality requirements of Sathify. It serves four audiences at once: the engineering team building and maintaining the platform, evaluators assessing its completeness and rigor, stakeholders assessing its commercial viability, and society administrators or pilot residents validating that it reflects real-world needs.

The document is deliberately unified rather than split across a technical spec and a separate business plan, because Sathify's engineering choices — offline-first attendance, a free-tier-only infrastructure stack, a four-tier AI fallback chain, an explainable trust score — are inseparable from its commercial reality as a zero-budget platform serving a two-sided market where one side is economically vulnerable.

Every requirement below reflects the system as it stands today: a working, tested, deployed application, not a proposal. Capabilities not yet built are marked as future scope in Section 6.

### 1.2 Project Scope

Sathify is a cross-platform mobile application, built in Flutter, backed by a Django REST Framework JSON API, that digitizes the management of domestic workers (maids, cooks, cleaners, and similar household help) within gated residential societies in India.

It replaces an informal, word-of-mouth system — paper attendance registers, unverified hiring, cash-only payment, no dispute mechanism, no portable work history — with one application used by four classes of user:

- **Residents** — discover, hire, schedule, pay, and rate domestic workers, either as a standing engagement or a one-day booking.
- **Domestic workers** — register with government-ID-backed verification, manage availability, mark attendance, take urgent leave, track earnings, and rate residents in turn.
- **Security guards** — verify identity and bookings at the gate via QR code and optional face verification, and log every entry and exit.
- **Society administrators** — approve residents and workers, manage the worker directory, oversee complaints against a service-level clock, and review reporting for their society.

The platform spans thirteen functional modules — identity and access, society/resident onboarding, worker KYC, discovery and hiring, one-day bookings, scheduling, attendance and gate verification, payments, ratings and trust, notifications, admin/reporting/complaints, an AI layer, and a cross-cutting set of offline-resilience conventions.

Sathify is a **standalone, dedicated domestic-workforce module** that a society can adopt independently of any society-management software it already runs; it does not replace such platforms, but solves one problem — verified, accountable, digitally-recorded management of domestic help — in depth.

This document covers the mobile application (Android first, structured to extend to iOS), the backend API and its modules, supporting third-party integrations (payments, push, generative AI, OCR, face verification), the current infrastructure and deployment model, and the commercial strategy by which the platform sustains itself.

### 1.3 Business Ideology & Core Vision

Sathify is built on one asymmetry that shapes every decision in this document: **the platform connects an affluent user (the resident) to an economically vulnerable one (the domestic worker), and a rupee taken from the worker's side costs far more than the same rupee earned from the resident's side.** It is a meaningful share of the worker's income, often invisible to the resident paying it, and trivially avoidable — the cash-based informal market it displaces charges no commission and is one phone call away.

This produces three commitments:

- **Trust is the product, not a bolted-on feature.** A resident is letting a stranger into their home; a worker is trusting a household to pay fairly and treat them decently. Verification (KYC, photo identity, an auditable gate log) and accountability (two-way ratings, an explainable trust score, a complaint process with a real deadline) come first.
- **No automated system may cost a person their livelihood on an imperfect measurement.** Face-recognition, GPS, and OCR accuracy all degrade for exactly the population this platform serves. Every module follows the same rule: a failed automated check asks for a human decision, never issues an automatic denial. Dignity is symmetrical too — workers rate residents just as residents rate workers, either side may raise a complaint, and urgent leave is approved instantly and without justification, because a worker forced to negotiate permission will simply not show up.
- **The commercial model must not tax the vulnerable side to subsidise the comfortable side.** Revenue (Section 2) comes from societies and residents — the side with disposable income — while the platform stays free for workers, tips are guaranteed to arrive in full, and recurring wages are never commissioned.

In short: free to a society until its committee is convinced it should pay for what it already depends on, and free to a worker, permanently, by design.

---

## 2. Business & Financial Strategy

### 2.1 Business Model

Sathify is a **two-sided, society-anchored marketplace**, sold primarily as B2B software to a society's managing committee, with a free, full-featured experience for individual residents and workers.

**The unit of sale is the society, not the household.** A resident in a society with no active subscription still gets the complete product free — discovery, hiring, bookings, attendance, payments, ratings. Individual residents are the acquisition channel: once enough of a society is actively using the app that its gate log, complaint history, and worker directory are genuinely relied on, the pitch to formalise a paid subscription writes itself.

**The model is built to resist disintermediation, not fight it.** Recurring domestic-work arrangements disintermediate fast — within a month the resident and worker have exchanged numbers and reverted to cash, owing the platform nothing. A percentage-of-salary fee is therefore structurally unsound. Sathify takes **zero** revenue from recurring salary payments and instead monetises what does not disintermediate:

| What is charged | Why it survives disintermediation |
|---|---|
| A subscription paid by the **society** | The gate log, attendance history, and complaint queue are the society's own record; leaving means losing it. |
| A convenience fee on **one-day bookings** | A single transaction with no ongoing relationship to route around. |
| One-off **verification services** | A background check is a discrete product, not a recurring toll. |
| The **digital payment rail** | Valuable only while easier than cash — an incentive to keep it genuinely good. |
| **Advertising**, shown only to residents | Independent of whether any transaction happens at all. |

This is implemented, not just priced: `SocietySubscription` has a `FREE` tier treated as a permanent, fully-functional state; `Payment.platform_fee_paise` exists in the schema today with the fee itself switched off; and every wage transfer, tip, replacement payment, or refund is hard-coded exempt from any platform fee.

### 2.2 Expected Revenue Sources

Revenue is layered and sequenced by durability, not by size at launch.

**1. Society subscriptions — the primary line**, sold to the managing committee across three tiers:

| Tier | Price | What it unlocks |
|---|---|---|
| **Free** | ₹0, indefinitely | Up to 25 workers, gate log, complaints, 30-day history. Deliberately permanent, so a society can run on it forever. |
| **Standard** | ₹1,500/mo or ₹15,000/yr | Unlimited workers, 12-month history, monthly PDF/CSV reports, SLA escalation, 3 admin accounts. |
| **Plus** | ₹4,000/mo | Everything in Standard, plus 5 verification credits/month, multi-gate support, ERP API access, priority replacement matching, and no booking fee. |

A 200-flat society already spends ₹25,000–₹40,000/month on security staffing; ₹1,500 is a rounding error against that, sold as a gate log that cannot be lost or argued with. **A lapsed subscription never degrades the operating product** — gate verification, attendance, payments, and complaints keep working identically; only extended reporting narrows. A billing dispute must never stand between a worker and their wages.

**2. A one-day booking convenience fee** — **8% of booking value, paid by the resident on top of the worker's rate, capped at ₹40, disclosed up front**, never deducted from what the worker was promised. Recurring salaries carry no fee.

**3. Verification and certification badges** (police verification ≈ ₹250; skills certification ≈ ₹150/module) — earned, dated, and expiring, and deliberately excluded from search ranking, so money can never buy a position in the trust score residents rely on.

**4. A bounded featured-placement product** (≈ ₹99/month), capped at one in five results, labelled "Promoted," and only ever re-ordering a worker within their own trust band — never above a lower-rated worker.

**5. Advertising**, shown only to residents, modelled as a small, late-stage line (well under ₹500/month at pilot scale).

**6. Tipping — revenue-neutral by design.** ₹50/₹100/₹200 presets shown only after a five-star rating; **100% of every tip reaches the worker**, with any gateway fee absorbed by the platform as a trust expense rather than deducted.

Build order: **subscription → booking fee → verification → advertising last**, in order of durability.

### 2.3 Financial Requirements

**Phase 1 — today.** The platform runs at **zero recurring cash cost**: Render's free web-service tier, Supabase's free database and storage tier, Firebase's free push tier, Razorpay test mode, and a four-provider free-tier AI chain (Gemini, Groq, OpenRouter, Hugging Face). It is built by a four-person team contributing engineering time rather than drawing salary, with no domain, app-store, or infrastructure spend incurred to date.

**Phase 2 — scaling past the free tier**, costed against named ceilings rather than a calendar date:

| Cost line | Trigger | Indicative cost |
|---|---|---|
| Render paid web service | 512 MB RAM or 750 hrs/month ceiling, or cold-start latency | From **US $7/mo** + **$25/mo** workspace fee ([Render](https://render.com/docs/new-workspace-plans)) |
| Supabase Pro | 500 MB DB / 1 GB storage ceiling | **US $25/mo**, scaling to **$25–500/mo** at 10K–100K users ([Supabase](https://www.jetadmin.io/blog/supabase-pricing-2026-guide-to-plans-limits-and-real-world-costs/)) |
| OCR/face microservice | Cannot fit CV stack in 512 MB alongside the API | **US $0–15/mo** |
| Razorpay live mode | Moving off test mode | **≈ 2% + GST per transaction** |
| SMS fallback gateway | Push alone judged insufficient | Per-message, low paise to a few rupees |
| Google Play registration | Before public Android launch | **US $25, one-time** |
| Apple Developer Program | If an iOS release ships | **US $99/yr** |
| Production domain + TLS | Before a public launch | **₹800–1,500/yr**; TLS is free |

None of this is committed spend today; figures reflect published rates at time of writing and should be re-verified before budgeting decisions. Beyond infrastructure, the main future cost driver is headcount: continued module ownership, part-time society sales/onboarding, and lightweight customer support.

### 2.4 Future Financial Scope & Projections

The model is validated against a **single society**, because the argument that matters is "this stops losing money at the first sale," not "this can eventually be large." Twelve months into one 200-flat society on Standard:

| Revenue line | Monthly (indicative) |
|---|---|
| Society subscription | ₹1,500 |
| ~60 bookings × ~₹28 avg. fee | ₹1,680 |
| ~4 verification badges | ₹1,000 |
| Advertising (~180 residents) | ₹300 |
| **Total per society** | **≈ ₹4,480** |
| *(Tips processed)* | *₹12,000 → ₹0 platform revenue, by design* |

Against an indicative **₹600/month** infrastructure cost at that scale, **one paying society already covers running costs with margin** — proof of sound unit economics rather than a scale projection. Longer-term scope multiplies this across more societies and layers in the currently-unbuilt lines in Section 6 (insurance, portable salary history, expanded verification), all following the same principle: monetise what does not disintermediate.

### 2.5 Business Expectations & KPIs

**Adoption** — societies verified and active; % of a society's flats with an approved resident; % of its workforce KYC-approved; time to a new user's first completed action (target: under five minutes, unassisted).

**Trust & safety** — share of gate decisions resolved `ALLOWED` vs. routed to human review, and how fast; complaint SLA compliance (4h urgent / 24h high / 72h normal) and escalation rate; trend of trust scores and how much is backed by real evidence versus the cold-start prior.

**Commercial health** — free-to-paid conversion rate and time-to-conversion; monthly revenue per society against its infrastructure cost; one-day booking attach rate; verification/featured-placement attach rate.

**Worker and household outcomes** — the metrics that justify the ideology, not just the revenue: worker earnings uplift and payment punctuality versus the informal-market baseline; worker retention and repeat engagement; rate at which safety complaints are raised and resolved, and whether workers use the complaint channel at a rate that shows the two-way symmetry is real.

---

## 3. Overall Description

### 3.1 Product Perspective & Architecture

Sathify digitizes a process that today is entirely manual — informal hiring, paper registers, cash payment, no durable record of a worker's reliability. It is a new, standalone product, not a redesign of an existing tool, positioned as a dedicated module a society adopts specifically for its domestic workforce.

[[DIAGRAM:architecture]]

The backend exposes a single versioned JSON API (`/api/v1/`) consumed exclusively by the Flutter client — there is no server-rendered web front end; Django's templating is used only to power the Django Admin, repurposed as the operational review console for approvals.

**Multi-tenancy is structural.** Every model holding society-specific data carries a `society` foreign key via a shared `SocietyScopedModel` base, and every API view filters on it through a `SocietyScopedQuerysetMixin` — one society can never read another's workers, bookings, attendance, or complaints through a shared endpoint.

**External integrations** are REST-based rather than SDK-heavy, to keep the deployment footprint small:

| Integration | Purpose | Notes |
|---|---|---|
| Razorpay | Payments, refunds, webhooks | Called via its REST API directly, not its SDK; test mode only today |
| Firebase Cloud Messaging | Push notifications | Optional — the in-app notification centre is the system of record regardless |
| Gemini / Groq / OpenRouter / Hugging Face | Chatbot, summarisation, sentiment, classification | Four-tier fallback chain; every feature has a non-AI fallback |
| PaddleOCR / EasyOCR | Aadhaar field extraction | Single internal interface; swappable without touching callers |
| DeepFace | Gate face verification | A below-threshold match is never a denial |
| SMS gateway | Notification fallback | Disabled by default; provider-agnostic |

### 3.2 User Classes and Characteristics

| User Class | Description | Technical Proficiency |
|---|---|---|
| **Resident** | Registers a flat; hires or books workers; sets expectations; pays digitally; views attendance and gate history; rates and raises complaints. One primary account holder per flat may create or edit hires. | Basic |
| **Domestic Worker** | Registers via KYC; manages availability and profile photo (also the gate's face-reference image); responds to hires/bookings; marks task completion; requests urgent leave; tracks earnings; rates residents. | Basic |
| **Security Guard** | Scans a worker's QR pass (or a resident-scanned card, or paper register as a last resort); allows/denies entry; resolves low-confidence face checks; works fully offline. | Basic |
| **Society Administrator** | Approves residents and workers; manages the worker directory; runs the complaint queue against its SLA; reviews reports; configures society settings; is the commercial counterparty for the subscription. | Intermediate |

A single `User` record underlies all four roles, distinguished by a mutually-exclusive `role` field and login by **phone number** rather than username or email — the identifier every user in this market already has.

### 3.3 Operating Environment

- **Client:** Flutter (SDK ≥ 3.27, Dart ≥ 3.6), Android first, structured to extend to iOS.
- **Server:** Python 3.13, Django 5.2 (LTS), Django REST Framework, single web service.
- **Database:** PostgreSQL on Supabase, with a transparent local-SQLite fallback for development.
- **Media storage:** Supabase Storage (S3-compatible) in production, since the host filesystem is ephemeral; local storage in development only.
- **Push:** Firebase Cloud Messaging, optional and gracefully degraded.
- **Hosting:** Render, free tier, Singapore region.
- **CI/CD:** Git and GitHub, with system checks, migration-consistency checks, backend tests, and Flutter analysis/tests on every pull request.

### 3.4 Design and Implementation Constraints

- **Mandatory Aadhaar-based KYC before activation.** The full number is never stored — only a masked last-four display and a keyed HMAC hash for duplicate detection, a conservative posture aligned with UIDAI and India's DPDP Act, but not a substitute for legal review at scale.
- **All payments run through a PCI-DSS-compliant gateway (Razorpay)**; card and bank details are never stored by Sathify.
- **Gate decisions cannot depend on server reachability.** The guard's device decides offline against a cached roster, keyed by a client-generated identifier so sync can never duplicate an entry.
- **Every AI feature must degrade to a deterministic fallback**, enforced by a shared utility that requires both paths as mandatory arguments.
- **No automated measurement may deny a person outright** — a weak face match, an out-of-geofence GPS fix, or an unreadable scan always resolves to human review, never rejection.
- **Every dependency runs on a genuinely free tier**, which rules out a background scheduler in production; anything that would be a cron job runs instead as an idempotent, bounded sweep triggered by an ordinary read, an uptime pinger, or a management command.
- **Money is stored as integer paise, never a float or rupee decimal**, to avoid drift against Razorpay's own paise-denominated ledger.

---

## 4. System Features & Functional Requirements

Sathify's scope spans thirteen modules, each mapped one-to-one onto a backend app and a mobile feature folder, giving every module one owner and a self-contained set of data, rules, and tests.

### 4.1 Module 1 — Identity & Access Management

Single authentication record for all four roles, login by **phone number**. JWT access tokens (60 min) with a rotating, blacklistable refresh token (30 days), so a lost device can be revoked without needing to sign in first.

- Phone-based OTP for registration, login, and reset — hashed storage, five-attempt cap, ten-minute validity, resend rate limiting.
- Default-deny role-based access control across the four mutually-exclusive roles.
- An administrator-approval gate (`is_approved`): registration alone grants no platform access.
- Device/session management with explicit revocation; a guard terminal holds one active session at a time.
- Multilingual UI (English, Hindi, Marathi) as a present-day capability.

### 4.2 Module 2 — Society & Resident Onboarding

The multi-tenancy backbone. A **Society** stays `PENDING` — inert — until platform-verified, subdivided into **towers**, **flats**, and staffed **gates**.

- Resident registration against a specific flat, with proof of residence reviewed by an administrator.
- Multiple residents per flat, exactly one **primary account holder** (a database constraint) who alone edits hires and schedules.
- Society-level configuration: towers, flat count, gates, minimum booking notice, guard shift length, GPS self-check-in permission.
- Two-sided trust scoring, so a worker gets the same signal about a household that a resident gets about a worker.

### 4.3 Module 3 — Worker Onboarding & KYC

The trust foundation: a worker cannot be searchable, hired, or gate-admitted until this pipeline completes and an administrator approves.

- Profile with service types, experience, languages, rate, availability, and a mandatory **photo that doubles as the gate's face-verification reference**.
- **An eight-stage OCR pipeline**: image load → preprocessing → detection/recognition (PaddleOCR primary, EasyOCR fallback) → field extraction → Verhoeff checksum validation → an automatic, non-overridable under-18 rejection → cross-check against the registration form.
- **Aadhaar numbers are never stored in full** — only a masked last-four and a keyed hash for cross-society duplicate detection.
- Low-confidence fields are flagged for manual confirmation, never auto-filled silently; when the CV stack is unavailable, the flow falls through cleanly to manual entry.
- Purpose-limited, separately-revocable consent for KYC, face biometrics, and general processing.
- A police-verification badge, dated and expiring, deliberately excluded from search ranking.

### 4.4 Module 4 — Discovery & Hiring (Recurring Engagements)

- **AI-assisted, explainable recommendation**: a weighted match across trust score (30%), rating history (25%), availability overlap (20%), responsiveness (15%), and proximity (10%), shown as a percentage with a full breakdown — never an opaque number.
- **Cold-start smoothing**: new workers with no history are scored at a neutral prior, not zero, so they are not permanently buried.
- **Hire requests with a 48-hour response deadline**; a lapsed request is logged as unmet demand.
- **Engagement lifecycle**: pause/resume without ending the relationship, and a **ten-day notice period** during which the engagement stays fully active and paid, right up to the last working day. A separate, immediate termination path exists for abuse or safety, so a worker reporting harassment is never made to serve notice.
- **No pay is withheld as a notice penalty** — compliance is reflected non-punitively in the trust score instead.

### 4.5 Module 5 — One-Day Service Booking

A distinct path from Module 4 for single, time-bound jobs (shifting, deep cleaning, event prep, temporary cooking, emergency assistance).

- A predefined catalogue of bookable categories with duration and indicative price guidance.
- Per-date worker availability, distinct from general availability hours.
- A configurable minimum booking notice, with an emergency-category exemption.
- Full lifecycle — pending, confirmed, completed, declined, cancelled (with a frozen cancellation fee), or auto-expired.
- Lazy, idempotent expiry of stale bookings and hire requests, evaluated on read rather than by a scheduler.

### 4.6 Module 6 — Scheduling & Task Management

A thin calendar layer: visits are always derived on read from an engagement's or booking's own terms, never materialised separately.

- Per-engagement arrival/departure timing with a configurable grace window (default 15 min).
- Worker-marked task completion, tracked independently of gate attendance.
- Durable, row-based reminders delivered through Module 10.
- **Urgent leave ("chutti") approved instantly, with no justification required.** The resident is then asked only whether they need a replacement; if so, one is matched via the existing ranking logic and the day's pay split per an engagement-level rule (default: full rate to the replacement). The missed day is never double-deducted — attendance pro-rating already removes it from the absent worker's pay.

### 4.7 Module 7 — Attendance & Gate Verification

- **Opaque QR gate passes** — printable for a worker without a smartphone, or in-app — instantly revocable and reissuable.
- **Offline-first scanning**: the guard's device decides against a locally cached roster, keyed by a client-generated identifier so a later sync can never duplicate an entry.
- **Face verification** against the registered photo; **a below-threshold match is never a denial** — it becomes `PENDING_REVIEW` for the guard to resolve explicitly.
- **A tiered fallback**, never allowed to deny entry below the top tier: (1) guard QR scan, optionally with face check; (2) worker self-check-in inside a ~250 m GPS geofence; (3) resident-scanned printed card; (4) a photographed paper register as a last resort.
- A complete, append-only log of every entry, exit, method, and override.

### 4.8 Module 8 — Payments & Payouts

- **Money is stored as integer paise**, never a float, to avoid drift against Razorpay's own accounting.
- Covers recurring salaries, bookings, tips, refunds, and replacement earnings, each with a human-readable receipt number.
- **A payment is marked paid only on a signature-verified message from Razorpay** — never on a client's own claim.
- Every webhook is stored and deduplicated by its provider event ID, for both idempotency and audit.
- A configurable, engagement-level replacement-earnings split for same-day cover.
- Payment disputes feed the same complaint workflow used everywhere else (Module 11).
- The subscription tier and platform-fee mechanics from Section 2 live here, including a due-date on every payment and a fee column that exists today, frozen at zero.

### 4.9 Module 9 — Ratings, Reviews & Trust Score

- **Worker trust score**: ratings (35%), attendance reliability (30%), verification completeness (20%), completion rate (15%).
- **Resident trust score**: payment punctuality (45%), worker-given ratings (35%), upheld complaints (20%).
- **Every score is explainable**: stored with a full, frozen, per-component breakdown at the moment it was computed, so a dispute is answered with the reasoning that produced it, not a fresh recalculation.
- Cold-start smoothing, identical in spirit to Module 4, so a newcomer is not scored at zero.
- Sentiment/theme analysis is stored separately from the raw review text, which is never overwritten by a model's interpretation.
- Suspicious-rating detection flags for administrator review rather than auto-deleting — a false positive should be recoverable and appealable.

### 4.10 Module 10 — Notifications

- **The in-app notification record is the durable system of record**, written before any delivery attempt and preserved regardless of what push or SMS does afterward.
- Category-based mute preferences — except **gate-entry alerts, urgent-leave/replacement notices, and account/verification notices, which can never be muted**, enforced server-side.
- Graceful fallback to a provider-agnostic SMS gateway when push fails or is unavailable.

### 4.11 Module 11 — Admin, Reporting & Complaints

- **Either party may raise a complaint against the other, or against the society itself.**
- **An SLA deadline is set once, at creation, and never moved** — escalation reorders the queue by raising priority, without shifting the deadline.
- The clock runs only during realistic waking hours (08:00–21:00, all seven days); indicative windows are 4h urgent, 24h high, 72h normal.
- **Complaints are never deleted, only closed**, with an append-only history of every transition.
- Safety-category complaints are automatically escalated on arrival.
- An unmet-demand log records every lapsed hire request or unfilled booking/replacement, for the committee to act on.
- A worker/resident directory built on customised Django Admin views, separate from the onboarding approval screens.

### 4.12 Module 12 — AI Layer

Every AI feature must answer identically, if less richly, when no provider is reachable — enforced by a shared utility requiring both an AI path and a fallback as mandatory arguments.

- **A four-tier fallback chain**: Gemini → Groq → OpenRouter (free catalogue) → Hugging Face, each skipped silently if unconfigured.
- Locally enforced rate limiting against the tightest published free-tier ceiling, so the chain fails over before a provider ever returns an error.
- **AI worker recommendation** — the same explainable formula from Module 4, exposed as an independent service so it can later be replaced by a learned model with no change to the hiring flow.
- **A conversational assistant** that only identifies *which* question is being asked; every figure in the answer is read straight from the same queries the corresponding screen uses — the model never composes the answer or sees the underlying data.
- OCR extraction (Module 3) and face verification (Module 7), routed through the same degradation convention.
- Review summarisation, sentiment analysis, and complaint classification.
- Usage logging captures which tier answered, latency, and outcome — **never the prompt or response content itself.**

### 4.13 Module 13 — Offline & Resilience Conventions

Engineering conventions applied wherever connectivity or a specific person's presence cannot be guaranteed, tested and enforced as code rather than left as prose:

- **Client-generated identifiers** for anything recorded offline, minted before the server ever sees the record.
- **Idempotent sync endpoints** — a replayed batch reports success, not error, and one bad row rejects only itself, never the whole batch.
- **A tiered attendance-evidence model** in which no fallback tier below the top may deny entry on its own.
- **A mandatory AI fallback** for every AI-backed feature.
- **The rule underlying all of the above**: no automated tier — a face score, a GPS fix, an OCR confidence value, an AI answer — may alone refuse a person something that costs them income or access.

---

## 5. Non-Functional Requirements

### 5.1 Performance

- Gate decisions are made and recorded without waiting on a network round trip, by design.
- OCR extraction targets a few seconds per document, with an automatic engine fallback and a manual-entry path.
- Standard list-view pagination (20 records/page) keeps response times bounded regardless of a society's history.
- Built and tested for a residential-community concurrency profile; horizontal scaling across societies is an infrastructure question, not an architectural one.

### 5.2 Security

- Default-deny role-based access control on every endpoint.
- Structural multi-tenant isolation via a society reference on every scoped model and query.
- Aadhaar numbers never stored in full; masked display and hashed duplicate-detection only.
- No card or bank data stored — delegated entirely to Razorpay, with every webhook signature-verified.
- **A below-threshold face match is never a denial**, routed instead to a guard for an explicit, logged decision.
- Short-lived JWT access tokens with rotating, revocable refresh tokens.
- Purpose-limited, individually-revocable consent per sensitive-data purpose.
- All client-server traffic over HTTPS.

### 5.3 Scalability

- Multi-tenant by construction and keyed on society; onboarding a new society needs no architectural change.
- The AI layer's four-tier fallback and local rate limiting let AI usage degrade gracefully rather than fail outright at a free-tier ceiling.
- Idempotent sweeps scale with read traffic today and move onto a real scheduler later with no logic change.

### 5.4 Reliability & Availability

- Every AI, OCR, and face-verification feature ships with a working fallback in the same change as the feature itself.
- Gate verification survives both network loss and AI-service outage via its tiered fallback, never denying entry automatically.
- Payment state advances only on a cryptographically verified gateway message; every webhook is logged and deduplicated.
- A minimum three-year audit trail across bookings, attendance, payments, gate events, and complaints; seven years for financial records.
- The mobile client shows an explicit "waking up the server" state rather than a silent hang during a free-tier cold start.

### 5.5 Usability & Accessibility

- A first-time user completes registration and a first meaningful action within about five minutes, unassisted.
- Clear iconography and simple language across varying digital-literacy levels.
- Multilingual support (English, Hindi, Marathi) as a present-day capability.
- A printed QR card and resident-operated scan fallback keep a worker without a smartphone from being excluded.

### 5.6 Auditability & Compliance

- A complete, append-only audit trail across bookings, attendance, gate events, payments, and complaints (3-year minimum; 7 years for financial records).
- Every trust score and recommendation ranking stored with a frozen, per-component breakdown.
- Identity-document handling follows a minimisation-first posture aligned with India's DPDP Act and UIDAI restrictions — an engineering default, not a substitute for legal review at scale.
- CSV/PDF export for attendance, payment, and complaint records.

### 5.7 Deployment Requirements & Infrastructure Strategy

The current deployment runs entirely on free-tier infrastructure, treated as an architectural input rather than a temporary shortcut:

- **API hosting**: a single Render free web service (Singapore), Gunicorn with one worker/four threads to fit 512 MB RAM.
- **Database**: PostgreSQL on Supabase's free tier via its connection pooler, with connection reuse to reduce churn.
- **Media**: Supabase Storage, since the host's own filesystem is ephemeral and wiped on every restart or sleep cycle.
- **The two hardest constraints**: a ~50-second free-tier cold start, addressed by making gate verification offline-first rather than server-dependent; and 512 MB of RAM being unable to hold Django and the OCR/face stack together, addressed today by running the CV stack locally for development/demo, with a small dedicated microservice or a lighter on-device model identified as the production paths.
- **No background scheduler**: every would-be cron job runs as an idempotent, bounded sweep instead.
- **Payments run in Razorpay test mode**, with a code-level guard rail refusing to initiate a live transaction while test mode is enabled.
- A pre-planned, pre-costed migration path off free infrastructure (Section 2.3), triggered by named ceilings rather than a calendar date.

---

## 6. Future Scope & Roadmap

Natural extensions of the existing architecture, deliberately out of the current release so the present system stays focused, tested, and free to run:

- **A public iOS release** — the Flutter codebase extends without a rewrite; requires an Apple Developer Program enrolment.
- **Insurance integration for domestic workers**, building on the existing verified-identity and attendance history.
- **Loan-eligibility assessment** from a worker's verified employment and payment history.
- **A portable digital salary/employment record** a worker can carry between households and platforms.
- **Smart-gate hardware integration** (turnstiles, dedicated biometric readers) alongside the existing QR/face workflow.
- **A learned-model replacement** for the rule-based recommendation scorer, a swap the architecture already supports without touching the hiring flow.
- **AI-driven attendance-absence prediction** and **AI-assisted fraud detection**, building on the existing AI usage-logging foundation.
- **An AI smart-scheduler** to optimise timing across a worker's engagements.
- **Automated monthly insight summaries** for workers and residents.
- **Self-serve subscription checkout**, once the hand-sold Standard/Plus tiers prove a repeatable sales motion.
- **RazorpayX payouts and full Razorpay Route splitting**, formalising tip and replacement settlement as worker banking coverage grows.
- **Expanded biometric and voice-assistant capabilities** beyond gate verification.
- **A move from Razorpay test mode to live processing**, contingent on business verification.
