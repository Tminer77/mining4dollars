"""Optimizer plan endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from m4d.api.deps import OptimizerServiceDep
from m4d.api.schemas import ProblemDetail
from m4d.api.shield_schemas import (
    OptimizerProposeRequest,
    PlanPageResponse,
    PlanResponse,
)
from m4d.domain.optimizers import OptimizerCategory, PlanFilter, PlanStatus
from m4d.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["plan_router", "router"]

router = APIRouter(prefix="/v1/optimizer/plans", tags=["optimizer"])
plan_router = APIRouter(prefix="/v1/endpoints", tags=["optimizer"])

_PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetail, "description": "Invalid input"},
}


@plan_router.post(
    "/{endpoint_id}/optimizer/plans",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose an optimizer plan",
    description=(
        "Composes a plan from the endpoint's role and its open findings. "
        "Security outranks performance: a miner with malware is recovered "
        "before it is tuned."
    ),
    responses={
        status.HTTP_200_OK: {"model": PlanResponse, "description": "Already proposed"},
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such endpoint"},
        **_PROBLEM_RESPONSES,
    },
)
async def propose_plan(
    body: OptimizerProposeRequest,
    optimizer: OptimizerServiceDep,
    response: Response,
    endpoint_id: Annotated[UUID, Path(description="Endpoint to optimize.")],
) -> PlanResponse:
    """Compose and store a plan for an endpoint."""
    result = await optimizer.propose(endpoint_id, idempotency_key=body.idempotency_key)
    if not result.was_created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/v1/optimizer/plans/{result.value.id}"
    return PlanResponse.from_domain(result.value)


@router.get(
    "",
    response_model=PlanPageResponse,
    summary="List optimizer plans",
    responses=_PROBLEM_RESPONSES,
)
async def list_plans(
    optimizer: OptimizerServiceDep,
    endpoint_id: Annotated[UUID | None, Query(description="Restrict to one endpoint.")] = None,
    status_filter: Annotated[
        PlanStatus | None, Query(alias="status", description="Exact match on plan state.")
    ] = None,
    category: Annotated[
        OptimizerCategory | None, Query(description="Exact match on plan category.")
    ] = None,
    cursor: Annotated[str | None, Query(description="Opaque token from a previous page.")] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum plans to return.")
    ] = DEFAULT_PAGE_SIZE,
) -> PlanPageResponse:
    """Return one page of plans."""
    page = await optimizer.list(
        filters=PlanFilter(endpoint_id=endpoint_id, status=status_filter, category=category),
        cursor_token=cursor,
        limit=limit,
    )
    return PlanPageResponse.from_domain(page)


@router.get(
    "/{plan_id}",
    response_model=PlanResponse,
    summary="Fetch one optimizer plan",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such plan"},
        **_PROBLEM_RESPONSES,
    },
)
async def get_plan(
    optimizer: OptimizerServiceDep,
    plan_id: Annotated[UUID, Path(description="Identifier of the plan.")],
) -> PlanResponse:
    """Return a single plan by id."""
    return PlanResponse.from_domain(await optimizer.get(plan_id))


@router.post(
    "/{plan_id}/accept",
    response_model=PlanResponse,
    summary="Accept a plan",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such plan"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal state"},
        **_PROBLEM_RESPONSES,
    },
)
async def accept_plan(
    optimizer: OptimizerServiceDep,
    plan_id: Annotated[UUID, Path(description="Identifier of the plan.")],
) -> PlanResponse:
    """Operator agrees; the agent has not yet carried the plan out."""
    return PlanResponse.from_domain(await optimizer.accept(plan_id))


@router.post(
    "/{plan_id}/reject",
    response_model=PlanResponse,
    summary="Reject a plan",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such plan"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal state"},
        **_PROBLEM_RESPONSES,
    },
)
async def reject_plan(
    optimizer: OptimizerServiceDep,
    plan_id: Annotated[UUID, Path(description="Identifier of the plan.")],
) -> PlanResponse:
    """Operator declines."""
    return PlanResponse.from_domain(await optimizer.reject(plan_id))


@router.post(
    "/{plan_id}/apply",
    response_model=PlanResponse,
    summary="Apply a plan",
    description=(
        "Marks every action applied. Applying from `proposed` is a shortcut. "
        "Performance and thermal plans cannot be applied while the endpoint "
        "is quarantined."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such plan"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal state"},
        **_PROBLEM_RESPONSES,
    },
)
async def apply_plan(
    optimizer: OptimizerServiceDep,
    plan_id: Annotated[UUID, Path(description="Identifier of the plan.")],
) -> PlanResponse:
    """Mark the plan carried out, honouring isolation rules."""
    return PlanResponse.from_domain(await optimizer.apply(plan_id))
