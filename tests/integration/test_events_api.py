"""The events HTTP surface, end to end through the real application."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.integration


def body(**overrides: Any) -> dict[str, Any]:
    """A valid create payload, with overrides applied."""
    return {"source": "api", "kind": "service.started", "severity": "info"} | overrides


class TestCreate:
    async def test_returns_201_with_the_stored_event(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/events", json=body(payload={"port": 8000}))

        assert response.status_code == 201
        recorded = response.json()
        assert recorded["source"] == "api"
        assert recorded["payload"] == {"port": 8000}
        assert recorded["id"]

    async def test_sets_the_location_header(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/events", json=body())
        assert response.headers["Location"] == f"/v1/events/{response.json()['id']}"

    async def test_defaults_occurred_at_to_now(self, client: httpx.AsyncClient) -> None:
        recorded = (await client.post("/v1/events", json=body())).json()
        assert recorded["occurred_at"] is not None
        assert recorded["ingest_lag_ms"] == 0

    async def test_computes_ingest_lag_for_a_backdated_event(
        self, client: httpx.AsyncClient
    ) -> None:
        earlier = dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=30)
        recorded = (
            await client.post("/v1/events", json=body(occurred_at=earlier.isoformat()))
        ).json()
        assert recorded["ingest_lag_ms"] > 25_000

    async def test_rejects_a_naive_timestamp(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/events", json=body(occurred_at="2026-08-10T12:00:00"))
        assert response.status_code == 422

    async def test_rejects_an_unknown_field(self, client: httpx.AsyncClient) -> None:
        """A typo must be a loud error, not a silently dropped value."""
        response = await client.post("/v1/events", json=body(sevrity="info"))
        assert response.status_code == 422

    async def test_rejects_a_blank_source(self, client: httpx.AsyncClient) -> None:
        assert (await client.post("/v1/events", json=body(source=""))).status_code == 422

    async def test_rejects_an_unknown_severity(self, client: httpx.AsyncClient) -> None:
        assert (await client.post("/v1/events", json=body(severity="loud"))).status_code == 422


class TestIdempotency:
    async def test_replay_returns_200_and_the_original(self, client: httpx.AsyncClient) -> None:
        first = await client.post("/v1/events", json=body(idempotency_key="k1"))
        second = await client.post("/v1/events", json=body(idempotency_key="k1"))

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    async def test_replay_does_not_create_a_duplicate(self, client: httpx.AsyncClient) -> None:
        for _ in range(3):
            await client.post("/v1/events", json=body(idempotency_key="k1"))

        listing = (await client.get("/v1/events")).json()
        assert len(listing["items"]) == 1

    async def test_a_different_key_creates_a_new_event(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/events", json=body(idempotency_key="k1"))
        second = await client.post("/v1/events", json=body(idempotency_key="k2"))
        assert second.status_code == 201


class TestFetch:
    async def test_returns_a_stored_event(self, client: httpx.AsyncClient) -> None:
        created = (await client.post("/v1/events", json=body())).json()
        response = await client.get(f"/v1/events/{created['id']}")

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    async def test_unknown_id_returns_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"/v1/events/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_malformed_id_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/events/not-a-uuid")
        assert response.status_code == 422


class TestList:
    async def _seed(self, client: httpx.AsyncClient, count: int) -> None:
        base = dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.UTC)
        for index in range(count):
            await client.post(
                "/v1/events",
                json=body(
                    source="api" if index % 2 == 0 else "worker",
                    severity="error" if index % 3 == 0 else "info",
                    occurred_at=(base + dt.timedelta(seconds=index)).isoformat(),
                ),
            )

    async def test_returns_newest_first(self, client: httpx.AsyncClient) -> None:
        await self._seed(client, 5)
        items = (await client.get("/v1/events")).json()["items"]
        timestamps = [item["occurred_at"] for item in items]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_empty_store_returns_an_empty_page(self, client: httpx.AsyncClient) -> None:
        page = (await client.get("/v1/events")).json()
        assert page["items"] == []
        assert page["next_cursor"] is None

    async def test_cursor_walk_visits_everything_once(self, client: httpx.AsyncClient) -> None:
        await self._seed(client, 10)

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 3}
            if cursor:
                params["cursor"] = cursor
            page = (await client.get("/v1/events", params=params)).json()
            seen.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

        assert cursor is None
        assert len(seen) == len(set(seen)) == 10

    async def test_filters_by_source(self, client: httpx.AsyncClient) -> None:
        await self._seed(client, 6)
        items = (await client.get("/v1/events", params={"source": "worker"})).json()["items"]
        assert {item["source"] for item in items} == {"worker"}

    async def test_filters_by_minimum_severity(self, client: httpx.AsyncClient) -> None:
        await self._seed(client, 6)
        items = (await client.get("/v1/events", params={"min_severity": "error"})).json()["items"]
        assert {item["severity"] for item in items} == {"error"}

    async def test_rejects_an_inverted_time_window(self, client: httpx.AsyncClient) -> None:
        """A domain rule, surfaced through the same error contract as the rest."""
        response = await client.get(
            "/v1/events",
            params={
                "occurred_after": "2026-08-10T13:00:00+00:00",
                "occurred_before": "2026-08-10T12:00:00+00:00",
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    async def test_rejects_a_malformed_cursor(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/events", params={"cursor": "not-a-cursor"})
        assert response.status_code == 422

    async def test_rejects_an_oversized_limit(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/events", params={"limit": 10_000})).status_code == 422

    async def test_rejects_a_zero_limit(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/v1/events", params={"limit": 0})).status_code == 422


class TestErrorContract:
    async def test_errors_use_the_problem_media_type(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"/v1/events/{uuid4()}")
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_problem_body_has_the_required_members(self, client: httpx.AsyncClient) -> None:
        problem = (await client.get(f"/v1/events/{uuid4()}")).json()
        assert {"type", "title", "status", "detail", "code", "instance"} <= problem.keys()
        assert problem["status"] == 404

    async def test_problem_body_carries_the_request_id(self, client: httpx.AsyncClient) -> None:
        """The one thing a caller can quote that ties their failure to our logs."""
        response = await client.get(f"/v1/events/{uuid4()}")
        assert response.json()["request_id"] == response.headers["X-Request-ID"]

    async def test_validation_problems_list_the_offending_fields(
        self, client: httpx.AsyncClient
    ) -> None:
        problem = (await client.post("/v1/events", json={"kind": "a.b"})).json()
        assert problem["code"] == "request_validation_error"
        assert any("source" in error["location"] for error in problem["errors"])


class TestRequestCorrelation:
    async def test_generates_an_id_when_absent(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/events")
        assert response.headers["X-Request-ID"]

    async def test_echoes_a_caller_supplied_id(self, client: httpx.AsyncClient) -> None:
        """Lets a single trace span several services."""
        response = await client.get("/v1/events", headers={"X-Request-ID": "trace-abc-123"})
        assert response.headers["X-Request-ID"] == "trace-abc-123"

    async def test_ids_differ_between_requests(self, client: httpx.AsyncClient) -> None:
        first = await client.get("/v1/events")
        second = await client.get("/v1/events")
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]

    @pytest.mark.parametrize(
        "hostile",
        ["x" * 300, "bad\nvalue", "semi;colon"],
    )
    async def test_replaces_a_hostile_id(self, client: httpx.AsyncClient, hostile: str) -> None:
        """An unbounded or newline-bearing id would let a caller forge log lines."""
        response = await client.get("/v1/events", headers={"X-Request-ID": hostile})
        assert response.headers["X-Request-ID"] != hostile
