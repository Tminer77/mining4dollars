"""Linear Timestamp Protocol endpoints.

Glossary interpretation, tree proposal, and tape commit. The tree view at
``GET /tree`` is the operator display for the same state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from fastapi import Path as ApiPath
from fastapi.responses import HTMLResponse

from m4d.api.deps import ProtocolServiceDep
from m4d.api.schemas import (
    BootstrapResponse,
    GlossaryTermCreateRequest,
    GlossaryTermResponse,
    HeadResponse,
    InterpretationResponse,
    InterpretRequest,
    NodeProposeRequest,
    NodeRejectRequest,
    NodeResponse,
    ProblemDetail,
    TapePageResponse,
    TreeSnapshotResponse,
)
from m4d.domain.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["router", "tree_router"]

router = APIRouter(prefix="/v1/protocol", tags=["protocol"])
tree_router = APIRouter(tags=["protocol"])

_TREE_HTML = Path(__file__).resolve().parents[2] / "static" / "tree.html"

_PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProblemDetail, "description": "Invalid input"},
    status.HTTP_409_CONFLICT: {"model": ProblemDetail, "description": "Guardrail or conflict"},
}


@router.post(
    "/bootstrap",
    response_model=BootstrapResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bootstrap the tape",
    description=(
        "Seeds the core glossary and commits genesis as tick 0 if they do not "
        "already exist. Idempotent: a replay returns the existing origin with **200**."
    ),
    responses={
        status.HTTP_200_OK: {"model": BootstrapResponse, "description": "Already bootstrapped"},
        **_PROBLEM_RESPONSES,
    },
)
async def bootstrap_protocol(
    protocol: ProtocolServiceDep,
    response: Response,
) -> BootstrapResponse:
    """Ensure genesis and the core glossary exist."""
    result = await protocol.bootstrap()
    if not result.was_created:
        response.status_code = status.HTTP_200_OK
    return BootstrapResponse.from_domain(result)


@router.post(
    "/terms",
    response_model=GlossaryTermResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Define a glossary term",
    description=(
        "Adds a canonical term. A replay of an existing slug returns the original "
        "with **200**. An alias that collides with another term is a conflict."
    ),
    responses={
        status.HTTP_200_OK: {"model": GlossaryTermResponse, "description": "Already defined"},
        **_PROBLEM_RESPONSES,
    },
)
async def define_term(
    body: GlossaryTermCreateRequest,
    protocol: ProtocolServiceDep,
    response: Response,
) -> GlossaryTermResponse:
    """Define a glossary term, or return the one that already owns the slug."""
    result = await protocol.define_term(body.to_domain())
    if not result.was_created:
        response.status_code = status.HTTP_200_OK
    return GlossaryTermResponse.from_domain(result.term)


@router.get(
    "/terms",
    response_model=list[GlossaryTermResponse],
    summary="List glossary terms",
)
async def list_terms(protocol: ProtocolServiceDep) -> list[GlossaryTermResponse]:
    """Return the full glossary, seeding core terms if needed."""
    terms = await protocol.list_terms()
    return [GlossaryTermResponse.from_domain(term) for term in terms]


@router.get(
    "/terms/{slug}",
    response_model=GlossaryTermResponse,
    summary="Fetch one glossary term",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such term"},
        **_PROBLEM_RESPONSES,
    },
)
async def get_term(
    protocol: ProtocolServiceDep,
    slug: Annotated[str, ApiPath(description="Canonical kebab-case slug.")],
) -> GlossaryTermResponse:
    """Return one term by slug."""
    term = await protocol.get_term(slug)
    return GlossaryTermResponse.from_domain(term)


@router.post(
    "/interpret",
    response_model=InterpretationResponse,
    summary="Interpret an utterance",
    description=(
        "Binds the utterance against the glossary without committing. "
        "Incomplete interpretations are returned, not rejected: drafting may be "
        "messy, the tape may not. Commit is the gate."
    ),
    responses=_PROBLEM_RESPONSES,
)
async def interpret_utterance(
    body: InterpretRequest,
    protocol: ProtocolServiceDep,
) -> InterpretationResponse:
    """Bind an utterance and return what the glossary made of it."""
    interpretation = await protocol.interpret_utterance(body.utterance)
    return InterpretationResponse.from_domain(interpretation)


@router.post(
    "/nodes",
    response_model=NodeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose a tree node",
    description=(
        "Drafts a node on the Tree of Claude. Empty ``parent_ids`` attach the "
        "node to genesis. The utterance is interpreted immediately; unbound "
        "words do not block proposing, only committing."
    ),
    responses=_PROBLEM_RESPONSES,
)
async def propose_node(
    body: NodeProposeRequest,
    protocol: ProtocolServiceDep,
) -> NodeResponse:
    """Draft a node."""
    node = await protocol.propose(
        body.utterance,
        kind=body.kind,
        parent_ids=tuple(body.parent_ids),
    )
    return NodeResponse.from_domain(node)


@router.post(
    "/nodes/{node_id}/commit",
    response_model=NodeResponse,
    summary="Commit a node onto the tape",
    description=(
        "Assigns the next tick. Refused if the node is not a draft, if any "
        "parent is uncommitted, or if the utterance still contains unbound or "
        "deprecated language."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such node"},
        **_PROBLEM_RESPONSES,
    },
)
async def commit_node(
    protocol: ProtocolServiceDep,
    node_id: Annotated[UUID, ApiPath(description="Identifier of the node.")],
) -> NodeResponse:
    """Serialise a node onto the tape."""
    node = await protocol.commit_node(node_id)
    return NodeResponse.from_domain(node)


@router.post(
    "/nodes/{node_id}/reject",
    response_model=NodeResponse,
    summary="Reject a proposed node",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such node"},
        **_PROBLEM_RESPONSES,
    },
)
async def reject_node(
    body: NodeRejectRequest,
    protocol: ProtocolServiceDep,
    node_id: Annotated[UUID, ApiPath(description="Identifier of the node.")],
) -> NodeResponse:
    """Refuse a draft. A correction is a new node."""
    node = await protocol.reject_node(node_id, body.reason)
    return NodeResponse.from_domain(node)


@router.get(
    "/nodes/{node_id}",
    response_model=NodeResponse,
    summary="Fetch one node",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ProblemDetail, "description": "No such node"},
        **_PROBLEM_RESPONSES,
    },
)
async def get_node(
    protocol: ProtocolServiceDep,
    node_id: Annotated[UUID, ApiPath(description="Identifier of the node.")],
) -> NodeResponse:
    """Return a single node by id."""
    node = await protocol.get_node(node_id)
    return NodeResponse.from_domain(node)


@router.get(
    "/tape",
    response_model=TapePageResponse,
    summary="Replay the tape",
    description=(
        "Returns committed ticks oldest first — the control sequence. Pass "
        "``next_after_tick`` back as ``after_tick`` to continue. This is not "
        "the event log: the tape is linear time, replayed from origin."
    ),
    responses=_PROBLEM_RESPONSES,
)
async def list_tape(
    protocol: ProtocolServiceDep,
    after_tick: Annotated[int, Query(ge=-1, description="Exclusive lower bound on tick.")] = -1,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum ticks to return.")
    ] = DEFAULT_PAGE_SIZE,
) -> TapePageResponse:
    """Return one page of the tape."""
    page = await protocol.list_tape(after_tick=after_tick, limit=limit)
    return TapePageResponse.from_domain(page)


@router.get(
    "/head",
    response_model=HeadResponse,
    summary="Read the protocol clock",
)
async def get_head(protocol: ProtocolServiceDep) -> HeadResponse:
    """Return the last committed instant."""
    head = await protocol.head()
    return HeadResponse.from_domain(head)


@router.get(
    "/tree",
    response_model=TreeSnapshotResponse,
    summary="Snapshot the tree and the tape",
)
async def get_tree(protocol: ProtocolServiceDep) -> TreeSnapshotResponse:
    """Return nodes, tape, and head together."""
    snapshot = await protocol.snapshot()
    return TreeSnapshotResponse.from_domain(snapshot)


@tree_router.get(
    "/tree",
    response_class=HTMLResponse,
    summary="Tree of Claude",
    include_in_schema=False,
)
async def tree_page() -> HTMLResponse:
    """Operator display: linear tape on the left, tree on the right."""
    return HTMLResponse(_TREE_HTML.read_text(encoding="utf-8"))
