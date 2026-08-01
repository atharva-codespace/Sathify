"""
Module 12 — AI Layer: tests.

Two groups carry most of the weight.

``TestChatbotNeverInventsData`` pins the property this module is built around:
the model classifies the question, the database answers it. A chatbot that told
a resident they had paid ₹6,000 when they had paid ₹4,500 would be worse than no
chatbot, and the person harmed would be the one with the least recourse.

``TestFallbacks`` pins that every AI feature works with no provider configured —
which is the state of a fresh clone, and the state of a free tier that has run
out of quota. If these pass with no keys set, they pass in the deployment this
project actually has.

No test here contacts a provider. The chain is exercised by pointing it at
tiers that cannot possibly answer, which is exactly the path production spends
most of its time on.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.ai_services import analysis, chatbot, client, degradation, providers
from apps.ai_services.models import (
    AiFeature,
    AiOutcomeKind,
    AiRequestLog,
    AiUsageCounter,
    UsageWindow,
)
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def worker(worker_user):
    profile = WorkerProfile.objects.create(
        user=worker_user, photo="workers/photos/test.jpg"
    )
    profile.service_types.add(ServiceType.objects.create(name="Maid", slug="maid"))
    return profile


@pytest.fixture
def no_providers(settings):
    """The normal state of a fresh clone: the layer is on, no keys are set."""
    settings.AI_SETTINGS = {
        "ENABLED": True,
        "TIMEOUT_SECONDS": 5,
        "TIERS": [
            {"name": "gemini", "api_key": "", "model": "m", "endpoint": "https://e"},
        ],
    }
    return settings


# ---------------------------------------------------------------------------
# 12.6 The degradation convention
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_the_ai_path_wins_when_it_answers(self):
        result = degradation.with_fallback(
            AiFeature.SENTIMENT,
            ai=lambda: ("from-ai", "gemini", ""),
            fallback=lambda: "from-rules",
        )

        assert result.value == "from-ai"
        assert result.from_ai is True
        assert result.engine == "gemini"

    def test_a_declining_ai_path_falls_back(self):
        result = degradation.with_fallback(
            AiFeature.SENTIMENT,
            ai=lambda: (None, "", "no key"),
            fallback=lambda: "from-rules",
        )

        assert result.value == "from-rules"
        assert result.from_ai is False
        assert result.engine == "fallback"
        assert result.is_available is True

    def test_a_raising_ai_path_falls_back_rather_than_propagating(self):
        """An AI bug must never break the thing that called it."""

        def explode():
            raise RuntimeError("provider SDK went wrong")

        result = degradation.with_fallback(
            AiFeature.SENTIMENT, ai=explode, fallback=lambda: "from-rules"
        )

        assert result.value == "from-rules"
        assert "provider SDK went wrong" in result.reason

    def test_both_paths_failing_is_reported_rather_than_raised(self):
        def explode():
            raise RuntimeError("nope")

        result = degradation.with_fallback(
            AiFeature.SENTIMENT, ai=explode, fallback=explode, default="nothing"
        )

        assert result.value == "nothing"
        assert result.is_available is False
        assert result.engine == "unavailable"

    def test_the_fallback_path_is_logged_but_the_ai_path_is_not(self):
        """The client layer logs its own attempts.

        Logging the AI success here as well would double-count the exact
        statistic the log exists to measure — how often the chain falls through.
        """
        degradation.with_fallback(
            AiFeature.SENTIMENT,
            ai=lambda: ("answered", "gemini", ""),
            fallback=lambda: "unused",
        )
        assert AiRequestLog.objects.count() == 0

        degradation.with_fallback(
            AiFeature.SENTIMENT,
            ai=lambda: (None, "", "declined"),
            fallback=lambda: "used",
        )
        assert AiRequestLog.objects.filter(outcome=AiOutcomeKind.FALLBACK).count() == 1


# ---------------------------------------------------------------------------
# The four-tier chain
# ---------------------------------------------------------------------------


class TestProviderChain:
    def test_no_configured_tier_is_reported_not_raised(self, no_providers):
        result = client.complete("anything", feature=AiFeature.CHAT)

        assert result.ok is False
        assert "No AI provider" in result.reason

    def test_a_switched_off_layer_short_circuits(self, settings):
        settings.AI_SETTINGS = {"ENABLED": False, "TIERS": []}
        result = client.complete("anything")

        assert result.ok is False
        assert "switched off" in result.reason

    def test_tiers_are_tried_in_order_until_one_answers(self, settings, monkeypatch):
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIMEOUT_SECONDS": 5,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"},
                {"name": "groq", "api_key": "k", "model": "m", "endpoint": "https://e"},
                {"name": "openrouter", "api_key": "k", "model": "m", "endpoint": "https://e"},
            ],
        }

        def fake_call(tier, **kwargs):
            if tier.name == "groq":
                return providers.ProviderResponse(ok=True, text="hello", tier=tier.name)
            return providers.ProviderResponse(tier=tier.name, reason="declined")

        monkeypatch.setattr(providers, "call", fake_call)

        result = client.complete("q", feature=AiFeature.CHAT)

        assert result.ok is True
        assert result.tier == "groq"
        # Ordered, not load-balanced: Tier 1 is always tried first, and Tier 3
        # is never reached once Tier 2 answers.
        assert result.tiers_attempted == ["gemini", "groq"]
        assert result.fell_through is True

    def test_the_chain_is_logged_even_when_nothing_answers(self, settings, monkeypatch):
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIMEOUT_SECONDS": 5,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"},
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                tier=tier.name, reason="down"
            ),
        )

        client.complete("q", feature=AiFeature.CHAT)

        log = AiRequestLog.objects.get()
        assert log.outcome == AiOutcomeKind.UNAVAILABLE
        assert log.tiers_attempted == ["gemini"]

    def test_the_log_records_length_but_never_content(self, settings, monkeypatch):
        """A chat question is "how much did I pay Sunita".

        Storing it would create a second, unaudited copy of data the rest of the
        platform is careful about.
        """
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"}
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True, text="an answer", tier=tier.name
            ),
        )

        client.complete("a secret question", feature=AiFeature.CHAT)

        log = AiRequestLog.objects.get()
        assert log.prompt_chars == len("a secret question")
        assert log.response_chars == len("an answer")
        for field in (log.error, str(log)):
            assert "secret" not in field


class TestRateLimiting:
    def test_a_tier_at_its_daily_cap_is_skipped_without_being_called(
        self, settings, monkeypatch
    ):
        """Asking OpenRouter for a 51st request is a wasted round trip.

        Enforcing locally is what makes Tier 3 fail over to Tier 4 cleanly
        instead of returning a provider error.
        """
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {
                    "name": "openrouter",
                    "api_key": "k",
                    "model": "m",
                    "endpoint": "https://e",
                    "daily_request_cap": 2,
                },
                {"name": "huggingface", "api_key": "k", "model": "m", "endpoint": "https://e"},
            ],
        }

        called: list[str] = []

        def fake_call(tier, **kwargs):
            called.append(tier.name)
            return providers.ProviderResponse(ok=True, text="ok", tier=tier.name)

        monkeypatch.setattr(providers, "call", fake_call)

        AiUsageCounter.objects.create(
            tier="openrouter",
            window=UsageWindow.DAY,
            bucket=AiUsageCounter.bucket_for(UsageWindow.DAY),
            count=2,
        )

        result = client.complete("q", feature=AiFeature.CHAT)

        assert result.tier == "huggingface"
        assert "openrouter" not in called

    def test_quota_is_returned_when_the_provider_never_answered(
        self, settings, monkeypatch
    ):
        """A provider outage must not eat the day's allowance."""
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {
                    "name": "openrouter",
                    "api_key": "k",
                    "model": "m",
                    "endpoint": "https://e",
                    "daily_request_cap": 50,
                }
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                tier=tier.name, reason="unreachable"
            ),
        )

        client.complete("q", feature=AiFeature.CHAT)

        assert AiUsageCounter.used("openrouter", window=UsageWindow.DAY) == 0

    def test_a_successful_call_does_spend_quota(self, settings, monkeypatch):
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {
                    "name": "openrouter",
                    "api_key": "k",
                    "model": "m",
                    "endpoint": "https://e",
                    "daily_request_cap": 50,
                }
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True, text="ok", tier=tier.name
            ),
        )

        client.complete("q", feature=AiFeature.CHAT)

        assert AiUsageCounter.used("openrouter", window=UsageWindow.DAY) == 1

    def test_the_cap_holds_across_calls(self):
        for _ in range(3):
            AiUsageCounter.reserve("t", window=UsageWindow.DAY, cap=3)

        assert AiUsageCounter.reserve("t", window=UsageWindow.DAY, cap=3) is False

    def test_pruning_drops_only_stale_buckets(self):
        old = (timezone.localdate() - dt.timedelta(days=30)).isoformat()
        AiUsageCounter.objects.create(tier="t", window=UsageWindow.DAY, bucket=old)
        AiUsageCounter.reserve("t", window=UsageWindow.DAY, cap=5)

        assert AiUsageCounter.prune(keep_days=7) == 1
        assert AiUsageCounter.objects.count() == 1


