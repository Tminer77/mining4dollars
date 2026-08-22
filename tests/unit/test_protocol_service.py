"""Protocol service: bootstrap, interpret, propose, commit, serialise."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import uuid4

import pytest

from m4d.domain.errors import ConflictError, GuardrailError, NotFoundError
from m4d.domain.glossary import CORE_GLOSSARY, NewTerm
from m4d.domain.protocol import Kind, NodeStatus
from m4d.services.clock import FrozenClock
from m4d.services.protocol import GENESIS_UTTERANCE, ProtocolService
from tests.unit.fakes import (
    FakeEventRepository,
    FakeGlossaryRepository,
    FakeProtocolRepository,
    FakeUnitOfWork,
)

NOW = dt.datetime(2026, 8, 22, 8, 50, tzinfo=dt.UTC)


class MutableClock:
    """A clock tests can rewind, which FrozenClock will not do."""

    def __init__(self, instant: dt.datetime) -> None:
        self.instant = instant

    def now(self) -> dt.datetime:
        return self.instant


@dataclass
class Harness:
    """A protocol service plus the fakes behind it."""

    service: ProtocolService
    events: FakeEventRepository
    glossary: FakeGlossaryRepository
    protocol: FakeProtocolRepository
    clock: MutableClock
    units: list[FakeUnitOfWork]


def build_harness(clock: MutableClock | FrozenClock | None = None) -> Harness:
    """Wire a ProtocolService onto shared in-memory stores."""
    events = FakeEventRepository()
    glossary = FakeGlossaryRepository()
    protocol = FakeProtocolRepository()
    units: list[FakeUnitOfWork] = []
    timepiece: MutableClock | FrozenClock = clock or MutableClock(NOW)

    def factory() -> FakeUnitOfWork:
        unit = FakeUnitOfWork(events, glossary=glossary, protocol=protocol)
        units.append(unit)
        return unit

    return Harness(
        service=ProtocolService(uow_factory=factory, clock=timepiece),
        events=events,
        glossary=glossary,
        protocol=protocol,
        clock=timepiece if isinstance(timepiece, MutableClock) else MutableClock(NOW),
        units=units,
    )


@pytest.fixture
def harness() -> Harness:
    return build_harness()


class TestBootstrap:
    async def test_commits_genesis_as_tick_zero(self, harness: Harness) -> None:
        result = await harness.service.bootstrap()
        assert result.was_created is True
        assert result.genesis.kind is Kind.GENESIS
        assert result.genesis.status is NodeStatus.COMMITTED
        assert result.genesis.instant is not None
        assert result.genesis.instant.tick == 0
        assert result.head.tick == 0
        assert result.terms_seeded == len(CORE_GLOSSARY)

    async def test_is_idempotent(self, harness: Harness) -> None:
        first = await harness.service.bootstrap()
        second = await harness.service.bootstrap()
        assert second.was_created is False
        assert second.genesis.id == first.genesis.id
        assert harness.protocol.head.tick == 0

    async def test_writes_a_genesis_event(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        kinds = {event.kind for event in harness.events.by_id.values()}
        assert "protocol.genesis" in kinds


class TestGlossary:
    async def test_define_adds_a_term(self, harness: Harness) -> None:
        result = await harness.service.define_term(
            NewTerm(slug="miner", name="Miner", definition="A hashing worker.", aliases=("rig",))
        )
        assert result.was_created is True
        assert result.term.slug == "miner"

    async def test_replay_of_the_same_slug_is_not_a_duplicate(self, harness: Harness) -> None:
        request = NewTerm(slug="miner", name="Miner", definition="A hashing worker.")
        first = await harness.service.define_term(request)
        second = await harness.service.define_term(request)
        assert second.was_created is False
        assert second.term.id == first.term.id

    async def test_alias_collision_is_a_conflict(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        with pytest.raises(ConflictError, match="already belongs"):
            await harness.service.define_term(
                NewTerm(
                    slug="moment", name="Moment", definition="A stolen word.", aliases=("tick",)
                )
            )


class TestInterpretAndCommit:
    async def test_bound_language_commits_as_the_next_tick(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        node = await harness.service.propose("commit the parent node onto the tape")
        committed = await harness.service.commit_node(node.id)
        assert committed.status is NodeStatus.COMMITTED
        assert committed.instant is not None
        assert committed.instant.tick == 1
        assert harness.protocol.head.tick == 1

    async def test_parallel_branches_serialise_onto_the_tape(self, harness: Harness) -> None:
        """The tree may branch. The tape may not."""
        await harness.service.bootstrap()
        left = await harness.service.propose("commit node onto the tape")
        right = await harness.service.propose("verify parent node")
        first = await harness.service.commit_node(left.id)
        second = await harness.service.commit_node(right.id)
        assert first.instant is not None and second.instant is not None
        assert first.instant.tick == 1
        assert second.instant.tick == 2
        tape = [entry.instant.tick for entry in harness.protocol.tape]
        assert tape == [0, 1, 2]

    async def test_unbound_language_is_refused_at_commit(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        node = await harness.service.propose("hack the production database")
        assert node.status is NodeStatus.PROPOSED
        assert node.interpretation is not None
        assert not node.interpretation.is_complete
        with pytest.raises(GuardrailError, match="unbound"):
            await harness.service.commit_node(node.id)

    async def test_a_child_cannot_commit_before_its_parent(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        parent = await harness.service.propose("commit node onto the tape")
        child = await harness.service.propose(
            "verify parent node", kind=Kind.VERIFY, parent_ids=(parent.id,)
        )
        with pytest.raises(GuardrailError, match="parent must be committed"):
            await harness.service.commit_node(child.id)
        committed_parent = await harness.service.commit_node(parent.id)
        committed_child = await harness.service.commit_node(child.id)
        assert committed_parent.instant is not None
        assert committed_child.instant is not None
        assert committed_child.instant.tick == committed_parent.instant.tick + 1

    async def test_empty_parents_attach_to_genesis(self, harness: Harness) -> None:
        genesis = (await harness.service.bootstrap()).genesis
        node = await harness.service.propose("commit node tape")
        assert node.parent_ids == (genesis.id,)

    async def test_a_backwards_clock_still_advances_the_tick(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        harness.clock.instant = NOW - dt.timedelta(hours=3)
        node = await harness.service.propose("commit node onto the tape")
        committed = await harness.service.commit_node(node.id)
        assert committed.instant is not None
        assert committed.instant.tick == 1
        assert committed.instant.clock_skewed is True
        assert committed.instant.wall == NOW

    async def test_rejected_nodes_cannot_commit(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        node = await harness.service.propose("commit node tape")
        await harness.service.reject_node(node.id, "off the rails")
        with pytest.raises(GuardrailError, match="rejected"):
            await harness.service.commit_node(node.id)

    async def test_missing_node_is_not_found(self, harness: Harness) -> None:
        with pytest.raises(NotFoundError):
            await harness.service.get_node(uuid4())

    async def test_snapshot_shows_tree_and_tape(self, harness: Harness) -> None:
        await harness.service.bootstrap()
        node = await harness.service.propose("commit node tape")
        await harness.service.commit_node(node.id)
        snapshot = await harness.service.snapshot()
        assert snapshot.head.tick == 1
        assert snapshot.committed_count == 2
        assert snapshot.glossary_size == len(CORE_GLOSSARY)
        assert snapshot.tape[0].utterance == GENESIS_UTTERANCE

    async def test_interpret_does_not_write_the_tape(self, harness: Harness) -> None:
        reading = await harness.service.interpret_utterance("commit tape")
        assert reading.is_complete
        assert harness.protocol.tape  # genesis, from ensure
        assert harness.protocol.head.tick == 0
