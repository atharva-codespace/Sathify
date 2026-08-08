# Manual test cases

What the automated suite cannot reach, and what to do about it by hand.

`pytest` covers 1,124 cases and everything in it is deterministic. Three things
are not, and they are exactly the three that decide whether somebody gets
through a gate or gets paid:

* **OCR against real-world image variance.** Synthetic cards are clean in ways a
  photograph taken in a stairwell is not.
* **Face verification.** DeepFace's accuracy on this population cannot be
  asserted in CI, and a synthetic face proves nothing.
* **Gate hardware.** Cameras, torches, a guard's cracked screen in sunlight, and
  a phone with no signal at the moment it matters.

Run **OCR-01 to OCR-07** and **GATE-01 to GATE-06** before any release that
touches KYC or attendance. The rest are per-feature.

---

## How to run these safely

```bash
# A local database. Never the shared Supabase instance.
cd backend
DJANGO_SETTINGS_MODULE=config.settings.test TEST_DATABASE_URL=sqlite:///db.scratch.sqlite3 \
  ./.venv/Scripts/python.exe manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings.test TEST_DATABASE_URL=sqlite:///db.scratch.sqlite3 \
  ./.venv/Scripts/python.exe manage.py seed_demo
```

**Never photograph a real Aadhaar card for a test.** Use a printed synthetic one
— the whole point of the masking in `apps/workers/ocr/` is that a real number
never reaches storage, a log, or a terminal scrollback, and a test that
introduces one defeats it. Numbers that pass the Verhoeff checksum but belong to
nobody can be generated with `apps.workers.ocr.verhoeff.verhoeff_checksum`.

---

## OCR and document upload

Needs `requirements/ml.txt`. Without it every case below should degrade to
manual entry rather than error — which is itself worth checking once.

| ID | Case | Steps | Expected |
| --- | --- | --- | --- |
| OCR-01 | Clean scan | Photograph a printed synthetic card flat, good light, filling the frame | Name, DOB, gender, last-4 extracted. Checksum valid. Confirmation screen pre-filled. |
| OCR-02 | Angled photograph | Same card at roughly 10–15° | Deskew corrects it; extraction matches OCR-01. **Known weak:** synthetic testing showed rotation costing the Aadhaar number entirely. Record what you get. |
| OCR-03 | Poor light | Photograph in a dim stairwell | Either extracts, or fails to FAILED status with the manual-entry path offered. Never a 500. |
| OCR-04 | Glare | Photograph under direct light with visible glare on the laminate | As OCR-03. |
| OCR-05 | Not a document | Upload a photo of a wall | FAILED, manual entry offered, no crash. |
| OCR-06 | Wrong file type | Upload a PDF, then a .txt renamed to .png | PDF is accepted (Stage 1 handles it); the renamed text file is refused with a readable message. |
| OCR-07 | **Which engine served it** | Upload anything and check the response's `ocr_engine` | Should read `paddleocr`. If it reads `easyocr`, the primary engine is broken — see the `paddlex` note in `requirements/ml.txt`. **This is the current known state.** |
| OCR-08 | Cross-check mismatch | Register as "Sunita Rao", then upload a card reading "Priya Sharma" | Stage 8 flags a mismatch; the confirmation screen highlights name and DOB. |
| OCR-09 | Minor | Upload a card with a DOB under 18 | Registration auto-rejected, not queued. Module 3.4 is a hard block. |
| OCR-10 | Duplicate | Upload the same card on two accounts | Second is flagged as a duplicate for an administrator. The full number is never shown. |
| OCR-11 | Consent refused | Untick the consent box and upload | Refused before the file is stored. Check no `KycDocument` row and no file exist. |
| OCR-12 | Storage down | Stop the storage backend and upload | 503 with a retryable message, not a 500. |

### What "correct extraction" means here

Name and DOB matching the printed card, and the **last four digits only** in
`masked_aadhaar`. If a full twelve-digit number appears anywhere in the API
response, the admin screen, or a log line, **stop and treat it as a defect** —
that is the one guarantee this module exists to provide.

---

## Face verification and liveness

Needs DeepFace. `ENABLE_FACE_VERIFICATION` must be on.

| ID | Case | Steps | Expected |
| --- | --- | --- | --- |
| FACE-01 | Match | Register a worker with a clear photo; scan them at the gate and capture a live photo | `verified: true`, entry stays ALLOWED. |
| FACE-02 | Non-match | Capture a different person's face against that worker's pass | `verified: false`, decision becomes **PENDING_REVIEW, never DENIED**. The guard decides. |
| FACE-03 | No registered photo | Remove the worker's photo, then run a check | Reported as unavailable, not as a failed match. |
| FACE-04 | Engine absent | Uninstall DeepFace and run a check | Unavailable, guard decides visually, no crash. |
| FACE-05 | Poor light | Capture in a dark doorway | Whatever the score, the outcome is never DENIED. |
| FACE-06 | Remote storage | With Supabase storage configured, run FACE-01 | Works. Regression guard for `FieldFile.path`, which does not exist on S3 — see `apps/core/files.py`. |
| FACE-07 | Liveness (**not built**) | — | Photo-of-a-photo currently passes. On-device ML Kit liveness is designed, not implemented. |

