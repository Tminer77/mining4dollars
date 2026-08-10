"""Domain rules for events."""

from __future__ import annotations

import datetime as dt

import pytest

from m4d.domain.errors import ValidationError
from m4d.domain.events import EventFilter, EventSeverity, NewEvent

NOW = dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.UTC)


class TestSeverity:
    def test_ranks_ascend_with_urgency(self) -> None:
        assert (
            EventSeverity.DEBUG.rank
            < EventSeverity.INFO.rank
            < EventSeverity.WARNING.rank
            < EventSeverity.ERROR.rank
            < EventSeverity.CRITICAL.rank
        )

    def test_at_or_above_is_inclusive(self) -> None:
        assert EventSeverity.at_or_above(EventSeverity.WARNING) == (
            EventSeverity.WARNING,
            EventSeverity.ERROR,
            EventSeverity.CRITICAL,
        )

    def test_at_or_above_debug_returns_everything(self) -> None:
        assert len(EventSeverity.at_or_above(EventSeverity.DEBUG)) == len(EventSeverity)

    @pytest.mark.parametrize(
        ("severity", "expected"),
        [
            (EventSeverity.DEBUG, False),
            (EventSeverity.INFO, False),
            (EventSeverity.WARNING, False),
            (EventSeverity.ERROR, True),
            (EventSeverity.CRITICAL, True),
        ],
    )
    def test_actionability(self, severity: EventSeverity, expected: bool) -> None:
        assert severity.is_actionable is expected


class TestNewEventValidation:
    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_rejects_a_blank_source(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="source"):
            NewEvent(source=blank, kind="a.b")

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_rejects_a_blank_kind(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="kind"):
            NewEvent(source="api", kind=blank)

    def test_trims_surrounding_whitespace(self) -> None:
        event = NewEvent(source="  api  ", kind="  a.b  ")
        assert event.source == "api"
        assert event.kind == "a.b"

    def test_rejects_an_oversized_source(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            NewEvent(source="x" * 129, kind="a.b")

    def test_rejects_a_naive_occurred_at(self) -> None:
        """Naive timestamps are the classic source of off-by-hours bugs."""
        with pytest.raises(ValidationError, match="timezone"):
            NewEvent(source="api", kind="a.b", occurred_at=dt.datetime(2026, 8, 10, 12, 0))

    def test_normalises_occurred_at_to_utc(self) -> None:
        offset = dt.timezone(dt.timedelta(hours=5))
        event = NewEvent(
            source="api", kind="a.b", occurred_at=dt.datetime(2026, 8, 10, 17, 0, tzinfo=offset)
        )
        assert event.occurred_at == dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.UTC)

    def test_rejects_a_blank_idempotency_key(self) -> None:
        with pytest.raises(ValidationError, match="idempotency_key"):
            NewEvent(source="api", kind="a.b", idempotency_key="   ")


class TestMaterialise:
    def test_assigns_identity_and_recording_time(self) -> None:
        stored = NewEvent(source="api", kind="a.b").materialise(now=NOW)
        assert stored.recorded_at == NOW
        assert stored.id is not None

    def test_defaults_occurred_at_to_now(self) -> None:
        stored = NewEvent(source="api", kind="a.b").materialise(now=NOW)
        assert stored.occurred_at == NOW
        assert stored.ingest_lag == dt.timedelta(0)

    def test_preserves_a_supplied_occurred_at(self) -> None:
        earlier = NOW - dt.timedelta(minutes=5)
        stored = NewEvent(source="api", kind="a.b", occurred_at=earlier).materialise(now=NOW)
        assert stored.occurred_at == earlier
        assert stored.ingest_lag == dt.timedelta(minutes=5)

    def test_ids_are_unique_per_materialisation(self) -> None:
        request = NewEvent(source="api", kind="a.b")
        assert request.materialise(now=NOW).id != request.materialise(now=NOW).id

    def test_payload_is_copied_not_aliased(self) -> None:
        """A later mutation of the caller's dict must not alter a stored event."""
        payload = {"attempt": 1}
        stored = NewEvent(source="api", kind="a.b", payload=payload).materialise(now=NOW)
        payload["attempt"] = 2
        assert stored.payload == {"attempt": 1}


class TestEventFilter:
    def test_defaults_constrain_nothing(self) -> None:
        assert EventFilter().is_empty is True

    def test_any_criterion_makes_it_non_empty(self) -> None:
        assert EventFilter(source="api").is_empty is False

    def test_rejects_an_inverted_window(self) -> None:
        with pytest.raises(ValidationError, match="strictly before"):
            EventFilter(occurred_after=NOW, occurred_before=NOW - dt.timedelta(hours=1))

    def test_rejects_a_zero_width_window(self) -> None:
        """A window that can never match is a client bug worth surfacing."""
        with pytest.raises(ValidationError, match="strictly before"):
            EventFilter(occurred_after=NOW, occurred_before=NOW)

    def test_accepts_a_valid_window(self) -> None:
        filters = EventFilter(occurred_after=NOW - dt.timedelta(hours=1), occurred_before=NOW)
        assert filters.occurred_before == NOW

    def test_rejects_naive_bounds(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            EventFilter(occurred_after=dt.datetime(2026, 8, 10, 12, 0))
