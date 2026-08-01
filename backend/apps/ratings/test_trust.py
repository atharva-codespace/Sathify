"""
Module 9.3 / 9.2 / 9.4 — tests for the pure logic.

Database-free, matching the modules they cover. The trust formula decides
whether someone gets hired, so it gets an executable specification independent
of any view — and the cold-start group in particular pins a property that is
easy to "optimise" away and expensive to get wrong.
"""

from __future__ import annotations

import pytest

from apps.ratings import detection, sentiment
from apps.ratings.trust import (
    NEUTRAL_PRIOR,
    RESIDENT_WEIGHTS,
    TRUST_SCORE_MAX,
    WORKER_WEIGHTS,
    ResidentTrustInputs,
    WorkerTrustInputs,
    resident_trust,
    worker_trust,
)


class TestWeights:
    def test_worker_weights_sum_to_one(self):
        """A drift here silently rescales every trust score in the system."""
        assert sum(WORKER_WEIGHTS.values()) == pytest.approx(1.0)

    def test_resident_weights_sum_to_one(self):
        assert sum(RESIDENT_WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_scale_matches_what_module_4_divides_by(self):
        from apps.hiring.scoring import TRUST_SCORE_MAX as HIRING_MAX

        assert TRUST_SCORE_MAX == HIRING_MAX


class TestWorkerColdStart:
    def test_a_brand_new_worker_is_not_scored_at_zero(self):
        """The property that decides whether new workers ever get hired.

        Scoring an empty history at zero buries them in Module 4's ranking
        permanently — they never get the first job that would produce their
        first rating.
        """
        score = worker_trust(WorkerTrustInputs())

        assert score.value > 0
        assert 25 <= score.value <= 75

    def test_verification_alone_lifts_a_new_worker(self):
        """Passing the checks is real evidence, unlike an empty rating history."""
        unverified = worker_trust(WorkerTrustInputs())
        verified = worker_trust(
            WorkerTrustInputs(is_approved=True, id_verified=True, has_photo=True)
        )

        assert verified.value > unverified.value

    def test_one_bad_rating_does_not_sink_a_worker(self):
        score = worker_trust(WorkerTrustInputs(average_rating=1.0, rating_count=1))
        assert score.value > 20

    def test_a_long_bad_history_does_sink_a_worker(self):
        """Smoothing protects the unknown, not the demonstrably poor."""
        few = worker_trust(WorkerTrustInputs(average_rating=1.0, rating_count=1))
        many = worker_trust(WorkerTrustInputs(average_rating=1.0, rating_count=100))

        assert many.value < few.value


class TestWorkerTrust:
    def test_a_perfect_worker_scores_near_the_maximum(self):
        score = worker_trust(
            WorkerTrustInputs(
                average_rating=5.0,
                rating_count=200,
                expected_visits=200,
                attended_visits=200,
                is_approved=True,
                id_verified=True,
                has_photo=True,
                completed_jobs=200,
                abandoned_jobs=0,
            )
        )
        assert score.value > 95

    def test_the_score_never_leaves_its_range(self):
        for inputs in (
            WorkerTrustInputs(),
            WorkerTrustInputs(average_rating=99, rating_count=999),
            WorkerTrustInputs(expected_visits=1, attended_visits=999),
        ):
            assert 0 <= worker_trust(inputs).value <= TRUST_SCORE_MAX

    def test_missing_verification_costs_exactly_its_weight(self):
        base = dict(
            average_rating=5.0, rating_count=200,
            expected_visits=100, attended_visits=100,
            completed_jobs=100, abandoned_jobs=0,
        )
        verified = worker_trust(
            WorkerTrustInputs(**base, is_approved=True, id_verified=True, has_photo=True)
        )
        unverified = worker_trust(WorkerTrustInputs(**base))

        lost = verified.value - unverified.value
        assert lost == pytest.approx(WORKER_WEIGHTS["verification"] * 100, abs=1.0)

    def test_partial_verification_scores_partially(self):
        one = worker_trust(WorkerTrustInputs(is_approved=True))
        all_three = worker_trust(
            WorkerTrustInputs(is_approved=True, id_verified=True, has_photo=True)
        )

        assert worker_trust(WorkerTrustInputs()).value < one.value < all_three.value

    def test_abandoning_jobs_lowers_the_score(self):
        reliable = worker_trust(
            WorkerTrustInputs(completed_jobs=50, abandoned_jobs=0)
        )
        flaky = worker_trust(WorkerTrustInputs(completed_jobs=25, abandoned_jobs=25))

        assert flaky.value < reliable.value

    def test_poor_attendance_lowers_the_score(self):
        good = worker_trust(WorkerTrustInputs(expected_visits=50, attended_visits=50))
        poor = worker_trust(WorkerTrustInputs(expected_visits=50, attended_visits=10))

        assert poor.value < good.value


class TestExplainability:
    def test_every_component_appears_in_the_breakdown(self):
        breakdown = worker_trust(WorkerTrustInputs()).explain()
        assert {row["key"] for row in breakdown} == set(WORKER_WEIGHTS)

    def test_contributions_sum_to_the_score(self):
        """The explanation must account for the number it explains."""
        score = worker_trust(
            WorkerTrustInputs(
                average_rating=4.2, rating_count=20,
                expected_visits=30, attended_visits=27,
                is_approved=True, has_photo=True,
                completed_jobs=10, abandoned_jobs=2,
            )
        )
        total = sum(row["contribution"] for row in score.explain())
        assert total == pytest.approx(score.value, abs=0.05)

    def test_the_breakdown_is_ordered_by_contribution(self):
        rows = worker_trust(WorkerTrustInputs()).explain()
        contributions = [row["contribution"] for row in rows]
        assert contributions == sorted(contributions, reverse=True)

    def test_every_component_carries_a_readable_detail(self):
        """"attendance: 0.72" explains nothing to a worker asking why."""
        for row in worker_trust(WorkerTrustInputs()).explain():
            assert row["detail"].strip()

    def test_the_weakest_component_is_identified(self):
        """So "how do I improve this?" has an answer."""
        score = worker_trust(
            WorkerTrustInputs(
                average_rating=5.0, rating_count=100,
                expected_visits=100, attended_visits=100,
                completed_jobs=100,
                # Verification is the only thing missing.
            )
        )
        assert score.weakest().key == "verification"

    def test_a_new_worker_is_told_their_history_is_empty_not_bad(self):
        rows = {row["key"]: row for row in worker_trust(WorkerTrustInputs()).explain()}
        assert "No ratings yet" in rows["ratings"]["detail"]


class TestResidentTrust:
    def test_a_new_resident_starts_neutral(self):
        score = resident_trust(ResidentTrustInputs())
        assert score.value == pytest.approx(NEUTRAL_PRIOR * 100, abs=8)

    def test_paying_reliably_raises_the_score(self):
        good = resident_trust(
            ResidentTrustInputs(payments_due=20, payments_settled=20)
        )
        poor = resident_trust(ResidentTrustInputs(payments_due=20, payments_settled=4))

        assert good.value > poor.value

    def test_payment_carries_the_most_weight(self):
        """It is the thing that most directly harms a worker when it goes wrong."""
        assert RESIDENT_WEIGHTS["payment"] == max(RESIDENT_WEIGHTS.values())

    def test_only_upheld_complaints_count(self):
        """A raised complaint is an allegation. Counting it would make the
        complaint button a weapon."""
        alleged = resident_trust(
            ResidentTrustInputs(disputes_against=5, disputes_upheld_against=0)
        )
        upheld = resident_trust(
            ResidentTrustInputs(disputes_against=5, disputes_upheld_against=5)
        )

        assert alleged.value > upheld.value
        assert alleged.value == resident_trust(ResidentTrustInputs()).value

    def test_one_upheld_complaint_is_not_permanent_ruin(self):
        one = resident_trust(ResidentTrustInputs(disputes_upheld_against=1))
        assert one.value > 40

    def test_the_score_never_leaves_its_range(self):
        score = resident_trust(ResidentTrustInputs(disputes_upheld_against=99))
        assert 0 <= score.value <= TRUST_SCORE_MAX


# ---------------------------------------------------------------------------
# 9.2 Sentiment
# ---------------------------------------------------------------------------


class TestSentiment:
    def test_clear_praise_reads_positive(self):
        assert sentiment.analyse("Very good work, always punctual").label == "positive"

    def test_clear_criticism_reads_negative(self):
        assert sentiment.analyse("Always late and careless").label == "negative"

    def test_hinglish_praise_is_understood(self):
        """Reviews arrive in Hindi, Hinglish and English, often mixed."""
        assert sentiment.analyse("kaam bahut accha hai").label == "positive"

    def test_devanagari_criticism_is_understood(self):
        assert sentiment.analyse("काम खराब है").label == "negative"

    def test_a_negator_flips_the_sentiment(self):
        """'not good' must not read as praise."""
        assert sentiment.analyse("not good at all").label == "negative"

    def test_a_trailing_negator_flips_it_too(self):
        """Hinglish puts the negator after the adjective as often as before."""
        assert sentiment.analyse("kaam accha nahi hai").label == "negative"

    def test_unrecognised_text_reports_unknown_rather_than_neutral(self):
        """'No opinion extracted' and 'a neutral opinion' are different."""
        result = sentiment.analyse("xyzzy plugh")
        assert result.label == "unknown"
        assert result.confidence == 0.0

    def test_confidence_stays_low_because_this_is_a_stopgap(self):
        """A keyword pass over mixed script should never sound certain."""
        result = sentiment.analyse("good clean polite punctual honest work")
        assert result.confidence <= sentiment.LEXICON_MAX_CONFIDENCE

    def test_themes_are_only_reported_when_present(self):
        """Reporting every theme on every review turns a profile into noise."""
        result = sentiment.analyse("always late")
        assert "punctuality" in result.themes
        assert "hygiene" not in result.themes

    def test_language_detection_is_coarse_but_honest(self):
        assert sentiment.detect_language("काम अच्छा") == "hi"
        assert sentiment.detect_language("good work") == "en-or-hinglish"
        assert sentiment.detect_language("kaam अच्छा") == "mixed"

    def test_empty_text_analyses_without_raising(self):
        assert sentiment.analyse("").label == "unknown"

    def test_analysis_never_raises(self):
        """A review must be storable even when nothing can be said about it."""
        for text in ("", "   ", "🙂🙂🙂", "a" * 5000):
            sentiment.analyse(text)


# ---------------------------------------------------------------------------
# 9.4 Fake-review detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_a_burst_from_one_rater_is_flagged(self):
        assert detection.check_burst(recent_by_rater=detection.BURST_THRESHOLD) is not None

    def test_ordinary_activity_is_not_flagged(self):
        assert detection.check_burst(recent_by_rater=2) is None

    def test_uniform_ratings_in_a_day_are_flagged(self):
        assert detection.check_uniformity(recent_stars=[5, 5, 5, 5]) is not None

    def test_varied_ratings_are_not_flagged(self):
        """A genuinely good week is not uniform enough to look manufactured."""
        assert detection.check_uniformity(recent_stars=[5, 5, 4, 5]) is None

    def test_too_few_ratings_are_not_flagged_as_uniform(self):
        assert detection.check_uniformity(recent_stars=[5, 5]) is None

    def test_near_identical_text_is_flagged(self):
        suspicion = detection.check_duplicate_text(
            review="very good work always on time",
            recent_reviews=["very good work always on time"],
        )
        assert suspicion is not None

    def test_different_reviews_are_not_flagged(self):
        suspicion = detection.check_duplicate_text(
            review="always punctual and careful",
            recent_reviews=["the kitchen was left dirty"],
        )
        assert suspicion is None

    def test_an_empty_review_is_not_a_duplicate(self):
        """Most reviews are stars only; that is not suspicious."""
        assert detection.check_duplicate_text(review="", recent_reviews=[""]) is None

    def test_similarity_ignores_word_order(self):
        assert detection.similarity("good clean work", "work clean good") == 1.0

    def test_self_rating_is_flagged(self):
        assert detection.check_self_interest(rater_id=7, subject_user_id=7) is not None

    def test_rating_someone_else_is_not(self):
        assert detection.check_self_interest(rater_id=7, subject_user_id=8) is None

    def test_inspect_reports_every_reason_at_once(self):
        """An administrator is better served by all of them than by one at a time."""
        suspicions = detection.inspect(
            review="good work",
            stars=5,
            rater_id=7,
            subject_user_id=7,
            recent_by_rater=10,
            recent_stars=[5, 5, 5, 5],
            recent_reviews=["good work"],
        )
        assert len(suspicions) >= 3

    def test_a_clean_rating_produces_nothing(self):
        suspicions = detection.inspect(
            review="She was thorough with the kitchen today.",
            stars=4,
            rater_id=7,
            subject_user_id=8,
            recent_by_rater=1,
            recent_stars=[4],
            recent_reviews=[],
        )
        assert suspicions == []