**FACE-02 is the one that matters.** Face recognition is least accurate for
exactly the people this platform serves. A false rejection costs somebody a
day's pay, so the model never turns anyone away on its own. If a run ever
produces DENIED from a face check alone, that is a release blocker.

---

## Gate hardware and offline

| ID | Case | Steps | Expected |
| --- | --- | --- | --- |
| GATE-01 | QR scan | Scan a worker's pass from the guard screen | Worker resolves, expected visits listed, decision recorded. |
| GATE-02 | Scan in sunlight | Same, outdoors at midday | Camera focuses; if not, the "log by hand" fallback is reachable in one tap. |
| GATE-03 | Torch | Scan in the dark using the torch toggle | Works. |
| GATE-04 | **Aeroplane mode** | Turn off all connectivity, scan five workers in and out, restore connectivity | All ten events sync. **No duplicates.** This is the property the whole offline design rests on. |
| GATE-05 | Replayed sync | Trigger the same sync batch twice | Duplicates reported as success, not error, and nothing is double-logged. |
| GATE-06 | Cold start | Leave the app 20 minutes so Render sleeps, then scan | Retries absorb the wake; the guard sees a delay, not an error. |
| GATE-07 | Unknown card | Scan a code from another society | Refused with a message distinguishing "not recognised" from "wrong society". |
| GATE-08 | Revoked pass | Rotate a worker's pass, then scan the old card | Flagged as unusable; the guard can still override, and the override is recorded. |
| GATE-09 | Exit scan | Scan a worker out after check-in | `Direction.EXIT` recorded; the resident's dashboard shows departure confirmed. |
| GATE-10 | Paper register | Photograph a paper register page and upload | Stored for transcription; transcribing produces ordinary attendance events. |
| GATE-11 | Tier 2 self check-in | As a worker inside the society, self check-in | ALLOWED. Repeat from a different city: PENDING_REVIEW, never DENIED. |
| GATE-12 | Tier 2.5 resident scan | As a resident, scan a worker you employ | Recorded, `recorded_by` is you. Repeat for a worker you do not employ: 403. |

---

## Payments

| ID | Case | Steps | Expected |
| --- | --- | --- | --- |
| PAY-01 | Razorpay checkout | `manage.py sample_payment --order`, then complete checkout with a test card | Payment reaches PAID only via a verified signature. |
| PAY-02 | Abandoned checkout | Start checkout, close the sheet | Payment stays PENDING; reopening uses the same order id. |
| PAY-03 | Webhook replay | Send the same webhook twice | Settled once. |
| PAY-04 | Tips owed | Pay with a tip, then open the admin tips list | Worker listed with the receipt number for hand settlement. |
| PAY-05 | Fee is off | Create a booking payment | `platform_fee_paise` is 0 and no fee line renders. |

---

## Notifications

| ID | Case | Expected |
| --- | --- | --- |
| NOTIF-01 | Tap a complaint notification | Opens `/complaints`. **Never** a crash — this was a live defect. |
| NOTIF-02 | Tap a notification whose route this build does not know | Lands on the not-found screen, keeps the navigation stack. |
| NOTIF-03 | Urgent leave notification | Arrives even with notifications muted (safety-critical category). |

---

## Findings from the last walkthrough

Run 8 Aug 2026, synthetic cards, full ML stack installed.

| Finding | Severity | State |
| --- | --- | --- |
| **PaddleOCR never runs.** `paddlex 3.7.2` breaks a constructor paddleocr calls positionally; every document silently served by EasyOCR | High — accuracy loss, invisible | Upper bound added to `ml.txt`, **unverified**. `is_available()` now constructs the reader so it stops lying. |
| **Rotation loses the Aadhaar number.** A 7° tilt dropped the number entirely and truncated the name, at 0.97 confidence | High — this is the common real-world photo | Open. Deskew fixed separately (`estimate_skew_angle` normalised only one tail), but extraction still degrades. Needs OCR-02 with real photographs. |
| **A blank page reports `needs_manual_confirmation = False`.** Nothing extracted means no low-confidence fields, so a total failure looks like a clean read | Medium | Open. Worker would be told "Document read successfully". |
| Deskew was returning 0.0 for every tilted image | High | **Fixed** — OpenCV 5's `minAreaRect` returns the negative tail; only `> 45` was normalised. |

---

## What is automated, for reference

Do not re-test these by hand; the suite is faster and stricter.

| Area | Tests |
| --- | --- |
| Full backend suite | **1,124** |
| Urgent leave / chutti | 36 |
| Notice period | 25 |
| Visit state machine | 20 |
| Fees, subscription, badges | 29 |
| Due dates + `sample_payment` | 17 |
| Dashboard | 15 |
| Resident scan (tier 2.5) | 16 |
| Photo uploads | 13 |
| OCR stages | 89 |
| Flutter | **349** |
