"""
Module 12.5 — review summarisation and complaint classification.

Two jobs the modspec pairs: condense review volume into a short insight, and
sort free-text complaints into the categories Module 11.3 defines.

-------------------------------------------------------------------------------
BOTH HAVE A RULE-BASED FALLBACK THAT ALREADY WORKS
-------------------------------------------------------------------------------
Sentiment falls back to Module 9.2's trilingual lexicon; classification falls
back to a keyword pass over the same category vocabulary. Neither is as good as
a model, and both say so — :attr:`Degraded.engine` records which answered, and
``ReviewSentiment.is_reliable`` still refuses to surface a low-confidence result.

That is not defensive coding for its own sake. A fresh clone has no API keys, a
free tier runs out of them, and these features attach opinions to named people's
livelihoods. "No summary today" is an acceptable outcome; a confident summary
nobody can attribute is not.

-------------------------------------------------------------------------------
THE CLASSIFIER MAY NOT INVENT A CATEGORY
-------------------------------------------------------------------------------
:func:`classify_complaint` validates the model's answer against
``ComplaintCategory`` and falls back to keywords if it does not match. A model
returning "billing" instead of "payment" would otherwise write a category the
database has no choice for, and Module 11's filters would silently stop matching
it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from apps.ratings.sentiment import SentimentResult, analyse_with_lexicon, detect_language

from . import client
from .degradation import Degraded, with_fallback
from .models import AiFeature

logger = logging.getLogger(__name__)

#: Maximum reviews fed to one summary call. Beyond this the prompt gets long
#: enough to matter on a free tier, and the marginal review adds nothing — a
#: summary of forty reviews and a summary of two hundred read the same.
MAX_REVIEWS_PER_SUMMARY = 40

#: Words that point at a complaint category when no model is available. Kept
#: deliberately small: a long list of weak signals misclassifies more confidently
#: than a short list of strong ones.
CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "late_arrival": {
        "late", "absent", "missed", "did not come", "didnt come", "no show",
        "der", "deri", "nahi aaya", "nahi aayi", "देर", "नहीं आया",
    },
    "payment": {
        "payment", "paid", "salary", "money", "wage", "refund", "charged",
        "paisa", "paise", "tankhwah", "vetan", "पैसा", "तनख्वाह", "भुगतान",
    },
    "quality": {
        "dirty", "unclean", "poor work", "not cleaned", "careless", "quality",
        "incomplete", "ganda", "safai", "kaam", "गंदा", "सफाई", "काम",
    },
    "behaviour": {
        "rude", "shouted", "abusive", "disrespect", "behaviour", "behavior",
        "argued", "badtameez", "gussa", "व्यवहार", "बदतमीज़",
    },
    "safety": {
        "unsafe", "theft", "stole", "stolen", "danger", "threat", "harass",
        "gate open", "chori", "khatra", "चोरी", "खतरा", "असुरक्षित",
    },
}


# ---------------------------------------------------------------------------
# 12.5 Sentiment
# ---------------------------------------------------------------------------


SENTIMENT_SYSTEM = (
    "You analyse short reviews of domestic workers written by residents of "
    "Indian housing societies. Reviews mix English, Hindi and Hinglish, often "
    "in one sentence. Return only JSON."
)


def _sentiment_prompt(text: str) -> str:
    return (
        "Analyse this review and return JSON with exactly these keys:\n"
        '  "label": one of "positive", "neutral", "negative"\n'
        '  "polarity": a number from -1.0 to 1.0\n'
        '  "confidence": a number from 0.0 to 1.0\n'
        '  "themes": an object mapping any of "punctuality", "hygiene", '
        '"behaviour", "quality" to "positive", "neutral" or "negative". '
        "Include a theme only if the review actually mentions it.\n\n"
        f"Review: {text}"
    )


def analyse_sentiment(text: str, *, user=None) -> Degraded[SentimentResult]:
    """Module 12.5 — what a review says, with the lexicon behind it.

    Replaces the branch Module 9.2's ``analyse()`` reserved for this module.
    """
    if not text or not text.strip():
        return Degraded(
            value=SentimentResult(engine="lexicon"),
            reason="Nothing to analyse.",
        )

    def ai() -> tuple[SentimentResult | None, str, str]:
        parsed, result = client.complete_json(
            _sentiment_prompt(text),
            feature=AiFeature.SENTIMENT,
            system=SENTIMENT_SYSTEM,
            max_tokens=300,
            user=user,
        )
        if not isinstance(parsed, dict):
            return None, result.tier, result.reason

        label = str(parsed.get("label", "")).lower()
        if label not in {"positive", "neutral", "negative"}:
            return None, result.tier, f"Unusable label from the model: {label!r}"

        return (
            SentimentResult(
                label=label,
                polarity=_clamp(parsed.get("polarity"), -1.0, 1.0),
                confidence=_clamp(parsed.get("confidence"), 0.0, 1.0),
                themes=_clean_themes(parsed.get("themes")),
                language=detect_language(text),
                engine=result.tier,
            ),
            result.tier,
            "",
        )

    return with_fallback(
        AiFeature.SENTIMENT,
        ai=ai,
        fallback=lambda: analyse_with_lexicon(text),
        default=SentimentResult(engine="unavailable"),
        user=user,
    )


def _clamp(value, low: float, high: float) -> float:
    try:
        return round(max(low, min(high, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def _clean_themes(raw) -> dict[str, str]:
    """Keep only the themes Module 9 knows about, with verdicts it understands.

    A model that invents a theme would put an unknown key into the JSON column
    Module 11.4's dashboard aggregates, where it would appear as a real finding.
    """
    if not isinstance(raw, dict):
        return {}

    known_themes = {"punctuality", "hygiene", "behaviour", "quality"}
    known_verdicts = {"positive", "neutral", "negative"}

    return {
        str(theme).lower(): str(verdict).lower()
        for theme, verdict in raw.items()
        if str(theme).lower() in known_themes
        and str(verdict).lower() in known_verdicts
    }


# ---------------------------------------------------------------------------
# 12.5 Review summary
# ---------------------------------------------------------------------------


@dataclass
class ReviewSummary:
    """A short, readable account of many reviews."""

    headline: str = ""
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    review_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.headline or self.strengths or self.concerns)


SUMMARY_SYSTEM = (
    "You summarise reviews of domestic workers for residents of Indian housing "
    "societies who are deciding whether to hire. Be specific and brief. Never "
    "invent detail that is not in the reviews. Return only JSON."
)


def summarise_reviews(
    texts: list[str], *, worker_name: str = "", user=None
) -> Degraded[ReviewSummary]:
    """Condense a worker's reviews into one insight (12.5).

    The fallback is deliberately modest — counts, not prose. Writing a fake
    summary from a keyword pass would put words in reviewers' mouths, and a
    resident reading "generally punctual" needs that to have come from somewhere.
    """
    cleaned = [text.strip() for text in texts if text and text.strip()]
    if not cleaned:
        return Degraded(
            value=ReviewSummary(),
            reason="No written reviews to summarise.",
        )

    sample = cleaned[:MAX_REVIEWS_PER_SUMMARY]

    def ai() -> tuple[ReviewSummary | None, str, str]:
        numbered = "\n".join(f"- {text}" for text in sample)
        parsed, result = client.complete_json(
            "Summarise these reviews and return JSON with exactly these keys:\n"
            '  "headline": one sentence, at most 20 words\n'
            '  "strengths": up to 3 short phrases\n'
            '  "concerns": up to 3 short phrases, empty if there are none\n\n'
            f"Reviews of {worker_name or 'this worker'}:\n{numbered}",
            feature=AiFeature.REVIEW_SUMMARY,
            system=SUMMARY_SYSTEM,
            max_tokens=400,
            user=user,
        )
        if not isinstance(parsed, dict):
            return None, result.tier, result.reason

        return (
            ReviewSummary(
                headline=str(parsed.get("headline", ""))[:200],
                strengths=_phrases(parsed.get("strengths")),
                concerns=_phrases(parsed.get("concerns")),
                review_count=len(cleaned),
            ),
            result.tier,
            "",
        )

    return with_fallback(
        AiFeature.REVIEW_SUMMARY,
        ai=ai,
        fallback=lambda: _summary_from_lexicon(cleaned),
        default=ReviewSummary(),
        user=user,
    )


def _summary_from_lexicon(texts: list[str]) -> ReviewSummary:
    """The offline summary: which themes came up, and how they went.

    Reports counts rather than composing sentences. "Punctuality mentioned in 7
    reviews, mostly positive" is defensible from a keyword pass; "she is
    reliable and always on time" is not.
    """
    tallies: dict[str, dict[str, int]] = {}

    for text in texts:
        for theme, verdict in analyse_with_lexicon(text).themes.items():
            bucket = tallies.setdefault(theme, {"positive": 0, "negative": 0})
            if verdict in bucket:
                bucket[verdict] += 1

    strengths = [
        f"{theme}: positive in {counts['positive']} review(s)"
        for theme, counts in tallies.items()
        if counts["positive"] > counts["negative"]
    ]
    concerns = [
        f"{theme}: raised in {counts['negative']} review(s)"
        for theme, counts in tallies.items()
        if counts["negative"] > 0
    ]

    return ReviewSummary(
        headline=(
            f"{len(texts)} written review(s)."
            if not tallies
            else f"{len(texts)} written review(s), covering {', '.join(sorted(tallies))}."
        ),
        strengths=strengths,
        concerns=concerns,
        review_count=len(texts),
    )


def _phrases(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip()[:120] for item in raw if str(item).strip()][:3]


# ---------------------------------------------------------------------------
# 12.5 Complaint classification
# ---------------------------------------------------------------------------


@dataclass
class ComplaintClassification:
    """Which Module 11 category a free-text complaint belongs to."""

    category: str = "other"
    confidence: float = 0.0
    rationale: str = ""

    @property
    def is_confident(self) -> bool:
        """Whether this is worth acting on without a person looking.

        The threshold matters: Module 11 routes safety complaints to the front
        of the queue, so a wrong confident guess either buries something urgent
        or promotes something trivial past things that are not.
        """
        return self.confidence >= 0.6


CLASSIFY_SYSTEM = (
    "You sort complaints raised in Indian housing societies into fixed "
    "categories. Complaints mix English, Hindi and Hinglish. Return only JSON."
)


def classify_complaint(
    subject: str, description: str, *, user=None
) -> Degraded[ComplaintClassification]:
    """Suggest a category for a complaint (12.5, feeding Module 11.3).

    A *suggestion*. Module 11 keeps whatever category the person chose; this is
    for the case where they chose "other", and for the analytics in 11.4. The
    person who raised the complaint knows what it is about better than a model
    does, and overwriting their choice would be both wrong and infuriating.
    """
    text = f"{subject}\n{description}".strip()
    if not text:
        return Degraded(
            value=ComplaintClassification(), reason="Nothing to classify."
        )

    valid = _valid_categories()

    def ai() -> tuple[ComplaintClassification | None, str, str]:
        parsed, result = client.complete_json(
            "Classify this complaint. Return JSON with exactly these keys:\n"
            f'  "category": one of {sorted(valid)}\n'
            '  "confidence": a number from 0.0 to 1.0\n'
            '  "rationale": at most 15 words\n\n'
            f"Complaint: {text}",
            feature=AiFeature.COMPLAINT_CLASSIFY,
            system=CLASSIFY_SYSTEM,
            max_tokens=200,
            user=user,
        )
        if not isinstance(parsed, dict):
            return None, result.tier, result.reason

        category = str(parsed.get("category", "")).lower().strip()
        if category not in valid:
            # A category the database has no choice for would silently drop out
            # of every filter in Module 11. Better to use the keyword pass.
            return None, result.tier, f"Model returned an unknown category: {category!r}"

        return (
            ComplaintClassification(
                category=category,
                confidence=_clamp(parsed.get("confidence"), 0.0, 1.0),
                rationale=str(parsed.get("rationale", ""))[:120],
            ),
            result.tier,
            "",
        )

    return with_fallback(
        AiFeature.COMPLAINT_CLASSIFY,
        ai=ai,
        fallback=lambda: classify_with_keywords(text),
        default=ComplaintClassification(),
        user=user,
    )


def classify_with_keywords(text: str) -> ComplaintClassification:
    """The offline classifier. Never raises, never sounds certain.

    Scores each category by how many of its keywords appear and takes the best.
    Confidence is capped well below :attr:`ComplaintClassification.is_confident`
    unless the signal is strong, so a keyword hit cannot promote something to
    the front of the queue on its own.
    """
    lowered = text.lower()

    scores = {
        category: sum(1 for keyword in keywords if keyword in lowered)
        for category, keywords in CATEGORY_KEYWORDS.items()
    }
    best = max(scores, key=lambda category: scores[category])

    if scores[best] == 0:
        return ComplaintClassification(
            category="other",
            confidence=0.0,
            rationale="No recognisable keywords.",
        )

    # Two independent hits is the point at which this stops being a coincidence.
    confidence = 0.4 if scores[best] == 1 else 0.65
    return ComplaintClassification(
        category=best,
        confidence=confidence,
        rationale=f"Matched {scores[best]} keyword(s) for {best}.",
    )


def _valid_categories() -> set[str]:
    """The categories Module 11 actually accepts.

    Read from Module 11 rather than duplicated, so adding a category there does
    not silently leave this classifier unable to produce it.
    """
    from apps.administration.models import ComplaintCategory

    return set(ComplaintCategory.values)


__all__ = [
    "CATEGORY_KEYWORDS",
    "ComplaintClassification",
    "MAX_REVIEWS_PER_SUMMARY",
    "ReviewSummary",
    "analyse_sentiment",
    "classify_complaint",
    "classify_with_keywords",
    "summarise_reviews",
]
