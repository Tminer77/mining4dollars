"""Fleet inventory use cases, exercised with in-memory ports only."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import uuid4

import pytest

from m4d.domain.endpoints import (
    EndpointPlatform,
    EndpointRole,
    EndpointStatus,
    NewEndpoint,
)
from m4d.domain.errors import ConflictError, NotFoundError
from m4d.services.clock import FrozenClock
from m4d.services.endpoints import EndpointService
from tests.unit.fakes import (
    FakeEndpointRepository,
    FakeEventRepository,
    FakeFindingRepository,
    FakePlanRepository,
    FakeScanRepository,
    FakeUnitOfWork,
)

NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class Harness:
    service: EndpointService
    endpoints: FakeEndpointRepository
    events: FakeEventRepository
    units: list[FakeUnitOfWork]


def build_harness() -> Harness:
    endpoints = FakeEndpointRepository()
    events = FakeEventRepository()
    scans = FakeScanRepository()
    findings = FakeFindingRepository()
    plans = FakePlanRepository()
    units: list[FakeUnitOfWork] = []

    def factory() -> FakeUnitOfWork:
        unit = FakeUnitOfWork(
            events, endpoints=endpoints, scans=scans, findings=findings, plans=plans
        )
        units.append(unit)
        return unit

    return Harness(
        service=EndpointService(uow_factory=factory, clock=FrozenClock(NOW)),
        endpoints=endpoints,
        events=events,
        units=units,
    )


def request(**overrides: object) -> NewEndpoint:
    values: dict[str, object] = {
        "hostname": "rig-01.site",
        "platform": EndpointPlatform.LINUX,
        "role": EndpointRole.MINER,
        "agent_version": "0.1.0",
    }
    values.update(overrides)
    return NewEndpoint(**values)  # type: ignore[arg-type]


@pytest.fixture
def harness() -> Harness:
    return build_harness()


class TestRegister:
    async def test_enrols_a_machine(self, harness: Harness) -> None:
        result = await harness.service.register(request())
        assert result.was_created is True
        assert result.value.hostname == "rig-01.site"
        assert harness.endpoints.by_id[result.value.id] == result.value

    async def test_rebind_on_the_same_hostname(self, harness: Harness) -> None:
        first = await harness.service.register(request())
        second = await harness.service.register(request(agent_version="0.2.0"))
        assert second.was_created is False
        assert second.value.id == first.value.id
        assert second.value.agent_version == "0.2.0"

    async def test_records_an_activity_event(self, harness: Harness) -> None:
        await harness.service.register(request())
        kinds = {event.kind for event in harness.events.by_id.values()}
        assert "endpoint.registered" in kinds

    async def test_commits(self, harness: Harness) -> None:
        await harness.service.register(request())
        assert harness.units[0].committed is True


class TestQuarantine:
    async def test_isolates_and_releases(self, harness: Harness) -> None:
        created = await harness.service.register(request())
        isolated = await harness.service.quarantine(created.value.id, reason="malware family:x")
        assert isolated.status is EndpointStatus.QUARANTINED
        released = await harness.service.release(created.value.id)
        assert released.status is EndpointStatus.ONLINE
        assert released.quarantine_reason is None

    async def test_heartbeat_does_not_lift_quarantine(self, harness: Harness) -> None:
        created = await harness.service.register(request())
        await harness.service.quarantine(created.value.id, reason="malware")
        beat = await harness.service.heartbeat(created.value.id, agent_version="0.3.0")
        assert beat.status is EndpointStatus.QUARANTINED
        assert beat.agent_version == "0.3.0"

    async def test_unknown_id_is_not_found(self, harness: Harness) -> None:
        with pytest.raises(NotFoundError):
            await harness.service.get(uuid4())

    async def test_double_quarantine_conflicts(self, harness: Harness) -> None:
        created = await harness.service.register(request())
        await harness.service.quarantine(created.value.id, reason="malware")
        with pytest.raises(ConflictError):
            await harness.service.quarantine(created.value.id, reason="again")


class TestSnapshot:
    async def test_counts_the_fleet(self, harness: Harness) -> None:
        first = await harness.service.register(request())
        await harness.service.register(request(hostname="rig-02.site"))
        await harness.service.quarantine(first.value.id, reason="malware")
        snapshot = await harness.service.snapshot()
        assert snapshot.endpoints_total == 2
        assert snapshot.endpoints_quarantined == 1
        assert snapshot.endpoints_online == 1
