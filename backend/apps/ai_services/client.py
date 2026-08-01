"""
Module 12 — the four-tier chain.

One entry point, :func:`complete`, which walks the configured tiers in order
until one answers. Everything else in this app calls it; nothing else calls
:mod:`apps.ai_services.providers` directly.

-------------------------------------------------------------------------------
THE CHAIN IS ORDERED, NOT LOAD-BALANCED
-------------------------------------------------------------------------------
Tiers are tried strictly in sequence and the first success wins. That is
deliberate: the tiers are not equivalent. Prompts are written and tested against
Gemini, and Tier 4 is a last resort whose output is expected to be worse. Round-
robining would make quality depend on which provider happened to be up, and
would make a bad answer impossible to reproduce.

-------------------------------------------------------------------------------
TIER 3 IS CAPPED LOCALLY BEFORE IT IS CALLED
-------------------------------------------------------------------------------
OpenRouter allows 20 requests a minute and 50 a day. Asking it for a 51st is a
wasted round trip that returns an error; :class:`AiUsageCounter` refuses the
attempt first and the chain moves to Tier 4 immediately. Quota reserved for an
attempt that never reached the provider is released again, so an outage does not
silently eat the day's allowance.

-------------------------------------------------------------------------------
EVERY CALL IS LOGGED, INCLUDING THE ONES THAT WENT NOWHERE
-------------------------------------------------------------------------------
"How often does this actually fall past Tier 1?" is the question the whole
four-tier design rests on, and provider dashboards cannot answer it because they
each only see their own traffic. The log row records the tiers attempted in
order — not the prompt, and not the answer (see ``models.py``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from . import providers
from .models import AiFeature, AiOutcomeKind, AiRequestLog, AiUsageCounter, UsageWindow

logger = logging.getLogger(__name__)

#: Fenced code blocks around JSON. Models add them despite being asked not to,
#: and a leading ```json is the single most common reason a parse fails.
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

#: The first {...} or [...] in a response. Used when a model prefixes its JSON
#: with a sentence of explanation, which Tier 4 does routinely.
_JSON_SPAN = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


@dataclass
class AiResult:
    """What the chain produced."""

    ok: bool = False
    text: str = ""

    #: Which provider answered. Blank when none did.
    tier: str = ""

    #: Every tier tried, in order, whether or not it answered.
    tiers_attempted: list[str] = field(default_factory=list)

    latency_ms: int = 0
    reason: str = ""

    @property
    def fell_through(self) -> bool:
        return len(self.tiers_attempted) > 1


def _reserve(tier: providers.Tier) -> tuple[bool, list[str]]:
    """Take quota for a tier. Returns ``(allowed, windows_reserved)``.

    Both windows must be available: a tier inside its daily cap but over its
    per-minute cap is still not callable right now.
    """
    reserved: list[str] = []

    for window, cap in (
        (UsageWindow.MINUTE, tier.per_minute_cap),
        (UsageWindow.DAY, tier.daily_request_cap),
    ):
        if cap <= 0:
            continue
        if not AiUsageCounter.reserve(tier.name, window=window, cap=cap):
            # Give back whatever was already taken for this attempt.
            for taken in reserved:
                AiUsageCounter.release(tier.name, window=taken)
            return False, []
        reserved.append(window)

    return True, reserved


def _release_all(tier_name: str, windows: list[str]) -> None:
    for window in windows:
        AiUsageCounter.release(tier_name, window=window)


def complete(
    prompt: str,
    *,
    feature: str = AiFeature.CHAT,
    system: str = "",
    max_tokens: int = providers.DEFAULT_MAX_TOKENS,
    user=None,
    society=None,
) -> AiResult:
    """Ask the chain a question. Never raises.

    Returns ``ok=False`` when the layer is switched off, no tier is configured,
    or every configured tier failed. Callers are expected to have a fallback —
    see :mod:`apps.ai_services.degradation`, which is the convention Module 12.6
    requires rather than an option.
    """
    if not providers.is_enabled():
        return AiResult(reason="The AI layer is switched off on this server.")

    configured = [tier for tier in providers.tiers() if tier.is_configured]
    if not configured:
        return AiResult(reason="No AI provider is configured on this server.")

    attempted: list[str] = []
    total_latency = 0
    last_reason = ""

    for tier in configured:
        allowed, windows = _reserve(tier)
        if not allowed:
            # Not an attempt — the provider was never contacted. Recorded so the
            # log shows why a tier was skipped rather than appearing to fail.
            attempted.append(f"{tier.name}:capped")
            last_reason = f"{tier.name} is at its local rate cap."
            continue

        attempted.append(tier.name)
        response = providers.call(
            tier, prompt=prompt, system=system, max_tokens=max_tokens
        )
        total_latency += response.latency_ms

        if response.ok:
            result = AiResult(
                ok=True,
                text=response.text.strip(),
                tier=tier.name,
                tiers_attempted=attempted,
                latency_ms=total_latency,
            )
            _log(result, feature=feature, prompt=prompt, user=user, society=society)
            return result

        # The provider was reached and refused, or was never reached at all.
        # Either way this attempt produced nothing, so the quota goes back.
        _release_all(tier.name, windows)
        last_reason = response.reason
        logger.info("AI tier %s did not answer: %s", tier.name, response.reason)

    result = AiResult(
        tiers_attempted=attempted,
        latency_ms=total_latency,
        reason=last_reason or "Every provider declined.",
    )
    _log(result, feature=feature, prompt=prompt, user=user, society=society)
    return result


def complete_json(
    prompt: str,
    *,
    feature: str = AiFeature.CHAT,
    system: str = "",
    max_tokens: int = providers.DEFAULT_MAX_TOKENS,
    user=None,
    society=None,
) -> tuple[dict | list | None, AiResult]:
    """Ask for JSON and parse it defensively.

    Returns ``(parsed, result)``. ``parsed`` is None when the chain failed *or*
    when it answered with something that is not JSON — which callers must treat
    identically, because an unparseable answer is exactly as useful as no answer.
    """
    result = complete(
        prompt,
        feature=feature,
        system=system or JSON_SYSTEM_PROMPT,
        max_tokens=max_tokens,
        user=user,
        society=society,
    )
    if not result.ok:
        return None, result

    parsed = parse_json(result.text)
    if parsed is None:
        result.ok = False
        result.reason = "The model did not return usable JSON."
    return parsed, result


#: Prepended to any call that expects JSON back. Every tier ignores this
#: occasionally, which is what :func:`parse_json` is for.
JSON_SYSTEM_PROMPT = (
    "You return only valid JSON. No prose, no explanation, no markdown code "
    "fences. If you are unsure, return your best guess in the requested shape."
)


def parse_json(text: str) -> dict | list | None:
    """Pull JSON out of a model's answer, tolerating the usual decorations.

    Three passes, cheapest first: parse it as-is, strip code fences, then take
    the first balanced-looking span. Anything still unparseable returns None
    rather than raising — the caller's fallback is a better answer than a 500.
    """
    if not text:
        return None

    for candidate in (text, _FENCE.sub("", text).strip()):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue

    match = _JSON_SPAN.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except (ValueError, TypeError):
            pass

    logger.info("Could not parse JSON from a model response of %s chars", len(text))
    return None


def _log(result: AiResult, *, feature: str, prompt: str, user, society) -> None:
    """Record the attempt. Never raises — logging must not break a feature."""
    try:
        AiRequestLog.objects.create(
            feature=feature,
            outcome=AiOutcomeKind.AI if result.ok else AiOutcomeKind.UNAVAILABLE,
            tier=result.tier,
            tiers_attempted=result.tiers_attempted,
            latency_ms=result.latency_ms,
            prompt_chars=len(prompt or ""),
            response_chars=len(result.text or ""),
            error=result.reason[:300],
            user=user if getattr(user, "pk", None) else None,
            society=society,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record an AI request log")


__all__ = [
    "AiResult",
    "JSON_SYSTEM_PROMPT",
    "complete",
    "complete_json",
    "parse_json",
]
