"""Mining-for-dollars HTTP surface."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from m4d.api.deps import ClockDep, MiningServiceDep
from m4d.api.mining_schemas import (
    AssignRequest,
    AssignResponse,
    CapabilitiesRequest,
    CoinCreateRequest,
    CoinResponse,
    FleetResponse,
    HeartbeatRequest,
    PoolCreateRequest,
    PoolResponse,
    ProfitOptionResponse,
    QuoteResponse,
    QuotesIngestRequest,
    WorkerCreateRequest,
    WorkerPageResponse,
    WorkerResponse,
)
from m4d.api.schemas import ProblemDetail
from m4d.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["coins_router", "fleet_router", "pools_router", "quotes_router", "workers_router"]

_PROBLEM: dict[int | str, dict[str, object]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetail, "description": "Invalid input"},
}

coins_router = APIRouter(prefix="/v1/coins", tags=["mining"])
pools_router = APIRouter(prefix="/v1/pools", tags=["mining"])
workers_router = APIRouter(prefix="/v1/workers", tags=["mining"])
quotes_router = APIRouter(prefix="/v1/quotes", tags=["mining"])
fleet_router = APIRouter(prefix="/v1/fleet", tags=["mining"])


@coins_router.post(
    "",
    response_model=CoinResponse,
    status_code=status.HTTP_201_CREATED,
    summary="List a mineable coin",
    responses={
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Ticker already listed"},
        **_PROBLEM,
    },
)
async def create_coin(body: CoinCreateRequest, mining: MiningServiceDep) -> CoinResponse:
    """Add a coin to the catalog."""
    coin = await mining.create_coin(body.to_domain())
    return CoinResponse.from_domain(coin)


@coins_router.get("", response_model=list[CoinResponse], summary="List coins")
async def list_coins(mining: MiningServiceDep) -> list[CoinResponse]:
    """Return the coin catalog."""
    return [CoinResponse.from_domain(coin) for coin in await mining.list_coins()]


@coins_router.get(
    "/{coin_id}",
    response_model=CoinResponse,
    summary="Fetch one coin",
    responses={status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such coin"}},
)
async def get_coin(
    mining: MiningServiceDep, coin_id: Annotated[UUID, Path(description="Coin id")]
) -> CoinResponse:
    """Return one coin."""
    return CoinResponse.from_domain(await mining.get_coin(coin_id))


@pools_router.post(
    "",
    response_model=PoolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a pool",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such coin"},
        **_PROBLEM,
    },
)
async def create_pool(body: PoolCreateRequest, mining: MiningServiceDep) -> PoolResponse:
    """Register a pool endpoint for a listed coin."""
    return PoolResponse.from_domain(await mining.create_pool(body.to_domain()))


@pools_router.get("", response_model=list[PoolResponse], summary="List pools")
async def list_pools(mining: MiningServiceDep) -> list[PoolResponse]:
    """Return every pool."""
    return [PoolResponse.from_domain(pool) for pool in await mining.list_pools()]


@workers_router.post(
    "",
    response_model=WorkerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enrol a mining worker",
    responses={
        status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Name already enrolled"},
        **_PROBLEM,
    },
)
async def enrol_worker(
    body: WorkerCreateRequest, mining: MiningServiceDep, clock: ClockDep
) -> WorkerResponse:
    """Enrol a rig."""
    worker = await mining.enrol_worker(body.to_domain())
    return WorkerResponse.from_domain(worker, now=clock.now())


@workers_router.get("", response_model=WorkerPageResponse, summary="List workers")
async def list_workers(
    mining: MiningServiceDep,
    clock: ClockDep,
    cursor: Annotated[str | None, Query(description="Opaque token from a previous page.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> WorkerPageResponse:
    """Return one page of workers, newest enrolment first."""
    page = await mining.list_workers(cursor_token=cursor, limit=limit)
    return WorkerPageResponse.from_domain(page, now=clock.now())


@workers_router.get(
    "/{worker_id}",
    response_model=WorkerResponse,
    summary="Fetch one worker",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such worker"}
    },
)
async def get_worker(
    mining: MiningServiceDep,
    clock: ClockDep,
    worker_id: Annotated[UUID, Path(description="Worker id")],
) -> WorkerResponse:
    """Return one worker."""
    return WorkerResponse.from_domain(await mining.get_worker(worker_id), now=clock.now())


@workers_router.post(
    "/{worker_id}/capabilities",
    response_model=WorkerResponse,
    summary="Set benchmarked hashrates",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such worker"},
        **_PROBLEM,
    },
)
async def set_capabilities(
    worker_id: UUID,
    body: CapabilitiesRequest,
    mining: MiningServiceDep,
    clock: ClockDep,
) -> WorkerResponse:
    """Replace the worker's algorithm capabilities."""
    worker = await mining.set_capabilities(worker_id, body.to_domain())
    return WorkerResponse.from_domain(worker, now=clock.now())


