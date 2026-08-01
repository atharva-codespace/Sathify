"""
Module 12.2 — the conversational interface.

Natural-language lookups for availability, bookings, payments and schedule, as
the modspec asks for.

-------------------------------------------------------------------------------
THE MODEL PICKS THE QUESTION. THE DATABASE ANSWERS IT.
-------------------------------------------------------------------------------
This is the single most important property in this file, and it is worth being
blunt about why.

An LLM asked "how much did I pay Sunita last month" will happily produce a
number. It will be a plausible number. It will not be *the* number. A platform
that told a resident they had paid a worker ₹6,000 when they had paid ₹4,500 —
or told a worker they had been paid when they had not — would be worse than
having no chatbot at all, and the person harmed would be the one with the least
recourse.

So the model is used for exactly one thing: turning a sentence into an intent
and a few parameters. Every figure in every answer is read from the database by
:func:`_answer_for`, using the same querysets the corresponding screen uses.
The model never sees the data and never composes the answer.

That also makes the fallback cheap: if no provider is available, a keyword pass
picks the intent instead, and the answer is identical because the answer never
depended on the model.

-------------------------------------------------------------------------------
IT ONLY EVER ANSWERS ABOUT THE PERSON ASKING
-------------------------------------------------------------------------------
Every lookup is scoped to ``user``. There is no intent that takes a name and
returns somebody else's payments, and the resident/worker split is the same one
Module 8's ledger enforces. A chatbot is a new front door to existing data, and
a new front door with a different lock is how tenancy leaks happen.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from django.utils import timezone

from . import client
from .degradation import Degraded, with_fallback
from .models import AiFeature

logger = logging.getLogger(__name__)

#: How far back the "recent" lookups reach.
LOOKBACK_DAYS = 30

#: How far forward the schedule lookup reaches.
LOOKAHEAD_DAYS = 7


class Intent:
    """What the user is asking about. Deliberately a small, closed set.

    Every intent maps to a query this platform already runs on a screen. An open
    set would mean an intent with no lookup behind it, and the only thing to do
    with one of those is have the model answer — which is exactly what this
    module refuses to do.
    """

    SCHEDULE = "schedule"
    PAYMENTS = "payments"
    BOOKINGS = "bookings"
    AVAILABILITY = "availability"
    COMPLAINTS = "complaints"
    HELP = "help"
    UNKNOWN = "unknown"

    ALL = [SCHEDULE, PAYMENTS, BOOKINGS, AVAILABILITY, COMPLAINTS, HELP]


#: Keywords for the offline intent pass, per intent. English plus the Hinglish
#: and Hindi forms these are actually typed in.
INTENT_KEYWORDS: dict[str, set[str]] = {
    Intent.SCHEDULE: {
        "schedule", "today", "tomorrow", "coming", "visit", "when", "timing",
        "kab", "aaj", "kal", "samay", "कब", "आज", "कल",
    },
    Intent.PAYMENTS: {
        "pay", "paid", "payment", "salary", "money", "earning", "receipt",
        "paisa", "paise", "tankhwah", "vetan", "पैसा", "भुगतान", "तनख्वाह",
    },
    Intent.BOOKINGS: {
        "booking", "booked", "book", "one day", "one-day", "service",
        "booking karna", "बुकिंग",
    },
    Intent.AVAILABILITY: {
        "available", "availability", "free", "khali", "available hun",
        "उपलब्ध", "खाली",
    },
    Intent.COMPLAINTS: {
        "complaint", "complain", "issue", "problem", "shikayat", "शिकायत",
    },
    Intent.HELP: {"help", "what can you", "how do i", "madad", "मदद"},
}


@dataclass
class ChatAnswer:
    """One reply.

    ``facts`` is the structured form of the same answer, so the app can render
    a list of payments as rows rather than re-parsing the sentence.
    """

    intent: str = Intent.UNKNOWN
    text: str = ""
    facts: list[dict] = field(default_factory=list)

    #: Where the intent came from — "ai" or "keywords". Not where the *data*
    #: came from: the data is always the database.
    intent_source: str = "keywords"

    #: What the user could usefully ask next. Especially useful on the unknown
    #: path, where the honest answer is "I did not understand".
    suggestions: list[str] = field(default_factory=list)


INTENT_SYSTEM = (
    "You classify questions from users of a domestic-worker management app used "
    "in Indian housing societies. Questions mix English, Hindi and Hinglish. "
    "You never answer the question. You only name its topic. Return only JSON."
)


def classify_intent(question: str, *, user=None) -> Degraded[str]:
    """Decide what the question is about (12.2).

    Returns an intent string, never an answer. The AI path and the keyword path
    produce values from the same closed set, so the rest of this module cannot
    tell which one ran — and the answer is identical either way.
    """
    if not question or not question.strip():
        return Degraded(value=Intent.UNKNOWN, reason="Empty question.")

    def ai() -> tuple[str | None, str, str]:
        parsed, result = client.complete_json(
            "Which topic is this question about? Return JSON with exactly one "
            f'key, "intent", whose value is one of {Intent.ALL} — or "unknown" '
            "if it fits none of them.\n\n"
            f"Question: {question}",
            feature=AiFeature.CHAT,
            system=INTENT_SYSTEM,
            max_tokens=100,
            user=user,
        )
        if not isinstance(parsed, dict):
            return None, result.tier, result.reason

        intent = str(parsed.get("intent", "")).lower().strip()
        if intent not in Intent.ALL + [Intent.UNKNOWN]:
            return None, result.tier, f"Unknown intent from the model: {intent!r}"
        return intent, result.tier, ""

    return with_fallback(
        AiFeature.CHAT,
        ai=ai,
        fallback=lambda: intent_from_keywords(question),
        default=Intent.UNKNOWN,
        user=user,
    )


def intent_from_keywords(question: str) -> str:
    """The offline intent pass.

    Scores each intent by keyword hits. Ties go to the earlier intent in
    :attr:`Intent.ALL`, which puts schedule ahead of payments — the more common
    question, and the less harmful one to get wrong.
    """
    lowered = question.lower()

    best = Intent.UNKNOWN
    best_score = 0
    for intent in Intent.ALL:
        score = sum(1 for keyword in INTENT_KEYWORDS[intent] if keyword in lowered)
        if score > best_score:
            best, best_score = intent, score

    return best


def answer(user, question: str) -> ChatAnswer:
    """Answer one question. Never raises.

    The only public entry point. Classifies, then looks the answer up.
    """
    classified = classify_intent(question, user=user)
    intent = classified.value or Intent.UNKNOWN

    try:
        reply = _answer_for(user, intent)
    except Exception:  # noqa: BLE001 — a lookup bug must not 500 a chat turn
        logger.exception("Chat lookup failed for intent %s", intent)
        reply = ChatAnswer(
            intent=intent,
            text="Something went wrong looking that up. Please try the screen "
            "for it instead.",
        )

    reply.intent_source = "ai" if classified.from_ai else "keywords"
    if not reply.suggestions:
        reply.suggestions = _suggestions_for(user)
    return reply


# ---------------------------------------------------------------------------
# Lookups. Every figure below comes from the database.
# ---------------------------------------------------------------------------


def _answer_for(user, intent: str) -> ChatAnswer:
    if intent == Intent.SCHEDULE:
        return _schedule_answer(user)
    if intent == Intent.PAYMENTS:
        return _payments_answer(user)
    if intent == Intent.BOOKINGS:
        return _bookings_answer(user)
    if intent == Intent.AVAILABILITY:
        return _availability_answer(user)
    if intent == Intent.COMPLAINTS:
        return _complaints_answer(user)
    if intent == Intent.HELP:
        return ChatAnswer(intent=Intent.HELP, text=_help_text())

    # Deliberately not a guess. "I did not understand" plus what it *can* do is
    # more useful than a confident answer to a question nobody asked.
    return ChatAnswer(
        intent=Intent.UNKNOWN,
        text="I did not understand that. Here is what I can look up:",
    )


def _schedule_answer(user) -> ChatAnswer:
    """Module 6's derived calendar, read through the chatbot."""
    from apps.scheduling.schedule import resident_schedule, worker_schedule

    today = timezone.localdate()
    horizon = today + dt.timedelta(days=LOOKAHEAD_DAYS)

    if getattr(user, "is_worker", False):
        profile = getattr(user, "worker_profile", None)
        if profile is None:
            return ChatAnswer(
                intent=Intent.SCHEDULE,
                text="Your worker profile is not set up yet, so there is "
                "nothing scheduled.",
            )
        items = worker_schedule(profile.pk, today, horizon)
    else:
        profile = getattr(user, "resident_profile", None)
        if profile is None:
            return ChatAnswer(
                intent=Intent.SCHEDULE,
                text="You have not claimed a flat yet, so nothing is scheduled.",
            )
        items = resident_schedule(profile.pk, today, horizon)

    facts = [
        {
            "date": item.date.isoformat(),
            "start_time": item.start_time.strftime("%H:%M"),
            "title": item.title,
            "worker_name": item.worker_name,
        }
        for item in items[:20]
    ]

    today_count = sum(1 for item in items if item.date == today)
    if not facts:
        return ChatAnswer(
            intent=Intent.SCHEDULE,
            text=f"Nothing is scheduled in the next {LOOKAHEAD_DAYS} days.",
        )

    return ChatAnswer(
        intent=Intent.SCHEDULE,
        text=(
            f"{today_count} visit(s) today, and {len(items)} in the next "
            f"{LOOKAHEAD_DAYS} days."
        ),
        facts=facts,
    )


