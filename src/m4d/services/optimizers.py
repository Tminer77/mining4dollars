"""Optimizer plan use cases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from m4d.domain.errors import ConflictError, NotFoundError
from m4d.domain.optimizers import (
    OptimizationPlan,
    PlanFilter,
    propose_plan,
)
from m4d.domain.pagination import Cursor, Page, normalise_page_size, take_page
from m4d.domain.ports import Clock, UnitOfWork
from m4d.services.activity import WriteResult, emit

__all__ = ["OptimizerService"]


class OptimizerService:
    """Use cases over optimizer plans.

    Proposal is composed from the endpoint and its open findings by domain
    policy. This service persists the result and records the operator's
    decision; it does not invent actions of its own.
    """

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def propose(
        self, endpoint_id: UUID, *, idempotency_key: str | None = None
    ) -> WriteResult[OptimizationPlan]:
        """Compose and store a plan for ``endpoint_id``."""
        async with self._uow_factory() as uow:
            if idempotency_key is not None:
                existing = await uow.plans.find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return WriteResult(value=existing, was_created=False)

            endpoint = await uow.endpoints.get(endpoint_id)
            if endpoint is None:
                raise NotFoundError("Endpoint", endpoint_id)
            findings = await uow.findings.list_open_for_endpoint(endpoint_id)
            plan = propose_plan(endpoint, findings, now=self._clock.now())
            if idempotency_key is not None:
                plan = replace(plan, idempotency_key=idempotency_key)

            try:
                stored = await uow.plans.add(plan)
            except ConflictError:
                if idempotency_key is None:
                    raise
                winner = await uow.plans.find_by_idempotency_key(idempotency_key)
                if winner is None:  # pragma: no cover
                    raise
                return WriteResult(value=winner, was_created=False)

            await emit(
                uow,
                clock=self._clock,
                kind="optimizer.plan.proposed",
                payload={
                    "plan_id": str(stored.id),
                    "endpoint_id": str(stored.endpoint_id),
                    "category": stored.category.value,
                    "actions": len(stored.actions),
                },
            )
            await uow.commit()

        return WriteResult(value=stored, was_created=True)

    async def accept(self, plan_id: UUID) -> OptimizationPlan:
        """Operator agrees; the agent has not yet carried the plan out."""
        async with self._uow_factory() as uow:
            plan = await _require_plan(uow, plan_id)
            stored = await uow.plans.save(plan.accept(now=self._clock.now()))
            await emit(
                uow,
                clock=self._clock,
                kind="optimizer.plan.accepted",
                payload={"plan_id": str(stored.id), "endpoint_id": str(stored.endpoint_id)},
            )
            await uow.commit()
        return stored

    async def reject(self, plan_id: UUID) -> OptimizationPlan:
        """Operator declines."""
        async with self._uow_factory() as uow:
            plan = await _require_plan(uow, plan_id)
            stored = await uow.plans.save(plan.reject(now=self._clock.now()))
            await emit(
                uow,
                clock=self._clock,
                kind="optimizer.plan.rejected",
                payload={"plan_id": str(stored.id), "endpoint_id": str(stored.endpoint_id)},
            )
            await uow.commit()
        return stored

    async def apply(self, plan_id: UUID) -> OptimizationPlan:
        """Mark the plan carried out, honouring isolation rules."""
        async with self._uow_factory() as uow:
            plan = await _require_plan(uow, plan_id)
            endpoint = await uow.endpoints.get(plan.endpoint_id)
            if endpoint is None:
                raise NotFoundError("Endpoint", plan.endpoint_id)
            stored = await uow.plans.save(plan.apply(now=self._clock.now(), endpoint=endpoint))
            await emit(
                uow,
                clock=self._clock,
                kind="optimizer.plan.applied",
                payload={
                    "plan_id": str(stored.id),
                    "endpoint_id": str(stored.endpoint_id),
                    "category": stored.category.value,
                },
            )
            await uow.commit()
        return stored

    async def get(self, plan_id: UUID) -> OptimizationPlan:
        """Return one plan."""
        async with self._uow_factory() as uow:
            return await _require_plan(uow, plan_id)

    async def list(
        self,
        *,
        filters: PlanFilter | None = None,
        cursor_token: str | None = None,
        limit: int | None = None,
    ) -> Page[OptimizationPlan]:
        """Return one page of plans, most recently proposed first."""
        page_size = normalise_page_size(limit)
        cursor = Cursor.decode(cursor_token) if cursor_token else None
        async with self._uow_factory() as uow:
            rows = await uow.plans.list_page(
                filters=filters or PlanFilter(), after=cursor, limit=page_size + 1
            )
        return take_page(
            rows, page_size, position=lambda item: Cursor(occurred_at=item.proposed_at, id=item.id)
        )


async def _require_plan(uow: UnitOfWork, plan_id: UUID) -> OptimizationPlan:
    """Load a plan or raise :class:`NotFoundError`."""
    plan = await uow.plans.get(plan_id)
    if plan is None:
        raise NotFoundError("OptimizationPlan", plan_id)
    return plan
