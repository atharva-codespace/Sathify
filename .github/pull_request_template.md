<!--
Keep this short. The point is that the other three can review without first
reconstructing what you were doing.
-->

## What this changes

<!-- One or two sentences. -->

**Module:** <!-- e.g. Module 5 — Bookings, or "core/shared" -->

## Why

<!-- Link the issue if there is one: Closes #12 -->

## How I checked it

- [ ] `python -m pytest -m "not ml"` passes (backend changes)
- [ ] `flutter test` and `flutter analyze` pass (mobile changes)
- [ ] Tried it by hand — say what you did:

## Things worth flagging

<!-- Delete the lines that do not apply. -->

- [ ] Touches files **outside my module** (`apps/core/`, `lib/core/`,
      `lib/shared/`, `config/settings/`, `config/urls.py`, `pubspec.yaml`,
      `requirements/`) — these are the files that cause merge conflicts, so say
      so here and give the others a heads-up.
- [ ] Adds a **migration**.
- [ ] Adds or renames a **setting** — added to `.env.example` too?
- [ ] Changes an **API response shape** the Flutter side reads.