def _payments_answer(user) -> ChatAnswer:
    """Module 8's ledger, scoped exactly as the ledger screen scopes it."""
    from apps.payments.models import Payment, format_paise

    since = timezone.localdate() - dt.timedelta(days=LOOKBACK_DAYS)

    if getattr(user, "is_worker", False):
        profile = getattr(user, "worker_profile", None)
        queryset = Payment.objects.filter(worker=profile) if profile else Payment.objects.none()
        subject = "received"
    else:
        profile = getattr(user, "resident_profile", None)
        queryset = (
            Payment.objects.filter(resident=profile) if profile else Payment.objects.none()
        )
        subject = "paid"

    settled = queryset.settled().filter(paid_at__date__gte=since).order_by("-paid_at")
    total = sum(payment.net_paise for payment in settled)

    facts = [
        {
            "receipt_number": payment.receipt_number,
            "date": payment.paid_at.date().isoformat() if payment.paid_at else "",
            "amount": format_paise(payment.net_paise),
            "kind": payment.get_kind_display(),
        }
        for payment in settled[:10]
    ]

    if not facts:
        return ChatAnswer(
            intent=Intent.PAYMENTS,
            text=f"Nothing has been {subject} in the last {LOOKBACK_DAYS} days.",
        )

    return ChatAnswer(
        intent=Intent.PAYMENTS,
        text=(
            f"{format_paise(total)} {subject} across {len(facts)} payment(s) in "
            f"the last {LOOKBACK_DAYS} days."
        ),
        facts=facts,
    )