@workers_router.post(
    "/{worker_id}/heartbeat",
    response_model=WorkerResponse,
    summary="Record telemetry",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such worker"},
        **_PROBLEM,
    },
)
async def heartbeat(
    worker_id: UUID,
    body: HeartbeatRequest,
    mining: MiningServiceDep,
    clock: ClockDep,
) -> WorkerResponse:
    """Mark the worker seen and optionally update hashrate and power."""
    worker = await mining.heartbeat(worker_id, body.to_domain())
    return WorkerResponse.from_domain(worker, now=clock.now())


@workers_router.post(
    "/{worker_id}/disable",
    response_model=WorkerResponse,
    summary="Pause a worker",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such worker"}
    },
)
async def disable_worker(
    worker_id: UUID, mining: MiningServiceDep, clock: ClockDep
) -> WorkerResponse:
    """Stop ranking and assigning this worker."""
    return WorkerResponse.from_domain(await mining.set_enabled(worker_id, False), now=clock.now())


@workers_router.post(
    "/{worker_id}/enable",
    response_model=WorkerResponse,
    summary="Resume a worker",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such worker"}
    },
)
async def enable_worker(
    worker_id: UUID, mining: MiningServiceDep, clock: ClockDep
) -> WorkerResponse:
    """Resume ranking and assigning this worker."""
    return WorkerResponse.from_domain(await mining.set_enabled(worker_id, True), now=clock.now())


@workers_router.get(
    "/{worker_id}/profitability",
    response_model=list[ProfitOptionResponse],
    summary="Rank coins by dollars after electricity",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such worker"}
    },
)
async def profitability(worker_id: UUID, mining: MiningServiceDep) -> list[ProfitOptionResponse]:
    """Score every coin this worker can mine, best profit first."""
    return [
        ProfitOptionResponse.from_domain(option) for option in await mining.profitability(worker_id)
    ]


@workers_router.post(
    "/{worker_id}/assign",
    response_model=AssignResponse,
    summary="Assign the most profitable coin",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such worker"},
        **_PROBLEM,
    },
)
async def assign_worker(
    worker_id: UUID,
    mining: MiningServiceDep,
    clock: ClockDep,
    response: Response,
    body: AssignRequest | None = None,
) -> AssignResponse:
    """Auto-assign the dollar winner, or force a coin if ``coin_id`` is set."""
    request = body or AssignRequest()
    result = await mining.assign(worker_id, coin_id=request.coin_id)
    if not result.changed:
        response.status_code = status.HTTP_200_OK
    return AssignResponse.from_domain(result, now=clock.now())


@quotes_router.post(
    "",
    response_model=list[QuoteResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a market snapshot",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such coin"},
        **_PROBLEM,
    },
)
async def ingest_quotes(body: QuotesIngestRequest, mining: MiningServiceDep) -> list[QuoteResponse]:
    """Record estimated USD/day for one or more coins at a reference hashrate."""
    quotes = await mining.ingest_quotes(body.to_domain())
    return [QuoteResponse.from_domain(quote) for quote in quotes]


@quotes_router.get("", response_model=list[QuoteResponse], summary="Latest quotes")
async def latest_quotes(mining: MiningServiceDep) -> list[QuoteResponse]:
    """Return the newest quote per coin."""
    return [QuoteResponse.from_domain(quote) for quote in await mining.latest_quotes()]


@fleet_router.get(
    "",
    response_model=FleetResponse,
    summary="Dollars the fleet is estimated to be making",
    description=(
        "Sums estimated revenue, electricity cost, and profit across **online, "
        "assigned** workers. That total is the original product: mining for dollars."
    ),
)
async def fleet(mining: MiningServiceDep, clock: ClockDep) -> FleetResponse:
    """Return the operator dollars snapshot."""
    return FleetResponse.from_domain(await mining.fleet(), now=clock.now())
