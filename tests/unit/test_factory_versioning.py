"""Build numbers: the rule both stores enforce and neither forgives."""

from __future__ import annotations

import datetime as dt

import pytest

from tools.factory.versioning import (
    MAX_BUILD_NUMBER,
    BuildNumberError,
    Version,
    parse_strategy,
    resolve_build_number,
)


class TestStrategyParsing:
    @pytest.mark.parametrize("name", ["ci-run", "timestamp", "explicit"])
    def test_accepts_every_implemented_strategy(self, name: str) -> None:
        assert parse_strategy(name) == name

    def test_an_unknown_strategy_lists_the_real_ones(self) -> None:
        with pytest.raises(BuildNumberError, match="ci-run, timestamp, explicit"):
            parse_strategy("latest")


class TestCiRun:
    def test_uses_the_github_run_number(self) -> None:
        assert resolve_build_number("ci-run", env={"GITHUB_RUN_NUMBER": "42"}) == 42

    def test_falls_back_through_other_ci_variables(self) -> None:
        assert resolve_build_number("ci-run", env={"BUILD_NUMBER": "7"}) == 7

    def test_prefers_github_when_several_are_set(self) -> None:
        env = {"GITHUB_RUN_NUMBER": "42", "BUILD_NUMBER": "7"}
        assert resolve_build_number("ci-run", env=env) == 42

    def test_outside_ci_it_says_what_to_use_instead(self) -> None:
        with pytest.raises(BuildNumberError, match="timestamp"):
            resolve_build_number("ci-run", env={})

    def test_a_non_numeric_run_number_is_an_error(self) -> None:
        with pytest.raises(BuildNumberError, match="not an integer"):
            resolve_build_number("ci-run", env={"GITHUB_RUN_NUMBER": "abc"})

    def test_ignores_a_blank_variable(self) -> None:
        env = {"GITHUB_RUN_NUMBER": "  ", "BUILD_NUMBER": "9"}
        assert resolve_build_number("ci-run", env=env) == 9


class TestTimestamp:
    def test_is_minutes_since_the_epoch(self) -> None:
        moment = dt.datetime(2020, 1, 1, 1, 0, tzinfo=dt.UTC)
        assert resolve_build_number("timestamp", env={}, now=moment) == 60

    def test_increases_with_time(self) -> None:
        early = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        later = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
        assert resolve_build_number("timestamp", env={}, now=early) < resolve_build_number(
            "timestamp", env={}, now=later
        )

    def test_stays_inside_plays_ceiling_far_into_the_future(self) -> None:
        far = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)
        assert resolve_build_number("timestamp", env={}, now=far) < MAX_BUILD_NUMBER


class TestExplicit:
    def test_uses_the_number_given(self) -> None:
        assert resolve_build_number("explicit", explicit=1234, env={}) == 1234

    def test_without_a_number_it_says_which_flag_to_pass(self) -> None:
        with pytest.raises(BuildNumberError, match="--build-number"):
            resolve_build_number("explicit", env={})


class TestMonotonicity:
    """The one rule that costs a whole run to discover at upload time."""

    def test_accepts_a_number_above_the_last_upload(self) -> None:
        assert resolve_build_number("explicit", explicit=11, previous=10, env={}) == 11

    def test_rejects_a_repeated_build_number(self) -> None:
        with pytest.raises(BuildNumberError, match="not greater"):
            resolve_build_number("explicit", explicit=10, previous=10, env={})

    def test_rejects_going_backwards(self) -> None:
        with pytest.raises(BuildNumberError, match="not greater"):
            resolve_build_number("explicit", explicit=9, previous=10, env={})

    def test_no_previous_upload_means_no_constraint(self) -> None:
        assert resolve_build_number("explicit", explicit=1, env={}) == 1


class TestRange:
    def test_rejects_zero(self) -> None:
        with pytest.raises(BuildNumberError, match="positive"):
            resolve_build_number("explicit", explicit=0, env={})

    def test_rejects_a_number_play_would_refuse(self) -> None:
        with pytest.raises(BuildNumberError, match="ceiling"):
            resolve_build_number("explicit", explicit=MAX_BUILD_NUMBER + 1, env={})


class TestVersion:
    def test_round_trips(self) -> None:
        assert str(Version.parse("1.2.3")) == "1.2.3"

    def test_orders_numerically_not_lexically(self) -> None:
        assert Version.parse("1.10.0") > Version.parse("1.9.0")

    def test_rejects_a_malformed_version(self) -> None:
        with pytest.raises(BuildNumberError, match=r"MAJOR\.MINOR\.PATCH"):
            Version.parse("1.0")