class TestJsonParsing:
    def test_plain_json_parses(self):
        assert client.parse_json('{"a": 1}') == {"a": 1}

    def test_fenced_json_parses(self):
        """Models add ```json despite being told not to. Every tier does it."""
        assert client.parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_after_a_sentence_of_prose_parses(self):
        # Tier 4 does this routinely.
        text = 'Sure! Here is the result:\n{"a": 1}'
        assert client.parse_json(text) == {"a": 1}

    def test_unparseable_text_returns_none_rather_than_raising(self):
        assert client.parse_json("I would rather not.") is None
        assert client.parse_json("") is None


# ---------------------------------------------------------------------------
# 12.5 Analysis
# ---------------------------------------------------------------------------


class TestAnalysisFallbacks:
    def test_sentiment_falls_back_to_the_lexicon(self, no_providers):
        result = analysis.analyse_sentiment("kaam bahut accha hai")

        assert result.from_ai is False
        assert result.value.label == "positive"
        # The engine is recorded so a lexicon guess is never mistaken for a
        # model's finding.
        assert result.value.engine == "lexicon"

    def test_the_lexicon_never_claims_high_confidence(self, no_providers):
        result = analysis.analyse_sentiment("accha")
        assert result.value.confidence <= 0.65

    def test_a_review_summary_falls_back_to_counts_not_prose(self, no_providers):
        """Composing a sentence from a keyword pass would put words in
        reviewers' mouths."""
        result = analysis.summarise_reviews(
            ["always on time and very clean", "kaam accha, hamesha samay par"]
        )

        assert result.from_ai is False
        assert "2 written review(s)" in result.value.headline

    def test_no_reviews_produces_an_empty_summary_rather_than_a_call(self):
        result = analysis.summarise_reviews([])
        assert result.value.is_empty is True

    def test_complaint_classification_falls_back_to_keywords(self, no_providers):
        result = analysis.classify_complaint(
            "Salary not paid", "Two reminders sent about the payment, no reply."
        )

        assert result.from_ai is False
        assert result.value.category == "payment"

    def test_the_keyword_classifier_admits_when_it_has_nothing(self):
        result = analysis.classify_with_keywords("something happened yesterday")

        assert result.category == "other"
        assert result.confidence == 0.0
        assert result.is_confident is False

    def test_a_single_keyword_hit_is_not_confident_enough_to_act_on(self):
        """Module 11 routes safety complaints to the front of the queue.

        A lone keyword must not be able to promote something past things that
        are genuinely urgent.
        """
        result = analysis.classify_with_keywords("there was a theft")

        assert result.category == "safety"
        assert result.is_confident is False

    def test_a_model_inventing_a_category_is_rejected(self, settings, monkeypatch):
        """A category the database has no choice for would silently drop out of
        every filter in Module 11."""
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"}
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True,
                text='{"category": "billing", "confidence": 0.99}',
                tier=tier.name,
            ),
        )

        result = analysis.classify_complaint("x", "the payment never arrived")

        assert result.from_ai is False
        assert result.value.category == "payment"  # from the keyword fallback

    def test_a_model_inventing_a_theme_has_it_dropped(self, settings, monkeypatch):
        """An unknown theme would appear as a real finding in Module 11.4."""
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"}
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True,
                text=(
                    '{"label": "positive", "polarity": 0.8, "confidence": 0.9, '
                    '"themes": {"punctuality": "positive", "vibes": "positive"}}'
                ),
                tier=tier.name,
            ),
        )

        result = analysis.analyse_sentiment("on time, good work")

        assert result.from_ai is True
        assert result.value.themes == {"punctuality": "positive"}

    def test_out_of_range_numbers_from_a_model_are_clamped(self, settings, monkeypatch):
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"}
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True,
                text='{"label": "positive", "polarity": 7, "confidence": 4}',
                tier=tier.name,
            ),
        )

        result = analysis.analyse_sentiment("good")

        assert result.value.polarity == 1.0
        assert result.value.confidence == 1.0


