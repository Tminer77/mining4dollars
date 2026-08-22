"""Linear timestamp rules: the tape does not run backwards, the tree serialises."""

from __future__ import annotations

import datetime as dt
from uuid import UUID, uuid4

import pytest

from m4d.domain.errors import GuardrailError, ValidationError
from m4d.domain.glossary import CORE_GLOSSARY, interpret
from m4d.domain.protocol import (
    Kind,
    LinearInstant,
    NodeStatus,
    ProtocolHead,
    ProtocolNode,
    advance,
    assert_commit_allowed,
    empty_head,
)

NOW = dt.datetime(2026, 8, 22, 8, 50, tzinfo=dt.UTC)
EARLIER = NOW - dt.timedelta(hours=1)
GLOSSARY = tuple(term.materialise(now=NOW) for term in CORE_GLOSSARY)


def _node(
    utterance: str = "commit the parent node onto the tape",
    *,
    kind: Kind = Kind.ACT,
    status: NodeStatus = NodeStatus.PROPOSED,
    parent_ids: tuple[UUID, ...] = (),
) -> ProtocolNode:
    reading = interpret(utterance, GLOSSARY, now=NOW)
    return ProtocolNode(
        id=uuid4(),
        kind=kind,
        utterance=reading.utterance,
        status=status,
        parent_ids=parent_ids,
        interpretation=reading,
        instant=None,
        proposed_at=NOW,
    )


class TestAdvance:
    def test_genesis_is_tick_zero(self) -> None:
        instant, head = advance(empty_head(), NOW)
        assert instant.tick == 0
        assert instant.wall == NOW
        assert instant.clock_skewed is False
        assert head.tick == 0

    def test_each_commit_increments_the_tick(self) -> None:
        _, head = advance(empty_head(), NOW)
        instant, _ = advance(head, NOW + dt.timedelta(seconds=1))
        assert instant.tick == 1

    def test_a_backwards_clock_does_not_run_the_tape_backwards(self) -> None:
        """Wall clocks lie. The protocol does not."""
        _, head = advance(empty_head(), NOW)
        instant, new_head = advance(head, EARLIER)
        assert instant.tick == 1
        assert instant.wall == NOW
        assert instant.clock_skewed is True
        assert new_head.wall == NOW

    def test_equal_wall_time_is_allowed(self) -> None:
        _, head = advance(empty_head(), NOW)
        instant, _ = advance(head, NOW)
        assert instant.tick == 1
        assert instant.clock_skewed is False

    def test_rejects_a_naive_now(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            advance(empty_head(), dt.datetime(2026, 8, 22, 8, 50))

    def test_instants_totally_order(self) -> None:
        first, head = advance(empty_head(), NOW)
        second, _ = advance(head, NOW)
        assert first.precedes(second)
        assert not second.precedes(first)


class TestLinearInstant:
    def test_rejects_a_negative_tick(self) -> None:
        with pytest.raises(ValidationError, match="tick"):
            LinearInstant(tick=-1, wall=NOW, id=uuid4())


class TestCommitGuardrails:
    def test_unbound_language_cannot_commit(self) -> None:
        node = _node("hack the production database")
        with pytest.raises(GuardrailError, match="unbound") as caught:
            assert_commit_allowed(node, (), genesis_committed=True)
        assert caught.value.code == "guardrail_violation"
        assert caught.value.context["rule"] == "bound_language"

    def test_a_child_cannot_commit_before_its_parent(self) -> None:
        parent = _node()
        child = _node("verify parent node", kind=Kind.VERIFY, parent_ids=(parent.id,))
        with pytest.raises(GuardrailError, match="parent must be committed") as caught:
            assert_commit_allowed(child, (parent,), genesis_committed=True)
        assert caught.value.context["rule"] == "parent_committed"

    def test_nothing_commits_before_genesis(self) -> None:
        node = _node()
        with pytest.raises(GuardrailError, match="before genesis") as caught:
            assert_commit_allowed(node, (), genesis_committed=False)
        assert caught.value.context["rule"] == "genesis_first"

    def test_a_committed_node_is_immutable(self) -> None:
        node = _node(status=NodeStatus.COMMITTED)
        with pytest.raises(GuardrailError, match="committed again"):
            assert_commit_allowed(node, (), genesis_committed=True)

    def test_a_rejected_node_cannot_be_committed(self) -> None:
        node = _node().rejected("off the rails", at=NOW)
        with pytest.raises(GuardrailError, match="rejected"):
            assert_commit_allowed(node, (), genesis_committed=True)

    def test_a_node_cannot_be_its_own_parent(self) -> None:
        node_id = uuid4()
        with pytest.raises(GuardrailError, match="own parent") as caught:
            ProtocolNode(
                id=node_id,
                kind=Kind.ACT,
                utterance="commit node tape",
                status=NodeStatus.PROPOSED,
                parent_ids=(node_id,),
                interpretation=None,
                instant=None,
                proposed_at=NOW,
            )
        assert caught.value.context["rule"] == "no_cycle"

    def test_a_complete_draft_with_committed_parents_is_allowed(self) -> None:
        parent = _node()
        instant = LinearInstant(tick=0, wall=NOW, id=uuid4())
        parent = parent.committed(instant, at=NOW)
        child = _node("verify parent node", kind=Kind.VERIFY, parent_ids=(parent.id,))
        assert_commit_allowed(child, (parent,), genesis_committed=True)

    def test_head_starts_empty(self) -> None:
        head = empty_head()
        assert head.is_empty
        assert head.next_tick == 0
        assert ProtocolHead(tick=-1, wall=None, instant_id=None).is_empty
