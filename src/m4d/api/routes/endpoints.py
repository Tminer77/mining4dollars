"""Fleet inventory endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from m4d.api.deps import EndpointServiceDep
from m4d.api.schemas import ProblemDetail
from m4d.api.shield_schemas import (
    EndpointCreateRequest,
    EndpointPageResponse,
    EndpointResponse,
    FleetSnapshotResponse,
    HeartbeatRequest,
    QuarantineRequest,
)
from m4d.domain.endpoints import EndpointFilter, EndpointPlatform, EndpointRole, EndpointStatus
from m4d.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["fleet_router", "router"]

router = APIRouter(prefix="/v1/endpoints", tags=["endpoints"])
fleet_router = APIRouter(prefix="/v1/fleet", tags=["fleet"])

_PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetail, "description": "Invalid input"},
}


@router.post(
    "",
    response_model=EndpointResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enrol an endpoint",
    description=(
        "Registers a company machine with Shield.\n\n"
        "Hostname is the natural key. A second register of the same hostname "
        "returns **200** and refreshes last-seen, agent version, role, and labels "
        "rather than creating a duplicate."
    ),
    responses={
        status.HTTP_200_OK: {"model": EndpointResponse, "description": "Already enrolled"},
        **_PROBLEM_RESPONSES,
    },
)
async def register_endpoint(
    body: EndpointCreateRequest,
    endpoints: EndpointServiceDep,
    response: Response,
) -> EndpointResponse:
    """Enrol a machine, or refresh one already known by hostname."""
    result = await endpoints.register(body.to_domain())
    if not result.was_created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/v1/endpoints/{result.value.id}"
    return EndpointResponse.from_domain(result.value)


@router.get(
    "",
    response_model=EndpointPageResponse,
    summary="List endpoints",
    description="Returns machines most recently seen first, using keyset pagination.",
    responses=_PROBLEM_RESPONSES,
)
async def list_endpoints(
    endpoints: EndpointServiceDep,
    status_filter: Annotated[
        EndpointStatus | None,
        Query(alias="status", description="Exact match on operational state."),
    ] = None,
    role: Annotated[EndpointRole | None, Query(description="Exact match on machine role.")] = None,
    platform: Annotated[
        EndpointPlatform | None, Query(description="Exact match on OS family.")
    ] = None,
    hostname: Annotated[str | None, Query(description="Exact match on hostname.")] = None,
    cursor: Annotated[str | None, Query(description="Opaque token from a previous page.")] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum endpoints to return.")
    ] = DEFAULT_PAGE_SIZE,
) -> EndpointPageResponse:
    """Return one page of endpoints."""
    page = await endpoints.list(
        filters=EndpointFilter(
            status=status_filter, role=role, platform=platform, hostname=hostname
        ),
        cursor_token=cursor,
        limit=limit,
    )
    return EndpointPageResponse.from_domain(page)


@router.get(
    "/{endpoint_id}",
    response_model=EndpointResponse,
    summary="Fetch one endpoint",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such endpoint"},
        **_PROBLEM_RESPONSES,
    },
)
async def get_endpoint(
    endpoints: EndpointServiceDep,
    endpoint_id: Annotated[UUID, Path(description="Identifier of the endpoint.")],
) -> EndpointResponse:
    """Return a single endpoint by id."""
    return EndpointResponse.from_domain(await endpoints.get(endpoint_id))


@router.post(
    "/{endpoint_id}/heartbeat",
    response_model=EndpointResponse,
    summary="Record an agent heartbeat",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such endpoint"},
        **_PROBLEM_RESPONSES,
    },
)
async def heartbeat(
    body: HeartbeatRequest,
    endpoints: EndpointServiceDep,
    endpoint_id: Annotated[UUID, Path(description="Identifier of the endpoint.")],
) -> EndpointResponse:
    """Record that the agent is still alive."""
    return EndpointResponse.from_domain(
        await endpoints.heartbeat(endpoint_id, agent_version=body.agent_version, labels=body.labels)
    )


@router.post(
    "/{endpoint_id}/quarantine",
    response_model=EndpointResponse,
    summary="Isolate an endpoint",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such endpoint"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal state"},
        **_PROBLEM_RESPONSES,
    },
)
async def quarantine_endpoint(
    body: QuarantineRequest,
    endpoints: EndpointServiceDep,
    endpoint_id: Annotated[UUID, Path(description="Identifier of the endpoint.")],
) -> EndpointResponse:
    """Isolate a machine from the fleet."""
    return EndpointResponse.from_domain(await endpoints.quarantine(endpoint_id, reason=body.reason))


@router.post(
    "/{endpoint_id}/release",
    response_model=EndpointResponse,
    summary="Release an isolated endpoint",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such endpoint"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Not quarantined"},
        **_PROBLEM_RESPONSES,
    },
)
async def release_endpoint(
    endpoints: EndpointServiceDep,
    endpoint_id: Annotated[UUID, Path(description="Identifier of the endpoint.")],
) -> EndpointResponse:
    """Return a quarantined machine to the fleet."""
    return EndpointResponse.from_domain(await endpoints.release(endpoint_id))


@fleet_router.get(
    "",
    response_model=FleetSnapshotResponse,
    summary="Fleet overview",
    description="Point-in-time counts for the operator dashboard.",
)
async def fleet_snapshot(endpoints: EndpointServiceDep) -> FleetSnapshotResponse:
    """Return the operator overview counts."""
    return FleetSnapshotResponse.from_domain(await endpoints.snapshot())
