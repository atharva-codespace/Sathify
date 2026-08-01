"""
Module 9.2 — sentiment analysis on review text.

-------------------------------------------------------------------------------
THE REAL MODEL IS MODULE 12'S JOB. THIS IS AN HONEST STOPGAP.
-------------------------------------------------------------------------------
The modspec calls for Gemini or a multilingual sentiment model, because reviews
arrive in Hindi, Hinglish and English mixed together — often within one
sentence. Module 12 owns that call and its four-tier fallback, and is not built
yet.

So this file provides the same interface with a small trilingual lexicon behind
it, and is explicit about being a stopgap: it reports a **low confidence**, and
``ReviewSentiment.is_reliable`` refuses to surface a result below the threshold.
A weak guess presented as a finding is worse than no finding, particularly when
the finding is attached to someone's livelihood.

When Module 12 lands, :func:`analyse` gains a Gemini branch and the ``engine``
field on each stored row keeps old results attributable to the thing that
actually produced them.

-------------------------------------------------------------------------------
WHY A LEXICON AND NOT NOTHING
-------------------------------------------------------------------------------
Doing nothing until Module 12 would leave 9.3's theme breakdown empty and give
the review screen nothing to show. A keyword pass over a short review is crude
but not useless — "kaam accha hai" and "hamesha late" are unambiguous — and it
exercises the storage, the API shape and the UI so that swapping the engine is a
one-function change rather than a new feature.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Confidence a keyword pass may claim. Kept below
#: ``ReviewSentiment.is_reliable``'s threshold on purpose when evidence is thin.
LEXICON_MAX_CONFIDENCE = 0.65

#: Positive terms across English, Hindi (transliterated) and Devanagari.
#: Transliteration is how these actually get typed on a phone keyboard.
POSITIVE_TERMS = {
    "good", "great", "excellent", "polite", "punctual", "clean", "honest",
    "hardworking", "reliable", "friendly", "respectful", "neat", "careful",
    "accha", "acha", "achha", "badhiya", "bahut", "shandar", "imandaar",
    "mehnati", "saaf", "safai", "samay", "vinamra",
    "अच्छा", "बढ़िया", "ईमानदार", "मेहनती", "साफ", "समय",
}

NEGATIVE_TERMS = {
    "bad", "late", "rude", "dirty", "lazy", "careless", "unreliable", "slow",
    "absent", "missing", "poor", "worst", "complaint", "broke", "damaged",
    "kharab", "bura", "ganda", "gandagi", "der", "deri", "aalsi", "nahi",
    "nahin", "bekar", "galat",
    "खराब", "बुरा", "गंदा", "देर", "आलसी", "बेकार", "गलत",
}

#: Negators. "not good" must not count as positive, and Hinglish reviews put
#: the negator after the adjective as often as before it.
NEGATORS = {"not", "no", "never", "nahi", "nahin", "na", "नहीं", "ना"}

#: Themes the modspec names, with the words that signal each.
THEME_TERMS: dict[str, set[str]] = {
    "punctuality": {
        "late", "punctual", "time", "early", "der", "deri", "samay", "waqt",
        "देर", "समय",
    },
    "hygiene": {
        "clean", "dirty", "hygiene", "neat", "saaf", "safai", "ganda", "gandagi",
        "साफ", "गंदा",
    },
    "behaviour": {
        "polite", "rude", "friendly", "respectful", "behaviour", "behavior",
        "vyavhar", "vinamra", "badtameez", "व्यवहार", "विनम्र",
    },
    "quality": {
        "work", "quality", "thorough", "careless", "kaam", "badhiya", "accha",
        "काम", "गुणवत्ता",
    },
}

#: Devanagari block. Its presence is a strong signal, unlike Latin script which
#: covers English and Hinglish equally.
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


@dataclass(frozen=True)
class SentimentResult:
    """What an engine made of one review."""

    label: str = "unknown"
    polarity: float = 0.0
    confidence: float = 0.0
    themes: dict[str, str] = field(default_factory=dict)
    language: str = ""
    engine: str = ""


def detect_language(text: str) -> str:
    """A coarse guess: Devanagari, Latin, or a mix of both.

    Deliberately coarse. Distinguishing Hinglish from English by script is
    impossible — both are Latin — so this reports what can actually be seen
    rather than inventing a confident answer.
    """
    if not text.strip():
        return ""

    has_devanagari = bool(_DEVANAGARI.search(text))
    has_latin = bool(re.search(r"[A-Za-z]", text))

    if has_devanagari and has_latin:
        return "mixed"
    if has_devanagari:
        return "hi"
    if has_latin:
        return "en-or-hinglish"
    return ""


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN.findall(text)]


def _score_tokens(tokens: list[str]) -> tuple[int, int]:
    """Count positive and negative hits, honouring nearby negators."""
    positive = negative = 0

    for index, token in enumerate(tokens):
        # A negator within two tokens either side flips the term. Hinglish puts
        # them on both sides ("accha nahi" as readily as "not accha"), so the
        # window looks both ways.
        window = tokens[max(0, index - 2) : index] + tokens[index + 1 : index + 3]
        negated = any(word in NEGATORS for word in window)

        if token in POSITIVE_TERMS:
            negative += 1 if negated else 0
            positive += 0 if negated else 1
        elif token in NEGATIVE_TERMS:
            positive += 1 if negated else 0
            negative += 0 if negated else 1

    return positive, negative


def _themes(tokens: list[str], polarity: float) -> dict[str, str]:
    """Which themes the review touched, and how it felt about them.

    A theme is only reported when its words actually appear. Reporting every
    theme on every review would turn a profile into noise.
    """
    verdict = "positive" if polarity > 0.15 else "negative" if polarity < -0.15 else "neutral"
    present = set(tokens)

    return {
        theme: verdict
        for theme, terms in THEME_TERMS.items()
        if present & terms
    }


def analyse_with_lexicon(text: str) -> SentimentResult:
    """The built-in stopgap. Never raises, and never claims high confidence."""
    tokens = _tokens(text)
    if not tokens:
        return SentimentResult(engine="lexicon", language=detect_language(text))

    positive, negative = _score_tokens(tokens)
    hits = positive + negative

    if hits == 0:
        # Nothing recognised. That is "no opinion extracted", not "neutral" —
        # and the caller can tell the difference by the confidence.
        return SentimentResult(
            label="unknown",
            confidence=0.0,
            language=detect_language(text),
            engine="lexicon",
        )

    polarity = (positive - negative) / hits
    label = (
        "positive" if polarity > 0.15 else "negative" if polarity < -0.15 else "neutral"
    )

    # Confidence grows with evidence but is capped: a keyword pass over mixed
    # Hindi and English should never sound certain.
    confidence = min(LEXICON_MAX_CONFIDENCE, 0.35 + 0.1 * hits)

    return SentimentResult(
        label=label,
        polarity=round(polarity, 3),
        confidence=round(confidence, 3),
        themes=_themes(tokens, polarity),
        language=detect_language(text),
        engine="lexicon",
    )


def analyse(text: str) -> SentimentResult:
    """Analyse one review.

    The single entry point. Module 12.5 now sits in front of the lexicon: it
    tries the four-tier provider chain and falls back to
    :func:`analyse_with_lexicon` when no provider is configured, every tier
    declines, or the answer comes back unusable. That fallback is not optional
    — Module 12.6's wrapper takes it as a required argument.

    ``SentimentResult.engine`` records which path answered, so a row produced by
    Gemini in March stays attributable after the provider is swapped.

    Never raises: a review must be storable even when nothing can be said about
    it, because the star rating is the part that actually matters.
    """
    if not text or not text.strip():
        return SentimentResult(engine="lexicon")

    try:
        # Imported here rather than at module scope: Module 12 imports this
        # module for its own fallback, and a top-level import either way round
        # would be a cycle.
        from apps.ai_services.analysis import analyse_sentiment

        return analyse_sentiment(text).value
    except Exception:  # noqa: BLE001 — analysis must never block a review
        logger.exception("Sentiment analysis failed")

    try:
        return analyse_with_lexicon(text)
    except Exception:  # noqa: BLE001
        logger.exception("Lexicon fallback failed")
        return SentimentResult(engine="lexicon")


__all__ = [
    "LEXICON_MAX_CONFIDENCE",
    "NEGATIVE_TERMS",
    "POSITIVE_TERMS",
    "SentimentResult",
    "THEME_TERMS",
    "analyse",
    "analyse_with_lexicon",
    "detect_language",
]