class TestModule9Integration:
    def test_module_9_now_routes_through_module_12(self, no_providers):
        """Module 9.2 reserved a branch for this and now uses it."""
        from apps.ratings.sentiment import analyse

        result = analyse("bahut accha kaam")

        assert result.label == "positive"
        assert result.engine == "lexicon"

    def test_an_empty_review_still_returns_a_result(self):
        from apps.ratings.sentiment import analyse

        assert analyse("").label == "unknown"


# ---------------------------------------------------------------------------
# 12.2 Chatbot
# ---------------------------------------------------------------------------


class TestChatbotIntent:
    def test_keywords_classify_without_any_provider(self, no_providers):
        assert chatbot.intent_from_keywords("who is coming today") == chatbot.Intent.SCHEDULE
        assert chatbot.intent_from_keywords("have I been paid") == chatbot.Intent.PAYMENTS
        assert (
            chatbot.intent_from_keywords("koi shikayat hai")
            == chatbot.Intent.COMPLAINTS
        )

    def test_an_unrecognisable_question_is_admitted_not_guessed(self, no_providers):
        assert chatbot.intent_from_keywords("purple monkey dishwasher") == (
            chatbot.Intent.UNKNOWN
        )

    def test_a_model_inventing_an_intent_is_rejected(self, settings, monkeypatch):
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"}
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True, text='{"intent": "launch_rockets"}', tier=tier.name
            ),
        )

        result = chatbot.classify_intent("who is coming today")

        assert result.from_ai is False
        assert result.value == chatbot.Intent.SCHEDULE  # keyword fallback


