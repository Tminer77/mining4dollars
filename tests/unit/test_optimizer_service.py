"""Optimizer use cases, exercised with in-memory ports only."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from m4d.domain.antivirus import FindingCategory, NewFinding, NewScan
from m4d.domain.endpoints import Endpoint, EndpointPlatform, EndpointRole, NewEndpoint
from m4d.domain.errors import ConflictError
from m4d.domain.optimizers import ActionKind, OptimizerCategory, PlanStatus
from m4d.services.antivirus import AntivirusService
from m4d.services.clock import FrozenClock
from m4d.services.endpoints import EndpointService
from m4d.services.optimizers import OptimizerService
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
    optimizer: OptimizerService
    antivirus: AntivirusService
    endpoints: EndpointService
    plans: FakePlanRepository
    events: FakeEventRepository


def build_harness() -> Harness:
    endpoint_store = FakeEndpointRepository()
    events = FakeEventRepository()
    scans = FakeScanRepository()
    findings = FakeFindingRepository()
    plans = FakePlanRepository()

    def factory() -> FakeUnitOfWork:
        return FakeUnitOfWork(
            events,
            endpoints=endpoint_store,
            scans=scans,
            findings=findings,
            plans=plans,
        )

    clock = FrozenClock(NOW)
    return Harness(
        optimizer=OptimizerService(uow_factory=factory, clock=clock),
        antivirus=AntivirusService(uow_factory=factory, clock=clock),
        endpoints=EndpointService(uow_factory=factory, clock=clock),
        plans=plans,
        events=events,
    )


async def enrol(harness: Harness) -> Endpoint:
    result = await harness.endpoints.register(
        NewEndpoint(
            hostname="rig-01",
            platform=EndpointPlatform.LINUX,
            role=EndpointRole.MINER,
        )
    )
    return result.value


@pytest.fixture
def harness() -> Harness:
    return build_harness()


class TestPropose:
    async def test_clean_miner_gets_a_performance_plan(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        result = await harness.optimizer.propose(endpoint.id)
        assert result.was_created is True
        assert result.value.category is OptimizerCategory.PERFORMANCE
        kinds = {action.kind for action in result.value.actions}
        assert ActionKind.GPU_THERMAL_PROFILE in kinds
        assert "optimizer.plan.proposed" in {event.kind for event in harness.events.by_id.values()}

    async def test_malware_finding_flips_the_plan_to_security(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        queued = await harness.antivirus.queue_scan(NewScan(endpoint_id=endpoint.id))
        await harness.antivirus.start_scan(queued.value.id)
        await harness.antivirus.ingest_finding(
            NewFinding(
                scan_id=queued.value.id,
                endpoint_id=endpoint.id,
                category=FindingCategory.MALWARE,
                indicator="family:emotet",
                title="loader",
            )
        )
        result = await harness.optimizer.propose(endpoint.id)
        assert result.value.category is OptimizerCategory.SECURITY
        kinds = {action.kind for action in result.value.actions}
        assert ActionKind.AV_UPDATE_SIGNATURES in kinds
        assert ActionKind.GPU_POWER_LIMIT not in kinds

    async def test_idempotent_propose(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        first = await harness.optimizer.propose(endpoint.id, idempotency_key="p1")
        second = await harness.optimizer.propose(endpoint.id, idempotency_key="p1")
        assert second.was_created is False
        assert second.value.id == first.value.id


class TestDecisions:
    async def test_accept_and_apply(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        proposed = await harness.optimizer.propose(endpoint.id)
        accepted = await harness.optimizer.accept(proposed.value.id)
        assert accepted.status is PlanStatus.ACCEPTED
        applied = await harness.optimizer.apply(proposed.value.id)
        assert applied.status is PlanStatus.APPLIED

    async def test_cannot_apply_performance_while_quarantined(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        proposed = await harness.optimizer.propose(endpoint.id)
        await harness.endpoints.quarantine(endpoint.id, reason="manual isolation")
        with pytest.raises(ConflictError, match="isolated"):
            await harness.optimizer.apply(proposed.value.id)

    async def test_reject_is_terminal(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        proposed = await harness.optimizer.propose(endpoint.id)
        await harness.optimizer.reject(proposed.value.id)
        with pytest.raises(ConflictError, match="rejected"):
            await harness.optimizer.apply(proposed.value.id)
