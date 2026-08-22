"""Domain rules for fleet inventory."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from m4d.domain.endpoints import (
    EndpointPlatform,
    EndpointRole,
    EndpointStatus,
    NewEndpoint,
)
from m4d.domain.errors import ConflictError, ValidationError

NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)


def enrol(**overrides: object) -> NewEndpoint:
    """A valid enrolment request."""
    values: dict[str, object] = {
        "hostname": "rig-01.site",
        "platform": EndpointPlatform.LINUX,
        "role": EndpointRole.MINER,
    }
    values.update(overrides)
    return NewEndpoint(**values)  # type: ignore[arg-type]


class TestNewEndpoint:
    def test_lowercases_hostname(self) -> None:
        assert enrol(hostname="RIG-01.SITE").hostname == "rig-01.site"

    def test_trims_hostname(self) -> None:
        assert enrol(hostname="  rig-01.site  ").hostname == "rig-01.site"

    def test_rejects_a_blank_hostname(self) -> None:
        with pytest.raises(ValidationError, match="hostname"):
            enrol(hostname="   ")

    def test_rejects_too_many_labels(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            enrol(labels={f"k{i}": "v" for i in range(33)})

    def test_materialise_starts_online(self) -> None:
        endpoint = enrol().materialise(now=NOW)
        assert endpoint.status is EndpointStatus.ONLINE
        assert endpoint.last_seen_at == NOW
        assert endpoint.registered_at == NOW


class TestLifecycle:
    def test_heartbeat_marks_offline_online(self) -> None:
        endpoint = replace(enrol().materialise(now=NOW), status=EndpointStatus.OFFLINE)
        later = NOW + dt.timedelta(minutes=1)
        refreshed = endpoint.heartbeat(now=later, agent_version="1.2.0")
        assert refreshed.status is EndpointStatus.ONLINE
        assert refreshed.agent_version == "1.2.0"
        assert refreshed.last_seen_at == later

    def test_heartbeat_does_not_lift_quarantine(self) -> None:
        isolated = enrol().materialise(now=NOW).quarantine(reason="malware", now=NOW)
        refreshed = isolated.heartbeat(now=NOW + dt.timedelta(seconds=5))
        assert refreshed.status is EndpointStatus.QUARANTINED

    def test_quarantine_requires_a_reason(self) -> None:
        with pytest.raises(ValidationError, match="quarantine_reason"):
            enrol().materialise(now=NOW).quarantine(reason="  ", now=NOW)

    def test_double_quarantine_conflicts(self) -> None:
        isolated = enrol().materialise(now=NOW).quarantine(reason="malware", now=NOW)
        with pytest.raises(ConflictError, match="already quarantined"):
            isolated.quarantine(reason="again", now=NOW)

    def test_release_restores_online(self) -> None:
        isolated = enrol().materialise(now=NOW).quarantine(reason="malware", now=NOW)
        released = isolated.release(now=NOW + dt.timedelta(minutes=5))
        assert released.status is EndpointStatus.ONLINE
        assert released.quarantine_reason is None

    def test_release_without_quarantine_conflicts(self) -> None:
        with pytest.raises(ConflictError, match="quarantined"):
            enrol().materialise(now=NOW).release(now=NOW)

    def test_performance_optimizer_only_on_online_boxes(self) -> None:
        assert EndpointStatus.ONLINE.accepts_optimizer_performance is True
        assert EndpointStatus.QUARANTINED.accepts_optimizer_performance is False
