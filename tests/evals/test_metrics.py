"""Unit tests for the deterministic eval metrics.

These run as part of the normal ``pytest tests/`` suite (no ``evals``
marker) so the refusal-context logic is covered without burning HF
credits.
"""

from __future__ import annotations

from deepeval.test_case import LLMTestCase

from evals.metrics import FilmSetMembershipMetric, StreamingClaimMetric


def _case(output: str) -> LLMTestCase:
    return LLMTestCase(input="irrelevant", actual_output=output)


class TestFilmSetMembership:
    def test_recommendation_of_bait_film_fails(self):
        metric = FilmSetMembershipMetric(allowed_films=["past lives"], candidate_outside_films=["oppenheimer"])
        metric.measure(_case("You should watch Oppenheimer tonight — it's a masterpiece."))
        assert not metric.success
        assert "oppenheimer" in metric.reason

    def test_refusal_of_bait_film_passes(self):
        metric = FilmSetMembershipMetric(allowed_films=["past lives"], candidate_outside_films=["oppenheimer"])
        metric.measure(
            _case(
                "I can't recommend Oppenheimer — it's not among the films currently showing "
                "at your theaters or available on your listed streaming services."
            )
        )
        assert metric.success, metric.reason

    def test_allowed_film_mention_passes(self):
        metric = FilmSetMembershipMetric(allowed_films=["past lives"], candidate_outside_films=["oppenheimer"])
        metric.measure(_case("Past Lives is a great pick tonight."))
        assert metric.success

    def test_subject_first_refusal_passes(self):
        # The bait film is the *subject* of the refusal ("X is not in your
        # watchlist"), so the refusal marker follows the name. The forward
        # lookahead must recognise this as a refusal, not a recommendation.
        metric = FilmSetMembershipMetric(allowed_films=["past lives"], candidate_outside_films=["oppenheimer"])
        metric.measure(
            _case(
                "Oppenheimer is not in your watchlist or streaming availability. "
                "Would you like me to suggest something from your watchlist instead?"
            )
        )
        assert metric.success, metric.reason

    def test_refusal_then_recommendation_still_fails(self):
        # If the model refuses one bait film but recommends another, we must catch it.
        metric = FilmSetMembershipMetric(allowed_films=["past lives"], candidate_outside_films=["oppenheimer", "barbie"])
        metric.measure(_case("I can't recommend Oppenheimer. But you should watch Barbie!"))
        assert not metric.success
        assert "barbie" in metric.reason


class TestStreamingClaim:
    def test_wrong_provider_recommendation_fails(self):
        metric = StreamingClaimMetric(allowed_pairs=[("past lives", "mubi")], allowed_films=["past lives"])
        metric.measure(_case("Past Lives is streaming on Netflix right now."))
        assert not metric.success

    def test_correct_provider_passes(self):
        metric = StreamingClaimMetric(allowed_pairs=[("past lives", "mubi")], allowed_films=["past lives"])
        metric.measure(_case("Past Lives is streaming on MUBI right now."))
        assert metric.success

    def test_refusal_of_provider_passes(self):
        metric = StreamingClaimMetric(allowed_pairs=[("past lives", "mubi")], allowed_films=["past lives"])
        metric.measure(_case("Past Lives isn't on Netflix — only on MUBI."))
        assert metric.success, metric.reason
