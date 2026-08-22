"""Finding query and disposition endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from m4d.api.deps import AntivirusServiceDep
from m4d.api.schemas import ProblemDetail
from m4d.api.shield_schemas import FindingDispositionRequest, FindingPageResponse, FindingResponse
from m4d.domain.antivirus import FindingCategory, FindingFilter, FindingStatus
from m4d.domain.events import EventSeverity
from m4d.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["router"]

router = APIRouter(prefix="/v1/findings", tags=["findings"])

_PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetail, "description": "Invalid input"},
}


@router.get(
    "",
    response_model=FindingPageResponse,
    summary="List findings",
    responses=_PROBLEM_RESPONSES,
)
async def list_findings(
    antivirus: AntivirusServiceDep,
    endpoint_id: Annotated[UUID | None, Query(description="Restrict to one endpoint.")] = None,
    scan_id: Annotated[UUID | None, Query(description="Restrict to one scan.")] = None,
    status_filter: Annotated[
        FindingStatus | None, Query(alias="status", description="Exact match on disposition.")
    ] = None,
    category: Annotated[
        FindingCategory | None, Query(description="Exact match on finding category.")
    ] = None,
    min_severity: Annotated[
        EventSeverity | None, Query(description="Return findings at least this severe.")
    ] = None,
    cursor: Annotated[str | None, Query(description="Opaque token from a previous page.")] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum findings to return.")
    ] = DEFAULT_PAGE_SIZE,
) -> FindingPageResponse:
    """Return one page of findings."""
    page = await antivirus.list_findings(
        filters=FindingFilter(
            endpoint_id=endpoint_id,
            scan_id=scan_id,
            status=status_filter,
            category=category,
            min_severity=min_severity,
        ),
        cursor_token=cursor,
        limit=limit,
    )
    return FindingPageResponse.from_domain(page)


@router.get(
    "/{finding_id}",
    response_model=FindingResponse,
    summary="Fetch one finding",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such finding"},
        **_PROBLEM_RESPONSES,
    },
)
async def get_finding(
    antivirus: AntivirusServiceDep,
    finding_id: Annotated[UUID, Path(description="Identifier of the finding.")],
) -> FindingResponse:
    """Return a single finding by id."""
    return FindingResponse.from_domain(await antivirus.get_finding(finding_id))


@router.post(
    "/{finding_id}/disposition",
    response_model=FindingResponse,
    summary="Change a finding's disposition",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such finding"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal transition"},
        **_PROBLEM_RESPONSES,
    },
)
async def dispose_finding(
    body: FindingDispositionRequest,
    antivirus: AntivirusServiceDep,
    finding_id: Annotated[UUID, Path(description="Identifier of the finding.")],
) -> FindingResponse:
    """Apply an operator disposition to a finding."""
    return FindingResponse.from_domain(
        await antivirus.dispose_finding(finding_id, status=body.status)
    )
