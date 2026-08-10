"""Event ingest and query endpoints."""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from m4d.api.deps import EventServiceDep
from m4d.api.schemas import (
    EventCreateRequest,
    EventPageResponse,
    EventResponse,
    ProblemDetail,
)
from m4d.domain.events import EventFilter, EventSeverity
from m4d.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["router"]

router = APIRouter(prefix="/v1/events", tags=["events"])

_PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetail, "description": "Invalid input"},
}


@router.post(
    "",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an event",
    description=(
        "Appends an event to the log.\n\n"
        "Returns **201** when the event was newly recorded and **200** when an "
        "`idempotency_key` matched an event already stored, in which case the "
        "original is returned unchanged. Clients may therefore retry safely."
    ),
    responses={
        status.HTTP_200_OK: {"model": EventResponse, "description": "Already recorded"},
        **_PROBLEM_RESPONSES,
    },
)
async def record_event(
    body: EventCreateRequest,
    events: EventServiceDep,
    response: Response,
) -> EventResponse:
    """Record an event, or return the one a replayed request already created."""
    result = await events.record(body.to_domain())

    if not result.was_created:
        response.status_code = status.HTTP_200_OK

    response.headers["Location"] = f"/v1/events/{result.event.id}"
    return EventResponse.from_domain(result.event)


@router.get(
    "",
    response_model=EventPageResponse,
    summary="List events",
    description=(
        "Returns events newest first, using keyset pagination.\n\n"
        "Pass the `next_cursor` from a response back as `cursor` to fetch the "
        "following page. A null `next_cursor` means the end of the sequence. "
        "Filters and `limit` must stay identical across a cursor walk; changing "
        "them invalidates the position."
    ),
    responses=_PROBLEM_RESPONSES,
)
async def list_events(
    events: EventServiceDep,
    source: Annotated[
        str | None, Query(description="Exact match on the reporting component.")
    ] = None,
    kind: Annotated[str | None, Query(description="Exact match on the event name.")] = None,
    min_severity: Annotated[
        EventSeverity | None, Query(description="Return events at least this severe.")
    ] = None,
    occurred_after: Annotated[
        dt.datetime | None, Query(description="Exclusive lower bound on occurred_at.")
    ] = None,
    occurred_before: Annotated[
        dt.datetime | None, Query(description="Exclusive upper bound on occurred_at.")
    ] = None,
    cursor: Annotated[str | None, Query(description="Opaque token from a previous page.")] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum events to return.")
    ] = DEFAULT_PAGE_SIZE,
) -> EventPageResponse:
    """Return one page of events."""
    # Constructing the filter validates the time window and raises a domain
    # ValidationError, which the error handlers render as a 422.
    filters = EventFilter(
        source=source,
        kind=kind,
        min_severity=min_severity,
        occurred_after=occurred_after,
        occurred_before=occurred_before,
    )
    page = await events.list(filters=filters, cursor_token=cursor, limit=limit)
    return EventPageResponse.from_domain(page)


@router.get(
    "/{event_id}",
    response_model=EventResponse,
    summary="Fetch one event",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such event"},
        **_PROBLEM_RESPONSES,
    },
)
async def get_event(
    events: EventServiceDep,
    event_id: Annotated[UUID, Path(description="Identifier of the event.")],
) -> EventResponse:
    """Return a single event by id."""
    event = await events.get(event_id)
    return EventResponse.from_domain(event)