def _bookings_answer(user) -> ChatAnswer:
    """Module 5's one-day bookings."""
    from apps.bookings.models import Booking

    if getattr(user, "is_worker", False):
        profile = getattr(user, "worker_profile", None)
        queryset = Booking.objects.filter(worker=profile) if profile else Booking.objects.none()
    else:
        profile = getattr(user, "resident_profile", None)
        queryset = (
            Booking.objects.filter(resident=profile) if profile else Booking.objects.none()
        )

    upcoming = queryset.filter(
        scheduled_date__gte=timezone.localdate()
    ).select_related("category").order_by("scheduled_date", "start_time")[:10]

    facts = [
        {
            "date": booking.scheduled_date.isoformat(),
            "start_time": booking.start_time.strftime("%H:%M"),
            "category": booking.category.name,
            "status": booking.get_status_display(),
        }
        for booking in upcoming
    ]

    if not facts:
        return ChatAnswer(
            intent=Intent.BOOKINGS, text="You have no upcoming one-day bookings."
        )

    return ChatAnswer(
        intent=Intent.BOOKINGS,
        text=f"{len(facts)} upcoming booking(s).",
        facts=facts,
    )


def _availability_answer(user) -> ChatAnswer:
    """Module 5.3's per-date availability. Workers only — a resident asking
    about availability is asking about a *specific* worker, which is a search,
    not a chat lookup."""
    if not getattr(user, "is_worker", False):
        return ChatAnswer(
            intent=Intent.AVAILABILITY,
            text="Search for a worker to see the days they are free.",
        )

    from apps.bookings.models import DayAvailability

    profile = getattr(user, "worker_profile", None)
    if profile is None:
        return ChatAnswer(
            intent=Intent.AVAILABILITY,
            text="Your worker profile is not set up yet.",
        )

    today = timezone.localdate()
    rows = DayAvailability.objects.filter(
        worker=profile, date__gte=today, date__lte=today + dt.timedelta(days=LOOKAHEAD_DAYS)
    ).order_by("date")

    facts = [
        {
            "date": row.date.isoformat(),
            "available": row.is_available,
            "note": row.note,
        }
        for row in rows
    ]

    open_days = sum(1 for row in facts if row["available"])
    return ChatAnswer(
        intent=Intent.AVAILABILITY,
        text=(
            f"You are open on {open_days} of the next {LOOKAHEAD_DAYS} days."
            if facts
            else "You have not marked any days yet, so your usual hours apply."
        ),
        facts=facts,
    )


def _complaints_answer(user) -> ChatAnswer:
    """Module 11.3, scoped the same way the complaints screen scopes it."""
    from apps.administration.models import Complaint

    mine = Complaint.objects.filter(raised_by=user).order_by("-created_at")[:10]

    facts = [
        {
            "reference": complaint.reference,
            "subject": complaint.subject,
            "status": complaint.get_status_display(),
            "is_open": complaint.is_open,
        }
        for complaint in mine
    ]

    if not facts:
        return ChatAnswer(
            intent=Intent.COMPLAINTS,
            text="You have not raised any complaints.",
        )

    open_count = sum(1 for fact in facts if fact["is_open"])
    return ChatAnswer(
        intent=Intent.COMPLAINTS,
        text=f"{open_count} of your {len(facts)} complaint(s) are still open.",
        facts=facts,
    )


def _help_text() -> str:
    return (
        "I can look up your schedule, your payments, your one-day bookings, "
        "your availability and your complaints. I only ever read your own "
        "records."
    )


def _suggestions_for(user) -> list[str]:
    if getattr(user, "is_worker", False):
        return [
            "What is my schedule this week?",
            "Have I been paid this month?",
            "Which days am I free?",
        ]
    return [
        "Who is coming today?",
        "How much have I paid this month?",
        "Do I have any bookings coming up?",
    ]


__all__ = [
    "ChatAnswer",
    "INTENT_KEYWORDS",
    "Intent",
    "answer",
    "classify_intent",
    "intent_from_keywords",
]
