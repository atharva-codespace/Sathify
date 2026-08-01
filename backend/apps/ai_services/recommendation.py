"""
Module 12.1 — the worker recommendation service.

The modspec asks for "the scoring logic from Module 4.3, exposed as its own
internal service so it can be swapped for a learned model later without touching
the hiring flow that depends on it". This module is that seam, and nothing more.

-------------------------------------------------------------------------------
THIS IS A BOUNDARY, NOT A REIMPLEMENTATION
-------------------------------------------------------------------------------
The formula stays in :mod:`apps.hiring.scoring`, where it was written and where
its tests live. Copying it here to make Module 12 look substantial would leave
two formulas to keep in agreement, and they would diverge the first time someone
tuned a weight.

What changes is who Modules 4 and 5 import from. They now call this service, so
replacing the rule-based scorer with a learned model is an edit to
:data:`ENGINE` and :func:`rank_workers` — not to the hiring flow, the booking
matcher, or any of their tests.

-------------------------------------------------------------------------------
WHY THIS DOES NOT GO THROUGH THE DEGRADATION WRAPPER
-------------------------------------------------------------------------------
Module 12.6 requires every *AI call* to carry a fallback. There is no AI call
here yet: the v1 engine is a pure local computation that cannot fail, be rate
limited, or cost anything, and it runs on every search — wrapping it would log a
"degraded" row per ranked list for a path that never degrades.

When a learned model does land, it belongs behind :func:`with_fallback` with
this rule-based scorer as its fallback. That is the intended shape, and it is
why :func:`rank_workers` returns the same type either way.
"""

from __future__ import annotations

import logging

from apps.hiring.scoring import MatchScore, ScoringInputs, score

logger = logging.getLogger(__name__)

#: Which scorer produced a ranking. Stored nowhere yet, returned everywhere, so
#: that when a second engine exists a result can be attributed to one of them.
ENGINE = "rule_based_v1"


def score_inputs(inputs: ScoringInputs) -> MatchScore:
    """Score one worker from already-gathered signals.

    The pure entry point: no database, no Django. Module 4's ``services.py``
    gathers the inputs; this decides what they are worth.
    """
    return score(inputs)


def rank(scored: list[tuple[object, MatchScore]]) -> list[tuple[object, MatchScore]]:
    """Order a scored list best-first.

    Ties break on the score alone here; Module 4's ``rank_workers`` adds trust
    and rating as secondary keys because it has the model objects to read them
    from. Kept separate so a learned model can reorder without needing them.
    """
    return sorted(scored, key=lambda pair: pair[1].total, reverse=True)


def engine_name() -> str:
    """What is doing the ranking right now.

    Exposed so the API can tell a resident that the match percentage came from
    a rule-based scorer rather than implying a model they might over-trust.
    """
    return ENGINE


__all__ = ["ENGINE", "MatchScore", "ScoringInputs", "engine_name", "rank", "score_inputs"]
