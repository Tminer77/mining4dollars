"""Scan and finding use cases, exercised with in-memory ports only."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import uuid4

import pytest

from m4d.domain.antivirus import (
    FindingCategory,
    FindingStatus,
    NewFinding,
    NewScan,
    ScanKind,
    ScanStatus,
)
from m4d.domain.endpoints import Endpoint, EndpointPlatform, EndpointRole, NewEndpoint
from m4d.domain.errors import ConflictError, NotFoundError, ValidationError
from m4d.services.antivirus import AntivirusService
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
    antivirus: AntivirusService
    endpoints: EndpointService
    scans: FakeScanRepository
    findings: FakeFindingRepository
    endpoint_store: FakeEndpointRepository
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
        antivirus=AntivirusService(uow_factory=factory, clock=clock),
        endpoints=EndpointService(uow_factory=factory, clock=clock),
        scans=scans,
        findings=findings,
        endpoint_store=endpoint_store,
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


class TestScan:
    async def test_queue_start_complete(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        queued = await harness.antivirus.queue_scan(
            NewScan(endpoint_id=endpoint.id, kind=ScanKind.QUICK)
        )
        assert queued.was_created is True
        running = await harness.antivirus.start_scan(queued.value.id)
        assert running.status is ScanStatus.RUNNING
        done = await harness.antivirus.complete_scan(queued.value.id, files_examined=40)
        assert done.status is ScanStatus.COMPLETED
        assert done.files_examined == 40
        assert done.findings_count == 0

    async def test_unknown_endpoint_is_not_found(self, harness: Harness) -> None:
        with pytest.raises(NotFoundError):
            await harness.antivirus.queue_scan(NewScan(endpoint_id=uuid4()))

    async def test_idempotent_queue(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        first = await harness.antivirus.queue_scan(
            NewScan(endpoint_id=endpoint.id, idempotency_key="s1")
        )
        second = await harness.antivirus.queue_scan(
            NewScan(endpoint_id=endpoint.id, idempotency_key="s1")
        )
        assert second.was_created is False
        assert second.value.id == first.value.id

    async def test_complete_uses_control_plane_tally(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        queued = await harness.antivirus.queue_scan(NewScan(endpoint_id=endpoint.id))
        await harness.antivirus.start_scan(queued.value.id)
        await harness.antivirus.ingest_finding(
            NewFinding(
                scan_id=queued.value.id,
                endpoint_id=endpoint.id,
                category=FindingCategory.PUA,
                indicator="pua:toolbar",
                title="toolbar",
            )
        )
        done = await harness.antivirus.complete_scan(queued.value.id, files_examined=9)
        assert done.findings_count == 1


class TestIngest:
    async def test_eicar_isolates_the_endpoint(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        queued = await harness.antivirus.queue_scan(NewScan(endpoint_id=endpoint.id))
        await harness.antivirus.start_scan(queued.value.id)
        result = await harness.antivirus.ingest_finding(
            NewFinding(
                scan_id=queued.value.id,
                endpoint_id=endpoint.id,
                category=FindingCategory.SUSPICIOUS,
                indicator="eicar",
                title="test file",
            )
        )
        assert result.value.status is FindingStatus.QUARANTINED
        stored = harness.endpoint_store.by_id[endpoint.id]
        assert stored.is_quarantined is True
        kinds = {event.kind for event in harness.events.by_id.values()}
        assert "endpoint.quarantined" in kinds
        assert "finding.recorded" in kinds

    async def test_mismatched_endpoint_is_rejected(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        queued = await harness.antivirus.queue_scan(NewScan(endpoint_id=endpoint.id))
        await harness.antivirus.start_scan(queued.value.id)
        with pytest.raises(ValidationError, match="endpoint_id"):
            await harness.antivirus.ingest_finding(
                NewFinding(
                    scan_id=queued.value.id,
                    endpoint_id=uuid4(),
                    category=FindingCategory.SUSPICIOUS,
                    indicator="x",
                    title="x",
                )
            )

    async def test_cannot_ingest_before_start(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        queued = await harness.antivirus.queue_scan(NewScan(endpoint_id=endpoint.id))
        with pytest.raises(ConflictError, match="before the scan has started"):
            await harness.antivirus.ingest_finding(
                NewFinding(
                    scan_id=queued.value.id,
                    endpoint_id=endpoint.id,
                    category=FindingCategory.SUSPICIOUS,
                    indicator="x",
                    title="x",
                )
            )

    async def test_disposition(self, harness: Harness) -> None:
        endpoint = await enrol(harness)
        queued = await harness.antivirus.queue_scan(NewScan(endpoint_id=endpoint.id))
        await harness.antivirus.start_scan(queued.value.id)
        result = await harness.antivirus.ingest_finding(
            NewFinding(
                scan_id=queued.value.id,
                endpoint_id=endpoint.id,
                category=FindingCategory.PUA,
                indicator="pua:toolbar",
                title="toolbar",
            )
        )
        resolved = await harness.antivirus.dispose_finding(
            result.value.id, status=FindingStatus.RESOLVED
        )
        assert resolved.status is FindingStatus.RESOLVED
        assert resolved.resolved_at == NOW
