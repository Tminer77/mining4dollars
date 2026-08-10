"""Liveness and readiness endpoints.

Deliberately unversioned and mounted at the root: orchestrators point at fixed
paths, and a probe URL that moves with the API version is a probe that breaks on
the next release.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from m4d import __version__
from m4d.api.deps import HealthServiceDep
from m4d.api.schemas import DependencyStatus, LivenessResponse, ReadinessResponse

__all__ = ["router"]

router = APIRouter(tags=["health"])


@router.get(
    "/healthz",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Reports whether the process is running. Touches no dependency, so a "
        "failure here means the process itself is broken and should be "
        "restarted."
    ),
)
async def liveness() -> LivenessResponse:
    """Return an unconditional alive signal."""
    return LivenessResponse(status="alive", version=__version__)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Reports whether the service can serve traffic. Returns 503 when a "
        "required dependency is unavailable, so the instance is removed from "
        "the load balancer without being restarted."
    ),
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(health: HealthServiceDep, response: Response) -> ReadinessResponse:
    """Probe dependencies and report the verdict."""
    report = await health.readiness()

    if not report.is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status=report.status.value,
        checks=[
            DependencyStatus(
                name=check.name,
                healthy=check.healthy,
                latency_ms=check.latency_ms,
                error=check.error,
            )
            for check in report.checks
        ],
    )
