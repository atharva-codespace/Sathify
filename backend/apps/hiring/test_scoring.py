"""
Module 4.3 — tests for the recommendation formula.

These are deliberately database-free, matching ``scoring.py`` itself. The point
of that separation is that the ranking logic can be reasoned about and pinned
down without fixtures, so that when Module 12.1 swaps in a learned model there is
an executable specification of what the rule-based version actually promised.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.hiring.scoring import (
    RATING_PRIOR,
    RESPONSE_RATE_PRIOR,
    TRUST_SCORE_MAX,
    WEIGHTS,
    ScoringInputs,
    availability_component,
    haversine_km,
    proximity_component,
    rating_component,
    response_rate_component,
    score,
    trust_component,
)


class TestWeights:
    def test_weights_sum_to_one(self):
        """Guards the percentage: if these drift, every match score rescales."""
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_every_weight_is_positive(self):
        assert all(w > 0 for w in WEIGHTS.values())


class TestTrustComponent:
    def test_zero_trust_scores_zero(self):
        assert trust_component(0) == 0.0

    def test_none_is_treated_as_zero(self):
        assert trust_component(None) == 0.0

    def test_max_trust_scores_one(self):
        assert trust_component(TRUST_SCORE_MAX) == pytest.approx(1.0)

    def test_is_linear_in_between(self):
        assert trust_component(50) == pytest.approx(0.5)

    def test_out_of_range_is_clamped(self):
        """Module 9 owns this number; a bad value must not produce a >100% match."""
        assert trust_component(500) == 1.0


class TestRatingComponent:
    def test_unrated_worker_sits_at_the_prior(self):
        """The cold-start guarantee: no ratings means neutral, not zero."""
        assert rating_component(0, 0) == pytest.approx(RATING_PRIOR / 5.0)

    def test_a_single_bad_rating_does_not_sink_a_worker(self):
        """One angry review should move the needle, not end a livelihood."""
        harsh = rating_component(1.0, 1)
        assert harsh < RATING_PRIOR / 5.0
        assert harsh > 0.4

    def test_many_ratings_converge_on_the_observed_average(self):
        assert rating_component(4.8, 200) == pytest.approx(4.8 / 5.0, abs=0.01)

    def test_more_ratings_means_less_shrinkage(self):
        few = rating_component(5.0, 1)
        many = rating_component(5.0, 50)
        assert many > few


class TestResponseRateComponent:
    def test_no_history_sits_at_the_prior(self):
        assert response_rate_component(0, 0) == pytest.approx(RESPONSE_RATE_PRIOR)

    def test_perfect_responder_with_history_beats_the_prior(self):
        assert response_rate_component(20, 0) > RESPONSE_RATE_PRIOR

    def test_ignoring_requests_lowers_the_score(self):
        assert response_rate_component(1, 9) < RESPONSE_RATE_PRIOR

    def test_result_stays_within_range(self):
        assert 0.0 <= response_rate_component(0, 500) <= 1.0
        assert 0.0 <= response_rate_component(500, 0) <= 1.0


class TestAvailabilityComponent:
    MORNING = (dt.time(8, 0), dt.time(12, 0))

    def test_worker_covering_the_whole_window_scores_one(self):
        assert availability_component(
            dt.time(7, 0), dt.time(18, 0), *self.MORNING
        ) == pytest.approx(1.0)

    def test_half_coverage_scores_half(self):
        assert availability_component(
            dt.time(10, 0), dt.time(18, 0), *self.MORNING
        ) == pytest.approx(0.5)

    def test_no_overlap_scores_zero(self):
        assert availability_component(dt.time(14, 0), dt.time(18, 0), *self.MORNING) == 0.0

    def test_worker_without_declared_hours_is_not_penalised(self):
        """A blank optional field is not a declaration of unavailability."""
        assert availability_component(None, None, *self.MORNING) == 1.0

    def test_no_requested_window_scores_neutral(self):
        assert availability_component(dt.time(8, 0), dt.time(9, 0)) == 1.0

    def test_inverted_request_window_carries_no_information(self):
        assert availability_component(
            dt.time(8, 0), dt.time(18, 0), dt.time(12, 0), dt.time(8, 0)
        ) == 1.0


class TestProximityComponent:
    def test_unmeasurable_distance_is_neutral_not_zero(self):
        """A society with no coordinates must not have its workers buried."""
        assert proximity_component(None) == 1.0

    def test_zero_distance_scores_one(self):
        assert proximity_component(0.0) == 1.0

    def test_decays_with_distance(self):
        assert proximity_component(5.0) == pytest.approx(0.5)

    def test_beyond_the_horizon_is_clamped_to_zero(self):
        assert proximity_component(500.0) == 0.0

    def test_haversine_matches_a_known_distance(self):
        """Pune to Mumbai is about 120 km."""
        assert haversine_km(18.5204, 73.8567, 19.0760, 72.8777) == pytest.approx(120, abs=15)


class TestScore:
    def test_perfect_worker_scores_one_hundred(self):
        result = score(
            ScoringInputs(
                trust_score=100,
                average_rating=5.0,
                rating_count=500,
                answered_requests=500,
                ignored_requests=0,
            )
        )
        assert result.percentage == 100

    def test_worst_worker_floors_at_the_uninformative_components(self):
        """A worker who is bad at everything *measured* still keeps the neutrals.

        With no requested window and no measurable distance, availability and
        proximity are both "no information", which scores neutral rather than
        zero by design. So the floor is their combined weight — roughly 30% —
        not 0%. This is the intended behaviour, and pinning it here keeps anyone
        from later "fixing" a neutral default into a punitive zero.
        """
        result = score(
            ScoringInputs(
                trust_score=0,
                average_rating=0.0,
                rating_count=500,
                answered_requests=0,
                ignored_requests=500,
                distance_km=None,
            )
        )
        assert 0 <= result.percentage <= 100
        assert result.total == pytest.approx(
            WEIGHTS["proximity"] + WEIGHTS["availability"], abs=0.02
        )

    def test_a_missed_availability_window_pushes_below_the_floor(self):
        """Once the resident *does* state a window, availability can be lost."""
        common = dict(
            trust_score=0, average_rating=0.0, rating_count=500, ignored_requests=500
        )
        neutral = score(ScoringInputs(**common))
        mismatched = score(
            ScoringInputs(
                **common,
                worker_available_from=dt.time(14, 0),
                worker_available_until=dt.time(18, 0),
                requested_from=dt.time(8, 0),
                requested_until=dt.time(12, 0),
            )
        )
        assert mismatched.total < neutral.total
        assert mismatched.total == pytest.approx(WEIGHTS["proximity"], abs=0.02)

    def test_brand_new_worker_is_ranked_plausibly(self):
        """The cold-start case that decides whether new workers ever get hired."""
        result = score(ScoringInputs())
        assert 30 <= result.percentage <= 75

    def test_percentage_is_always_in_range(self):
        result = score(ScoringInputs(trust_score=10_000, average_rating=99))
        assert 0 <= result.percentage <= 100

    def test_is_pure(self):
        """Same inputs, same answer — the property Module 12.1's swap relies on."""
        inputs = ScoringInputs(trust_score=61, average_rating=4.2, rating_count=9)
        assert score(inputs).total == score(inputs).total

    def test_trust_outranks_response_rate(self):
        """Ordering the weights encode: what a resident asked about wins."""
        trusted = score(ScoringInputs(trust_score=100, answered_requests=0, ignored_requests=10))
        responsive = score(ScoringInputs(trust_score=0, answered_requests=10))
        assert trusted.total > responsive.total


class TestExplainability:
    def test_breakdown_covers_every_component(self):
        result = score(ScoringInputs(trust_score=80, average_rating=4.5, rating_count=10))
        assert {row["key"] for row in result.explain()} == set(WEIGHTS)

    def test_breakdown_is_ordered_by_contribution(self):
        contributions = [row["contribution"] for row in score(ScoringInputs()).explain()]
        assert contributions == sorted(contributions, reverse=True)

    def test_contributions_sum_to_the_total(self):
        """The explanation must actually account for the number it explains."""
        result = score(ScoringInputs(trust_score=70, average_rating=4.0, rating_count=12))
        assert sum(r["contribution"] for r in result.explain()) == pytest.approx(
            result.total, abs=0.001
        )

    def test_response_rate_raw_is_none_without_history(self):
        """Never present the prior as if it were observed history."""
        rows = {r["key"]: r for r in score(ScoringInputs()).explain()}
        assert rows["response_rate"]["raw"] is None

    def test_response_rate_raw_reports_the_observed_rate(self):
        rows = {
            r["key"]: r
            for r in score(
                ScoringInputs(answered_requests=3, ignored_requests=1)
            ).explain()
        }
        assert rows["response_rate"]["raw"] == pytest.approx(0.75)
