"""
Module 12.6 — the graceful-degradation convention.

Every AI-backed feature in this codebase goes through :func:`with_fallback`.
That is the whole of the convention, and it exists because the modspec is
explicit that fallbacks are "coded at the same time as the AI integration
itself, not added afterward" — which only holds if writing the AI path without
one is awkward.

-------------------------------------------------------------------------------
THE FALLBACK IS AN ARGUMENT, NOT AN AFTERTHOUGHT
-------------------------------------------------------------------------------
:func:`with_fallback` takes both paths as required parameters. There is no way
to call it with only the AI half. A developer adding a sixth AI feature has to
answer "and what happens when this is unavailable?" before the code compiles,
rather than discovering the question during the first provider outage.

-------------------------------------------------------------------------------
THE CALLER ALWAYS KNOWS WHICH PATH ANSWERED
-------------------------------------------------------------------------------
:class:`Degraded` carries ``source`` and ``tier``. Every screen that shows an
AI-derived figure can therefore say so, and Module 9's ``ReviewSentiment.engine``
column and Module 7's per-event engine field exist for the same reason: an
answer from a rule-based fallback and an answer from a model are both valid, but
conflating them makes a weak guess indistinguishable from a finding.

-------------------------------------------------------------------------------
NOTHING HERE RAISES, INCLUDING THE FALLBACK
-------------------------------------------------------------------------------
If the AI path throws, the fallback runs. If the fallback throws too, the result
is ``UNAVAILABLE`` with the default value — because the caller is somewhere in
the middle of a gate scan, a review submission or a complaint being raised, and
none of those may fail because a summariser did.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .models import AiFeature, AiOutcomeKind, AiRequestLog

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class Degraded(Generic[T]):
    """A value, plus an honest account of where it came from."""

    value: T
    source: str = AiOutcomeKind.FALLBACK

    #: Which provider answered, when one did.
    tier: str = ""

    #: Why the AI path did not serve this. Shown to operators, not to users.
    reason: str = ""

    @property
    def from_ai(self) -> bool:
        return self.source == AiOutcomeKind.AI

    @property
    def is_available(self) -> bool:
        """Whether *any* path produced a real answer.

        False only when both the model and the fallback failed, which is the
        one case a caller may need to treat as "say nothing" rather than "say
        this".
        """
        return self.source != AiOutcomeKind.UNAVAILABLE

    @property
    def engine(self) -> str:
        """A short label to store alongside the value.

        Written into ``ReviewSentiment.engine`` and the like, so a row produced
        by Gemini in March stays attributable after the provider is swapped.
        """
        if self.from_ai:
            return self.tier or "ai"
        return "fallback" if self.is_available else "unavailable"


def with_fallback(
    feature: str,
    *,
    ai: Callable[[], tuple[T | None, str, str]],
    fallback: Callable[[], T],
    default: T | None = None,
    user=None,
    society=None,
) -> Degraded[T]:
    """Run an AI path, falling back to a rule-based one. Never raises.

    ``ai`` returns ``(value, tier, reason)``. A value of ``None`` means the AI
    path declined — no key, provider down, unparseable answer — and ``reason``
    explains which. Returning a tuple rather than raising keeps the "no provider
    configured" case, which is the *normal* state of a fresh clone, off the
    exception path.

    ``fallback`` takes no arguments and is expected to work offline.
    """
    tier = ""
    reason = ""

    try:
        value, tier, reason = ai()
        if value is not None:
            # Not logged here. Reaching this line means the chain answered, and
            # `client.complete` already wrote the row — logging again would
            # double-count exactly the statistic the log exists to measure.
            return Degraded(value=value, source=AiOutcomeKind.AI, tier=tier)
    except Exception as exc:  # noqa: BLE001 — an AI bug must not break the caller
        logger.exception("AI path for %s raised", feature)
        reason = str(exc)

    try:
        result = Degraded(
            value=fallback(),
            source=AiOutcomeKind.FALLBACK,
            reason=reason,
        )
        # Logged, because this is the one outcome the client layer cannot see:
        # it knows a provider declined, not that something else answered.
        _log(feature, AiOutcomeKind.FALLBACK, tier, reason, user, society)
        return result
    except Exception as exc:  # noqa: BLE001
        # Both paths gone. Rare, and worth a real log line: the fallback is
        # supposed to be the thing that always works.
        logger.exception("Fallback for %s raised as well", feature)
        _log(feature, AiOutcomeKind.UNAVAILABLE, tier, str(exc), user, society)
        return Degraded(
            value=default,  # type: ignore[arg-type]
            source=AiOutcomeKind.UNAVAILABLE,
            reason=f"{reason} / fallback: {exc}".strip(" /"),
        )


def _log(feature: str, outcome: str, tier: str, error: str, user, society) -> None:
    """Record an outcome the client layer could not have recorded.

    Only fallback and unavailable outcomes reach here — see the comments in
    :func:`with_fallback`. The client layer logs every provider attempt itself;
    what it cannot know is whether anything picked up afterwards.
    """
    if feature not in AiFeature.values:
        return

    try:
        AiRequestLog.objects.create(
            feature=feature,
            outcome=outcome,
            tier=tier,
            error=error[:300],
            user=user if getattr(user, "pk", None) else None,
            society=society,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record a degradation log")


__all__ = ["Degraded", "with_fallback"]
