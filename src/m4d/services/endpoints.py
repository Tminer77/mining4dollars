"""Fleet inventory use cases."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from m4d.domain.endpoints import (
    Endpoint,
    EndpointFilter,
    EndpointStatus,
    FleetSnapshot,
    NewEndpoint,
)
from m4d.domain.errors import ConflictError, NotFoundError
from m4d.domain.events import EventSeverity
from m4d.domain.optimizers import PlanStatus
from m4d.domain.pagination import Cursor, Page, normalise_page_size, take_page
from m4d.domain.ports import Clock, UnitOfWork
from m4d.services.activity import WriteResult, emit

__all__ = ["EndpointService"]


class EndpointService:
    """Use cases over the company fleet.

    Depends only on ports, so it can be exercised with in-memory fakes.
    """

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def register(self, request: NewEndpoint) -> WriteResult[Endpoint]:
        """Enrol a machine, or refresh one already known by hostname.

        Hostname is the natural key. Agents reinstall, so a second register is
        a rebind rather than a duplicate: last-seen, agent version, role, and
        labels update; identity does not. A concurrent pair of first-time
        registers is settled by the unique index, the same way event ingest is.
        """
        now = self._clock.now()

        async with self._uow_factory() as uow:
            existing = await uow.endpoints.find_by_hostname(request.hostname)
            if existing is not None:
                rebound = existing.rebind(
                    now=now,
                    agent_version=request.agent_version,
                    labels=request.labels,
                    platform=request.platform,
                    role=request.role,
                )
                stored = await uow.endpoints.save(rebound)
                await emit(
                    uow,
                    clock=self._clock,
                    kind="endpoint.rebound",
                    payload={"endpoint_id": str(stored.id), "hostname": stored.hostname},
                )
                await uow.commit()
                return WriteResult(value=stored, was_created=False)

            endpoint = request.materialise(now=now)
            try:
                stored = await uow.endpoints.add(endpoint)
            except ConflictError:
                winner = await uow.endpoints.find_by_hostname(request.hostname)
                if winner is None:  # pragma: no cover - implies the index vanished
                    raise
                return WriteResult(value=winner, was_created=False)

            await emit(
                uow,
                clock=self._clock,
                kind="endpoint.registered",
                payload={
                    "endpoint_id": str(stored.id),
                    "hostname": stored.hostname,
                    "role": stored.role.value,
                    "platform": stored.platform.value,
                },
            )
            await uow.commit()

        return WriteResult(value=stored, was_created=True)

    async def heartbeat(
        self,
        endpoint_id: UUID,
        *,
        agent_version: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> Endpoint:
        """Record that the agent is still alive."""
        async with self._uow_factory() as uow:
            endpoint = await _require_endpoint(uow, endpoint_id)
            updated = endpoint.heartbeat(
                now=self._clock.now(), agent_version=agent_version, labels=labels
            )
            stored = await uow.endpoints.save(updated)
            await emit(
                uow,
                clock=self._clock,
                kind="endpoint.heartbeat",
                payload={"endpoint_id": str(stored.id), "status": stored.status.value},
            )
            await uow.commit()
        return stored

    async def quarantine(self, endpoint_id: UUID, *, reason: str) -> Endpoint:
        """Isolate a machine from the fleet."""
        async with self._uow_factory() as uow:
            endpoint = await _require_endpoint(uow, endpoint_id)
            updated = endpoint.quarantine(reason=reason, now=self._clock.now())
            stored = await uow.endpoints.save(updated)
            await emit(
                uow,
                clock=self._clock,
                kind="endpoint.quarantined",
                payload={
                    "endpoint_id": str(stored.id),
                    "hostname": stored.hostname,
                    "reason": stored.quarantine_reason,
                },
                severity=EventSeverity.ERROR,
            )
            await uow.commit()
        return stored

    async def release(self, endpoint_id: UUID) -> Endpoint:
        """Return a quarantined machine to the fleet."""
        async with self._uow_factory() as uow:
            endpoint = await _require_endpoint(uow, endpoint_id)
            updated = endpoint.release(now=self._clock.now())
            stored = await uow.endpoints.save(updated)
            await emit(
                uow,
                clock=self._clock,
                kind="endpoint.released",
                payload={"endpoint_id": str(stored.id), "hostname": stored.hostname},
                severity=EventSeverity.WARNING,
            )
            await uow.commit()
        return stored

    async def get(self, endpoint_id: UUID) -> Endpoint:
        """Return one endpoint.

        Raises:
            NotFoundError: if no endpoint has that id.
        """
        async with self._uow_factory() as uow:
            return await _require_endpoint(uow, endpoint_id)

    async def list(
        self,
        *,
        filters: EndpointFilter | None = None,
        cursor_token: str | None = None,
        limit: int | None = None,
    ) -> Page[Endpoint]:
        """Return one page of endpoints, most recently seen first."""
        page_size = normalise_page_size(limit)
        cursor = Cursor.decode(cursor_token) if cursor_token else None
        async with self._uow_factory() as uow:
            rows = await uow.endpoints.list_page(
                filters=filters or EndpointFilter(),
                after=cursor,
                limit=page_size + 1,
            )
        return take_page(
            rows,
            page_size,
            position=lambda item: Cursor(occurred_at=item.last_seen_at, id=item.id),
        )

    async def snapshot(self) -> FleetSnapshot:
        """Return the operator overview counts."""
        async with self._uow_factory() as uow:
            total = await uow.endpoints.count()
            online = await uow.endpoints.count(status=EndpointStatus.ONLINE)
            offline = await uow.endpoints.count(status=EndpointStatus.OFFLINE)
            quarantined = await uow.endpoints.count(status=EndpointStatus.QUARANTINED)
            in_flight = await uow.scans.count_in_flight()
            open_findings = await uow.findings.count_open()
            actionable = await uow.findings.count_open(actionable_only=True)
            pending_proposed = await uow.plans.count(status=PlanStatus.PROPOSED)
            pending_accepted = await uow.plans.count(status=PlanStatus.ACCEPTED)
        return FleetSnapshot(
            endpoints_total=total,
            endpoints_online=online,
            endpoints_offline=offline,
            endpoints_quarantined=quarantined,
            scans_in_flight=in_flight,
            findings_open=open_findings,
            findings_open_actionable=actionable,
            plans_pending=pending_proposed + pending_accepted,
        )


async def _require_endpoint(uow: UnitOfWork, endpoint_id: UUID) -> Endpoint:
    """Load an endpoint or raise :class:`NotFoundError`."""
    endpoint = await uow.endpoints.get(endpoint_id)
    if endpoint is None:
        raise NotFoundError("Endpoint", endpoint_id)
    return endpoint
