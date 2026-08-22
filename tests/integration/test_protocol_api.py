"""The protocol HTTP surface, end to end through the real application."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration


def term_body(**overrides: Any) -> dict[str, Any]:
    return {
        "slug": "miner",
        "name": "Miner",
        "definition": "A hashing worker enrolled in the fleet.",
        "aliases": ["rig"],
    } | overrides


class TestBootstrapAndTree:
    async def test_bootstrap_returns_genesis_at_tick_zero(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/protocol/bootstrap")
        assert response.status_code == 201
        body = response.json()
        assert body["was_created"] is True
        assert body["genesis"]["kind"] == "genesis"
        assert body["genesis"]["instant"]["tick"] == 0
        assert body["head"]["tick"] == 0

    async def test_bootstrap_replay_returns_200(self, client: httpx.AsyncClient) -> None:
        first = await client.post("/v1/protocol/bootstrap")
        second = await client.post("/v1/protocol/bootstrap")
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["genesis"]["id"] == first.json()["genesis"]["id"]

    async def test_tree_page_is_served(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/tree")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Tree of Claude" in response.text
        assert "/v1/protocol/tree" in response.text

    async def test_snapshot_contains_genesis(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/protocol/bootstrap")
        snapshot = (await client.get("/v1/protocol/tree")).json()
        assert snapshot["committed_count"] == 1
        assert snapshot["tape"][0]["tick"] == 0
        assert snapshot["glossary_size"] == 15  # core glossary; keep in lockstep with CORE_GLOSSARY


class TestGlossaryAndInterpreter:
    async def test_core_glossary_is_seeded(self, client: httpx.AsyncClient) -> None:
        slugs = {term["slug"] for term in (await client.get("/v1/protocol/terms")).json()}
        assert {"linear-time", "tick", "tape", "tree", "glossary", "guardrail"} <= slugs

    async def test_interpret_binds_disciplined_language(self, client: httpx.AsyncClient) -> None:
        reading = (
            await client.post(
                "/v1/protocol/interpret",
                json={"utterance": "commit the parent node onto the tape"},
            )
        ).json()
        assert reading["complete"] is True
        assert reading["unbound"] == []
        assert reading["bindings"][0]["slug"] == "commit"

    async def test_interpret_names_unbound_words(self, client: httpx.AsyncClient) -> None:
        reading = (
            await client.post(
                "/v1/protocol/interpret",
                json={"utterance": "hack the production database"},
            )
        ).json()
        assert reading["complete"] is False
        assert "hack" in reading["unbound"]

    async def test_defining_a_term_enables_new_language(self, client: httpx.AsyncClient) -> None:
        created = await client.post("/v1/protocol/terms", json=term_body())
        assert created.status_code == 201
        reading = (await client.post("/v1/protocol/interpret", json={"utterance": "miner"})).json()
        assert reading["complete"] is True
        assert reading["bindings"][0]["slug"] == "miner"

    async def test_alias_collision_is_a_conflict(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/protocol/bootstrap")
        response = await client.post(
            "/v1/protocol/terms",
            json=term_body(slug="moment", aliases=["tick"]),
        )
        assert response.status_code == 409


class TestCommitGuardrails:
    async def test_bound_node_commits_as_tick_one(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/protocol/bootstrap")
        proposed = (
            await client.post(
                "/v1/protocol/nodes",
                json={"utterance": "commit the parent node onto the tape"},
            )
        ).json()
        committed = await client.post(f"/v1/protocol/nodes/{proposed['id']}/commit")
        assert committed.status_code == 200
        body = committed.json()
        assert body["status"] == "committed"
        assert body["instant"]["tick"] == 1

    async def test_unbound_language_cannot_commit(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/protocol/bootstrap")
        proposed = (
            await client.post(
                "/v1/protocol/nodes",
                json={"utterance": "hack the production database"},
            )
        ).json()
        response = await client.post(f"/v1/protocol/nodes/{proposed['id']}/commit")
        assert response.status_code == 409
        problem = response.json()
        assert problem["code"] == "guardrail_violation"
        assert problem["context"]["rule"] == "bound_language"

    async def test_child_cannot_commit_before_parent(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/protocol/bootstrap")
        parent = (
            await client.post("/v1/protocol/nodes", json={"utterance": "commit node onto the tape"})
        ).json()
        child = (
            await client.post(
                "/v1/protocol/nodes",
                json={
                    "utterance": "verify parent node",
                    "kind": "verify",
                    "parent_ids": [parent["id"]],
                },
            )
        ).json()
        refused = await client.post(f"/v1/protocol/nodes/{child['id']}/commit")
        assert refused.status_code == 409
        assert refused.json()["context"]["rule"] == "parent_committed"

        await client.post(f"/v1/protocol/nodes/{parent['id']}/commit")
        allowed = await client.post(f"/v1/protocol/nodes/{child['id']}/commit")
        assert allowed.status_code == 200
        assert allowed.json()["instant"]["tick"] == 2

    async def test_tape_replays_oldest_first(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/protocol/bootstrap")
        proposed = (
            await client.post("/v1/protocol/nodes", json={"utterance": "commit node tape"})
        ).json()
        await client.post(f"/v1/protocol/nodes/{proposed['id']}/commit")
        tape = (await client.get("/v1/protocol/tape")).json()["items"]
        ticks = [entry["tick"] for entry in tape]
        assert ticks == [0, 1]
        assert ticks == sorted(ticks)

    async def test_events_are_written_for_commits(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/protocol/bootstrap")
        listing = (await client.get("/v1/events", params={"source": "protocol"})).json()
        kinds = {item["kind"] for item in listing["items"]}
        assert "protocol.genesis" in kinds
