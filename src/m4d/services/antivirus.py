"""Scan and finding use cases."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from m4d.domain.antivirus import (
    Finding,
    FindingFilter,
    FindingStatus,
    NewFinding,
    NewScan,
    Scan,
    ScanFilter,
    classify_finding,
    materialise_finding,
)
from m4d.domain.errors import ConflictError, NotFoundError, ValidationError
from m4d.domain.events import EventSeverity
from m4d.domain.pagination import Cursor, Page, normalise_page_size, take_page
from m4d.domain.ports import Clock, UnitOfWork
from m4d.services.activity import WriteResult, emit

__all__ = ["AntivirusService"]


class AntivirusService:
    """Use cases over scans and findings.

    Classification is applied here, at ingest, so every stored finding has
    already been through company policy. The classifier is a pure function; a
    later model adapter replaces the call without touching persistence.
    """

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def queue_scan(self, request: NewScan) -> WriteResult[Scan]:
        """Queue a scan on an enrolled endpoint."""
        now = self._clock.now()
        scan = request.materialise(now=now)

        async with self._uow_factory() as uow:
            endpoint = await uow.endpoints.get(request.endpoint_id)
            if endpoint is None:
                raise NotFoundError("Endpoint", request.endpoint_id)

            if request.idempotency_key is not None:
                existing = await uow.scans.find_by_idempotency_key(request.idempotency_key)
                if existing is not None:
                    return WriteResult(value=existing, was_created=False)

            try:
                stored = await uow.scans.add(scan)
            except ConflictError:
                if request.idempotency_key is None:
                    raise
                winner = await uow.scans.find_by_idempotency_key(request.idempotency_key)
                if winner is None:  # pragma: no cover
                    raise
                return WriteResult(value=winner, was_created=False)

            await emit(
                uow,
                clock=self._clock,
                kind="scan.queued",
                payload={
                    "scan_id": str(stored.id),
                    "endpoint_id": str(stored.endpoint_id),
                    "kind": stored.kind.value,
                    "hostname": endpoint.hostname,
                },
            )
            await uow.commit()

        return WriteResult(value=stored, was_created=True)

    async def start_scan(self, scan_id: UUID) -> Scan:
        """Mark a queued scan as running."""
        async with self._uow_factory() as uow:
            scan = await _require_scan(uow, scan_id)
            stored = await uow.scans.save(scan.start(now=self._clock.now()))
            await emit(
                uow,
                clock=self._clock,
                kind="scan.started",
                payload={"scan_id": str(stored.id), "endpoint_id": str(stored.endpoint_id)},
            )
            await uow.commit()
        return stored

    async def complete_scan(self, scan_id: UUID, *, files_examined: int) -> Scan:
        """Mark a running scan finished.

        ``findings_count`` is the control plane's own tally, not the agent's,
        so a compromised agent cannot under-report detections.
        """
        async with self._uow_factory() as uow:
            scan = await _require_scan(uow, scan_id)
            stored = await uow.scans.save(
                scan.complete(
                    now=self._clock.now(),
                    files_examined=files_examined,
                    findings_count=scan.findings_count,
                )
            )
            await emit(
                uow,
                clock=self._clock,
                kind="scan.completed",
                payload={
                    "scan_id": str(stored.id),
                    "endpoint_id": str(stored.endpoint_id),
                    "files_examined": stored.files_examined,
                    "findings_count": stored.findings_count,
                },
            )
            await uow.commit()
        return stored

    async def fail_scan(self, scan_id: UUID, *, error_message: str) -> Scan:
        """Mark a scan as failed."""
        async with self._uow_factory() as uow:
            scan = await _require_scan(uow, scan_id)
            stored = await uow.scans.save(
                scan.fail(now=self._clock.now(), error_message=error_message)
            )
            await emit(
                uow,
                clock=self._clock,
                kind="scan.failed",
                payload={
                    "scan_id": str(stored.id),
                    "endpoint_id": str(stored.endpoint_id),
                    "error_message": stored.error_message,
                },
                severity=EventSeverity.ERROR,
            )
            await uow.commit()
        return stored

    async def ingest_finding(self, request: NewFinding) -> WriteResult[Finding]:
        """Record a detection, classify it, and isolate the box if policy says so."""
        classification = classify_finding(request)
        finding = materialise_finding(request, classification=classification, now=self._clock.now())

        async with self._uow_factory() as uow:
            if request.idempotency_key is not None:
                existing = await uow.findings.find_by_idempotency_key(request.idempotency_key)
                if existing is not None:
                    return WriteResult(value=existing, was_created=False)

            scan = await _require_scan(uow, request.scan_id)
            if scan.endpoint_id != request.endpoint_id:
                raise ValidationError(
                    "Finding endpoint_id does not match the scan's endpoint.",
                    scan_id=str(scan.id),
                    scan_endpoint_id=str(scan.endpoint_id),
                    finding_endpoint_id=str(request.endpoint_id),
                )
            endpoint = await uow.endpoints.get(request.endpoint_id)
            if endpoint is None:
                raise NotFoundError("Endpoint", request.endpoint_id)

            try:
                stored = await uow.findings.add(finding)
            except ConflictError:
                if request.idempotency_key is None:
                    raise
                winner = await uow.findings.find_by_idempotency_key(request.idempotency_key)
                if winner is None:  # pragma: no cover
                    raise
                return WriteResult(value=winner, was_created=False)

            await uow.scans.save(scan.with_finding_recorded())

            isolated = False
            if classification.auto_quarantines and not endpoint.is_quarantined:
                isolated_endpoint = endpoint.quarantine(
                    reason=f"Auto-isolated: {stored.title}",
                    now=self._clock.now(),
                )
                await uow.endpoints.save(isolated_endpoint)
                isolated = True
                await emit(
                    uow,
                    clock=self._clock,
                    kind="endpoint.quarantined",
                    payload={
                        "endpoint_id": str(endpoint.id),
                        "hostname": endpoint.hostname,
                        "reason": isolated_endpoint.quarantine_reason,
                        "finding_id": str(stored.id),
                        "automatic": True,
                    },
                    severity=EventSeverity.CRITICAL,
                )

            await emit(
                uow,
                clock=self._clock,
                kind="finding.recorded",
                payload={
                    "finding_id": str(stored.id),
                    "scan_id": str(stored.scan_id),
                    "endpoint_id": str(stored.endpoint_id),
                    "category": stored.category.value,
                    "severity": stored.severity.value,
                    "status": stored.status.value,
                    "confidence": stored.ai_confidence,
                    "isolated": isolated,
                },
                severity=stored.severity,
            )
            await uow.commit()

        return WriteResult(value=stored, was_created=True)

    async def dispose_finding(self, finding_id: UUID, *, status: FindingStatus) -> Finding:
        """Apply an operator disposition to a finding."""
        async with self._uow_factory() as uow:
            finding = await _require_finding(uow, finding_id)
            stored = await uow.findings.save(finding.dispose(status=status, now=self._clock.now()))
            await emit(
                uow,
                clock=self._clock,
                kind="finding.disposition_changed",
                payload={
                    "finding_id": str(stored.id),
                    "from_status": finding.status.value,
                    "to_status": stored.status.value,
                },
            )
            await uow.commit()
        return stored

    async def get_scan(self, scan_id: UUID) -> Scan:
        """Return one scan."""
        async with self._uow_factory() as uow:
            return await _require_scan(uow, scan_id)

    async def get_finding(self, finding_id: UUID) -> Finding:
        """Return one finding."""
        async with self._uow_factory() as uow:
            return await _require_finding(uow, finding_id)

    async def list_scans(
        self,
        *,
        filters: ScanFilter | None = None,
        cursor_token: str | None = None,
        limit: int | None = None,
    ) -> Page[Scan]:
        """Return one page of scans, most recently queued first."""
        page_size = normalise_page_size(limit)
        cursor = Cursor.decode(cursor_token) if cursor_token else None
        async with self._uow_factory() as uow:
            rows = await uow.scans.list_page(
                filters=filters or ScanFilter(), after=cursor, limit=page_size + 1
            )
        return take_page(
            rows, page_size, position=lambda item: Cursor(occurred_at=item.queued_at, id=item.id)
        )

    async def list_findings(
        self,
        *,
        filters: FindingFilter | None = None,
        cursor_token: str | None = None,
        limit: int | None = None,
    ) -> Page[Finding]:
        """Return one page of findings, most recently recorded first."""
        page_size = normalise_page_size(limit)
        cursor = Cursor.decode(cursor_token) if cursor_token else None
        async with self._uow_factory() as uow:
            rows = await uow.findings.list_page(
                filters=filters or FindingFilter(), after=cursor, limit=page_size + 1
            )
        return take_page(
            rows, page_size, position=lambda item: Cursor(occurred_at=item.recorded_at, id=item.id)
        )


async def _require_scan(uow: UnitOfWork, scan_id: UUID) -> Scan:
    """Load a scan or raise :class:`NotFoundError`."""
    scan = await uow.scans.get(scan_id)
    if scan is None:
        raise NotFoundError("Scan", scan_id)
    return scan


async def _require_finding(uow: UnitOfWork, finding_id: UUID) -> Finding:
    """Load a finding or raise :class:`NotFoundError`."""
    finding = await uow.findings.get(finding_id)
    if finding is None:
        raise NotFoundError("Finding", finding_id)
    return finding
