"""Scan and finding ingest endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from m4d.api.deps import AntivirusServiceDep
from m4d.api.schemas import ProblemDetail
from m4d.api.shield_schemas import (
    FindingCreateRequest,
    FindingResponse,
    ScanCompleteRequest,
    ScanCreateRequest,
    ScanFailRequest,
    ScanPageResponse,
    ScanResponse,
)
from m4d.domain.antivirus import ScanFilter, ScanKind, ScanStatus
from m4d.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["router", "scan_collection_router"]

router = APIRouter(prefix="/v1/scans", tags=["scans"])
scan_collection_router = APIRouter(prefix="/v1/endpoints", tags=["scans"])

_PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetail, "description": "Invalid input"},
}


@scan_collection_router.post(
    "/{endpoint_id}/scans",
    response_model=ScanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Queue a scan",
    responses={
        status.HTTP_200_OK: {"model": ScanResponse, "description": "Already queued"},
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such endpoint"},
        **_PROBLEM_RESPONSES,
    },
)
async def queue_scan(
    body: ScanCreateRequest,
    antivirus: AntivirusServiceDep,
    response: Response,
    endpoint_id: Annotated[UUID, Path(description="Endpoint to scan.")],
) -> ScanResponse:
    """Queue a scan on an enrolled endpoint."""
    result = await antivirus.queue_scan(body.to_domain(endpoint_id=endpoint_id))
    if not result.was_created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/v1/scans/{result.value.id}"
    return ScanResponse.from_domain(result.value)


@router.get(
    "",
    response_model=ScanPageResponse,
    summary="List scans",
    responses=_PROBLEM_RESPONSES,
)
async def list_scans(
    antivirus: AntivirusServiceDep,
    endpoint_id: Annotated[UUID | None, Query(description="Restrict to one endpoint.")] = None,
    status_filter: Annotated[
        ScanStatus | None, Query(alias="status", description="Exact match on scan state.")
    ] = None,
    kind: Annotated[ScanKind | None, Query(description="Exact match on scan kind.")] = None,
    cursor: Annotated[str | None, Query(description="Opaque token from a previous page.")] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum scans to return.")
    ] = DEFAULT_PAGE_SIZE,
) -> ScanPageResponse:
    """Return one page of scans."""
    page = await antivirus.list_scans(
        filters=ScanFilter(endpoint_id=endpoint_id, status=status_filter, kind=kind),
        cursor_token=cursor,
        limit=limit,
    )
    return ScanPageResponse.from_domain(page)


@router.get(
    "/{scan_id}",
    response_model=ScanResponse,
    summary="Fetch one scan",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such scan"},
        **_PROBLEM_RESPONSES,
    },
)
async def get_scan(
    antivirus: AntivirusServiceDep,
    scan_id: Annotated[UUID, Path(description="Identifier of the scan.")],
) -> ScanResponse:
    """Return a single scan by id."""
    return ScanResponse.from_domain(await antivirus.get_scan(scan_id))


@router.post(
    "/{scan_id}/start",
    response_model=ScanResponse,
    summary="Mark a scan running",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such scan"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal state"},
        **_PROBLEM_RESPONSES,
    },
)
async def start_scan(
    antivirus: AntivirusServiceDep,
    scan_id: Annotated[UUID, Path(description="Identifier of the scan.")],
) -> ScanResponse:
    """Mark a queued scan as running."""
    return ScanResponse.from_domain(await antivirus.start_scan(scan_id))


@router.post(
    "/{scan_id}/complete",
    response_model=ScanResponse,
    summary="Mark a scan finished",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such scan"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal state"},
        **_PROBLEM_RESPONSES,
    },
)
async def complete_scan(
    body: ScanCompleteRequest,
    antivirus: AntivirusServiceDep,
    scan_id: Annotated[UUID, Path(description="Identifier of the scan.")],
) -> ScanResponse:
    """Mark a running scan finished."""
    return ScanResponse.from_domain(
        await antivirus.complete_scan(scan_id, files_examined=body.files_examined)
    )


@router.post(
    "/{scan_id}/fail",
    response_model=ScanResponse,
    summary="Mark a scan failed",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such scan"},
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Illegal state"},
        **_PROBLEM_RESPONSES,
    },
)
async def fail_scan(
    body: ScanFailRequest,
    antivirus: AntivirusServiceDep,
    scan_id: Annotated[UUID, Path(description="Identifier of the scan.")],
) -> ScanResponse:
    """Mark a scan as failed."""
    return ScanResponse.from_domain(
        await antivirus.fail_scan(scan_id, error_message=body.error_message)
    )


@router.post(
    "/{scan_id}/findings",
    response_model=FindingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a finding",
    description=(
        "Records a detection, classifies it against company threat policy, and "
        "isolates the endpoint when confidence clears the auto-quarantine bar."
    ),
    responses={
        status.HTTP_200_OK: {"model": FindingResponse, "description": "Already recorded"},
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such scan"},
        **_PROBLEM_RESPONSES,
    },
)
async def ingest_finding(
    body: FindingCreateRequest,
    antivirus: AntivirusServiceDep,
    response: Response,
    scan_id: Annotated[UUID, Path(description="Scan that produced the finding.")],
) -> FindingResponse:
    """Record a detection and apply company policy."""
    result = await antivirus.ingest_finding(body.to_domain(scan_id=scan_id))
    if not result.was_created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/v1/findings/{result.value.id}"
    return FindingResponse.from_domain(result.value)