class TestChatbotNeverInventsData:
    """The property this module is built around.

    A model that produced a plausible payment figure would be worse than no
    chatbot at all, and the person harmed would be the one with the least
    recourse. So the model picks the question; the database answers it.
    """

    def test_payment_figures_come_from_the_ledger(
        self, no_providers, resident, worker, society, resident_user
    ):
        from apps.payments.models import Payment, PaymentKind, PaymentStatus

        payment = Payment.objects.create(
            society=society,
            resident=resident,
            worker=worker,
            kind=PaymentKind.BOOKING,
            amount_paise=450000,
            status=PaymentStatus.PAID,
            paid_at=timezone.now(),
        )

        reply = chatbot.answer(resident_user, "how much have I paid this month")

        assert reply.intent == chatbot.Intent.PAYMENTS
        # The exact ledger figure, not a plausible one.
        assert "4,500" in reply.text
        assert reply.facts[0]["receipt_number"] == payment.receipt_number

    def test_a_user_with_no_payments_is_told_so_rather_than_given_a_number(
        self, no_providers, resident, resident_user
    ):
        reply = chatbot.answer(resident_user, "how much have I paid")

        assert reply.facts == []
        assert "Nothing has been paid" in reply.text

    def test_it_only_reads_the_caller_s_own_records(
        self, no_providers, resident, worker, society, resident_user, worker_user
    ):
        """A chatbot is a new front door to existing data.

        A new front door with a different lock is how tenancy leaks happen.
        """
        from apps.payments.models import Payment, PaymentKind, PaymentStatus

        Payment.objects.create(
            society=society,
            resident=resident,
            worker=worker,
            kind=PaymentKind.BOOKING,
            amount_paise=450000,
            status=PaymentStatus.PAID,
            paid_at=timezone.now(),
        )

        # The worker on the other side of that payment sees it as received.
        worker_reply = chatbot.answer(worker_user, "have I been paid")
        assert "received" in worker_reply.text
        assert worker_reply.facts

        # The resident sees the same payment as paid, from their own side.
        resident_reply = chatbot.answer(resident_user, "have I paid anything")
        assert "paid" in resident_reply.text

    def test_an_unknown_question_says_so_and_offers_what_it_can_do(
        self, no_providers, resident, resident_user
    ):
        reply = chatbot.answer(resident_user, "what is the meaning of life")

        assert reply.intent == chatbot.Intent.UNKNOWN
        assert "did not understand" in reply.text
        assert reply.suggestions  # never a dead end

    def test_a_lookup_crash_is_answered_not_raised(
        self, no_providers, resident_user, monkeypatch
    ):
        def explode(user):
            raise RuntimeError("query blew up")

        monkeypatch.setattr(chatbot, "_payments_answer", explode)

        reply = chatbot.answer(resident_user, "have I been paid")

        assert "Something went wrong" in reply.text


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestApi:
    def test_status_reports_capability_never_keys(
        self, authenticated_client, resident_user, no_providers
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:ai_services:status")
        )

        assert response.status_code == 200
        body = response.json()
        assert body["providers_configured"] == []
        # Chat works with no provider: the keyword pass and the database lookups
        # behind it need nothing external.
        assert body["chat_available"] is True
        assert body["recommendation_engine"] == "rule_based_v1"
        assert "api_key" not in response.content.decode()

    def test_chat_answers_over_the_api(
        self, authenticated_client, resident_user, resident, no_providers
    ):
        response = authenticated_client(resident_user).post(
            reverse("v1:ai_services:chat"),
            {"question": "who is coming today"},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["intent"] == chatbot.Intent.SCHEDULE
        assert response.json()["intent_source"] == "keywords"

    def test_an_overlong_question_is_refused(
        self, authenticated_client, resident_user, no_providers
    ):
        """A 4,000-character question is a paste accident or a probe, and either
        way it is not worth a call from a metered free tier."""
        response = authenticated_client(resident_user).post(
            reverse("v1:ai_services:chat"),
            {"question": "x" * 5000},
            format="json",
        )

        assert response.status_code == 400

    def test_classification_is_offered_as_a_suggestion(
        self, authenticated_client, resident_user, no_providers
    ):
        response = authenticated_client(resident_user).post(
            reverse("v1:ai_services:classify-complaint"),
            {"subject": "Salary", "description": "The payment never arrived."},
            format="json",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["category"] == "payment"
        assert body["engine"] == "fallback"

    def test_a_review_summary_excludes_withheld_reviews(
        self, authenticated_client, resident_user, resident, worker, society, no_providers
    ):
        """A review withheld pending an administrator's decision must not leak.

        Summarising it would surface exactly the thing withholding it was meant
        to suppress — and Module 9 withholds precisely the reviews suspected of
        being unfair to the worker being summarised.
        """
        from apps.hiring.models import Engagement
        from apps.ratings.models import Rating, RatingDirection

        def rating_on(engagement, *, review: str, withheld: bool) -> None:
            Rating.objects.create(
                society=society,
                direction=RatingDirection.RESIDENT_TO_WORKER,
                worker=worker,
                resident=resident,
                rater=resident_user,
                engagement=engagement,
                stars=5,
                review=review,
                is_withheld=withheld,
            )

        # One engagement per (resident, worker, service type) is a database
        # constraint, so the two ratings hang off two different services.
        services = [
            worker.service_types.first(),
            ServiceType.objects.create(name="Cook", slug="cook"),
        ]

        for index, withheld in enumerate([False, True]):
            engagement = Engagement.objects.create(
                society=society,
                resident=resident,
                worker=worker,
                service_type=services[index],
                days_of_week=[index],
                start_time=dt.time(9, 0),
                monthly_rate=4000,
            )
            rating_on(
                engagement,
                review="withheld text" if withheld else "visible and good",
                withheld=withheld,
            )

        response = authenticated_client(resident_user).get(
            reverse("v1:ai_services:review-summary", args=[worker.pk])
        )

        assert response.status_code == 200
        assert response.json()["review_count"] == 1

    def test_a_worker_from_another_society_is_not_found(
        self, authenticated_client, resident_user, no_providers, django_user_model
    ):
        from apps.accounts.models import Role
        from apps.societies.models import Society, SocietyStatus

        other = Society.objects.create(
            name="Blue Ridge",
            address_line="Elsewhere",
            city="Mumbai",
            state="Maharashtra",
            pincode="400001",
            total_towers=1,
            total_flats=10,
            status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9899999999",
            password="test-pass-12345",
            role=Role.WORKER,
            society=other,
            is_approved=True,
        )
        other_worker = WorkerProfile.objects.create(user=outsider)

        response = authenticated_client(resident_user).get(
            reverse("v1:ai_services:review-summary", args=[other_worker.pk])
        )

        assert response.status_code == 404

    def test_chat_needs_an_approved_account(
        self, api_client, no_providers
    ):
        response = api_client.post(
            reverse("v1:ai_services:chat"), {"question": "hello"}, format="json"
        )

        assert response.status_code in {401, 403}


# ---------------------------------------------------------------------------
# 12.1 / 12.4 Service boundaries
# ---------------------------------------------------------------------------


class TestServiceBoundaries:
    def test_the_recommendation_service_is_the_hiring_formula(self):
        """A seam, not a reimplementation.

        Two copies of the formula would diverge the first time someone tuned a
        weight, so this asserts they are literally the same computation.
        """
        from apps.ai_services import recommendation
        from apps.hiring.scoring import ScoringInputs, score

        inputs = ScoringInputs(trust_score=70, average_rating=4.2, rating_count=8)

        assert recommendation.score_inputs(inputs).total == score(inputs).total
        assert recommendation.engine_name() == "rule_based_v1"

    def test_hiring_still_scores_through_the_new_seam(self, worker):
        from apps.hiring.services import score_worker

        result = score_worker(worker)

        assert 0.0 <= result.total <= 1.0
        assert result.components

    def test_face_verification_never_produces_a_denial(self, settings):
        """The model gets a vote, not a veto.

        Face recognition is measurably less accurate for darker skin tones,
        older cameras and poor lighting — which describes the gate, the phone
        and the workforce this platform is for.
        """
        from apps.ai_services.face_service import verify

        settings.FACE_SETTINGS = {"ENABLED": False}
        check = verify("/nonexistent/live.jpg", "/nonexistent/ref.jpg")

        assert check.available is False
        assert check.verified is False
        assert check.requires_human_decision is True
        assert check.outcome == "unavailable"

    def test_ocr_reports_unavailability_rather_than_raising(self):
        """"No engine installed" is a supported state, not a crash.

        The CV stack does not fit on a 512 MB instance, so this is the
        production path, not an edge case.
        """
        from apps.ai_services.ocr_service import extract

        result = extract(b"not an image", filename="x.jpg")

        assert result.available is False
        assert result.needs_manual_entry is True

    def test_an_unreadable_document_never_implies_the_worker_is_an_adult(self):
        """Module 3.4's age block is not something silence can satisfy."""
        from apps.ai_services.ocr_service import ExtractionResult

        assert ExtractionResult().is_minor is False
        assert ExtractionResult().needs_manual_entry is True


# ---------------------------------------------------------------------------
# Module 11 integration
# ---------------------------------------------------------------------------


class TestModule11Integration:
    def test_a_disagreeing_classifier_leaves_an_internal_note(
        self, society, resident_user, settings, monkeypatch
    ):
        from apps.administration.models import ComplaintCategory
        from apps.administration.services import raise_complaint

        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"}
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True,
                text='{"category": "safety", "confidence": 0.95, "rationale": "mentions a threat"}',
                tier=tier.name,
            ),
        )

        complaint = raise_complaint(
            raised_by=resident_user,
            society=society,
            category=ComplaintCategory.OTHER,
            subject="Something odd",
            description="Someone threatened the guard at the gate.",
        )

        notes = complaint.updates.filter(is_system=True, is_internal=True)
        assert notes.count() == 1
        assert "safety" in notes.first().note

    def test_the_category_the_person_chose_is_never_overwritten(
        self, society, resident_user, settings, monkeypatch
    ):
        """They know what their complaint is about better than a model does."""
        from apps.administration.models import ComplaintCategory
        from apps.administration.services import raise_complaint

        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIERS": [
                {"name": "gemini", "api_key": "k", "model": "m", "endpoint": "https://e"}
            ],
        }
        monkeypatch.setattr(
            providers,
            "call",
            lambda tier, **kwargs: providers.ProviderResponse(
                ok=True,
                text='{"category": "quality", "confidence": 0.99, "rationale": "x"}',
                tier=tier.name,
            ),
        )

        complaint = raise_complaint(
            raised_by=resident_user,
            society=society,
            category=ComplaintCategory.SAFETY,
            subject="Gate left open",
            description="Found the service gate unlocked overnight.",
        )

        assert complaint.category == ComplaintCategory.SAFETY
        # And the priority that came with it, which a silent reclassification
        # would have taken away.
        assert complaint.priority == "urgent"

    def test_an_agreeing_classifier_adds_no_noise(
        self, society, resident_user, no_providers
    ):
        from apps.administration.models import ComplaintCategory
        from apps.administration.services import raise_complaint

        complaint = raise_complaint(
            raised_by=resident_user,
            society=society,
            category=ComplaintCategory.PAYMENT,
            subject="Salary",
            description="The payment never arrived.",
        )

        assert complaint.updates.filter(is_system=True).count() == 0
