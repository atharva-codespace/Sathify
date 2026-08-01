"""
Module 12 — the four provider tiers.

One adapter per provider, all returning the same :class:`ProviderResponse`, so
:mod:`apps.ai_services.client` can walk the chain without knowing which
provider it is talking to.

-------------------------------------------------------------------------------
NOTHING HERE RAISES
-------------------------------------------------------------------------------
Every failure — no key, bad key, timeout, rate limit, malformed response,
provider outage — comes back as ``ok=False`` with a reason. The chain needs a
*result* to decide whether to try the next tier; an exception would either abort
the whole chain at Tier 1 or have to be caught four times over.

-------------------------------------------------------------------------------
THREE OF THE FOUR SPEAK THE SAME PROTOCOL
-------------------------------------------------------------------------------
Groq, OpenRouter and Hugging Face all expose OpenAI-compatible
``chat/completions``, so they share one implementation and differ only in
endpoint, key and model. Gemini has its own request and response shape and gets
its own function. That asymmetry is the provider's, not ours, and pretending
otherwise would mean an abstraction layer over two shapes.

-------------------------------------------------------------------------------
WHY requests AND NOT EACH PROVIDER'S SDK
-------------------------------------------------------------------------------
Four SDKs to make one authenticated POST each, on a 512 MB instance that already
cannot hold the CV stack. ``requests`` is already a dependency; the SDKs would
be four dependency trees for four HTTP calls. The same reasoning that put
Module 8 on the Razorpay REST API and Module 10 on FCM's REST API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

#: Providers speaking the OpenAI chat-completions protocol.
OPENAI_COMPATIBLE = {"groq", "openrouter", "huggingface"}

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_TOKENS = 800

#: Sent by OpenRouter for attribution. Harmless elsewhere; it is a plain header.
REFERER = "https://sathify.example"


@dataclass
class ProviderResponse:
    """What one provider attempt produced."""

    ok: bool = False
    text: str = ""
    tier: str = ""
    latency_ms: int = 0
    reason: str = ""

    #: True when the provider itself said "slow down" or "out of quota". The
    #: chain treats this the same as any other failure — move on — but it is
    #: worth distinguishing in logs, because it means the local cap is wrong.
    rate_limited: bool = False


@dataclass
class Tier:
    """One configured provider, as read from ``settings.AI_SETTINGS``."""

    name: str
    api_key: str
    model: str
    endpoint: str
    daily_request_cap: int = 0
    per_minute_cap: int = 0

    @property
    def is_configured(self) -> bool:
        """A tier with no key is skipped silently rather than failed loudly.

        A deployment with only a Gemini key is a perfectly normal deployment —
        and a developer with no keys at all should see the fallback path, not
        four errors per call.
        """
        return bool(self.api_key and self.model and self.endpoint)


def tiers() -> list[Tier]:
    """Every configured tier, in the order they are tried."""
    config = getattr(settings, "AI_SETTINGS", {})
    return [
        Tier(
            name=entry.get("name", ""),
            api_key=entry.get("api_key", ""),
            model=entry.get("model", ""),
            endpoint=entry.get("endpoint", ""),
            daily_request_cap=entry.get("daily_request_cap", 0),
            per_minute_cap=entry.get("per_minute_cap", 0),
        )
        for entry in config.get("TIERS", [])
    ]


def is_enabled() -> bool:
    return bool(getattr(settings, "AI_SETTINGS", {}).get("ENABLED", True))


def timeout_seconds() -> int:
    return int(
        getattr(settings, "AI_SETTINGS", {}).get(
            "TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        )
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _call_gemini(
    tier: Tier, *, prompt: str, system: str, max_tokens: int
) -> ProviderResponse:
    """Google Gemini, ``generateContent``.

    The system instruction is a separate top-level field rather than a message
    role, which is the main shape difference from the OpenAI protocol.
    """
    url = f"{tier.endpoint.rstrip('/')}/{tier.model}:generateContent"

    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            # Low but not zero. These are classification and summarisation
            # tasks where a different answer on the same input is a bug.
            "temperature": 0.2,
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    response = requests.post(
        url,
        params={"key": tier.api_key},
        json=payload,
        timeout=timeout_seconds(),
    )

    if response.status_code != 200:
        return ProviderResponse(
            tier=tier.name,
            reason=_describe_http_error(response),
            rate_limited=response.status_code == 429,
        )

    try:
        body = response.json()
        parts = body["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # A 200 with a shape we do not recognise — a safety block, usually.
        return ProviderResponse(
            tier=tier.name, reason=f"Gemini returned an unreadable response: {exc}"
        )

    return ProviderResponse(ok=bool(text.strip()), text=text, tier=tier.name)


def _call_openai_compatible(
    tier: Tier, *, prompt: str, system: str, max_tokens: int
) -> ProviderResponse:
    """Groq, OpenRouter and Hugging Face all speak this."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {tier.api_key}",
        "Content-Type": "application/json",
    }
    if tier.name == "openrouter":
        # OpenRouter asks callers to identify themselves. Omitting it is not
        # fatal but it is what their free-tier policy expects.
        headers["HTTP-Referer"] = REFERER
        headers["X-Title"] = "Sathify"

    response = requests.post(
        tier.endpoint,
        headers=headers,
        json={
            "model": tier.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
        timeout=timeout_seconds(),
    )

    if response.status_code != 200:
        return ProviderResponse(
            tier=tier.name,
            reason=_describe_http_error(response),
            rate_limited=response.status_code == 429,
        )

    try:
        body = response.json()
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return ProviderResponse(
            tier=tier.name, reason=f"{tier.name} returned an unreadable response: {exc}"
        )

    return ProviderResponse(ok=bool(text.strip()), text=text, tier=tier.name)


def call(
    tier: Tier, *, prompt: str, system: str = "", max_tokens: int = DEFAULT_MAX_TOKENS
) -> ProviderResponse:
    """One attempt against one provider. Never raises."""
    if not tier.is_configured:
        return ProviderResponse(tier=tier.name, reason="No API key configured.")

    started = time.perf_counter()
    try:
        if tier.name == "gemini":
            result = _call_gemini(
                tier, prompt=prompt, system=system, max_tokens=max_tokens
            )
        elif tier.name in OPENAI_COMPATIBLE:
            result = _call_openai_compatible(
                tier, prompt=prompt, system=system, max_tokens=max_tokens
            )
        else:
            result = ProviderResponse(
                tier=tier.name, reason=f"No adapter for provider '{tier.name}'."
            )
    except requests.Timeout:
        result = ProviderResponse(
            tier=tier.name, reason=f"Timed out after {timeout_seconds()}s."
        )
    except requests.RequestException as exc:
        result = ProviderResponse(tier=tier.name, reason=f"Could not reach {tier.name}: {exc}")
    except Exception as exc:  # noqa: BLE001 — a provider bug must not break the chain
        logger.exception("Provider %s raised", tier.name)
        result = ProviderResponse(tier=tier.name, reason=str(exc))

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def _describe_http_error(response) -> str:
    """A short, loggable reason. Never includes the API key.

    Provider error bodies are inconsistent enough that this reads the two shapes
    that cover all four and falls back to the status code, rather than trying to
    parse each provider's schema.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"

    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or ""
    elif isinstance(error, str):
        message = error
    else:
        message = body.get("message", "")

    return f"HTTP {response.status_code}: {message}"[:280] if message else f"HTTP {response.status_code}"


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "OPENAI_COMPATIBLE",
    "ProviderResponse",
    "Tier",
    "call",
    "is_enabled",
    "tiers",
    "timeout_seconds",
]
