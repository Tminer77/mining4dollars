"""The mining HTTP surface: enrol, quote, assign the dollar winner."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

MH = "100000000"


async def _coin(
    client: httpx.AsyncClient, ticker: str, algorithm: str = "ethash"
) -> dict[str, Any]:
    response = await client.post(
        "/v1/coins",
        json={"ticker": ticker, "name": ticker, "algorithm": algorithm},
    )
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def _worker(client: httpx.AsyncClient, name: str = "rig-1") -> dict[str, Any]:
    response = await client.post(
        "/v1/workers",
        json={
            "name": name,
            "power_watts": "1000",
            "electricity_usd_per_kwh": "0.10",
        },
    )
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


class TestOriginalIntent:
    async def test_assigns_the_coin_that_makes_more_dollars(
        self, client: httpx.AsyncClient
    ) -> None:
        ethw = await _coin(client, "ETHW")
        etc = await _coin(client, "ETC")
        worker = await _worker(client)

        caps = await client.post(
            f"/v1/workers/{worker['id']}/capabilities",
            json={"capabilities": [{"algorithm": "ethash", "hashrate_hps": MH}]},
        )
        assert caps.status_code == 200, caps.text

        quotes = await client.post(
            "/v1/quotes",
            json={
                "quotes": [
                    {
                        "coin_id": ethw["id"],
                        "algorithm": "ethash",
                        "revenue_usd_per_day": "10.00",
                        "reference_hashrate_hps": MH,
                        "source": "whattomine",
                    },
                    {
                        "coin_id": etc["id"],
                        "algorithm": "ethash",
                        "revenue_usd_per_day": "5.00",
                        "reference_hashrate_hps": MH,
                        "source": "whattomine",
                    },
                ]
            },
        )
        assert quotes.status_code == 201, quotes.text

        ranked = await client.get(f"/v1/workers/{worker['id']}/profitability")
        assert ranked.status_code == 200
        assert [item["ticker"] for item in ranked.json()] == ["ETHW", "ETC"]
        assert ranked.json()[0]["profit_usd_per_day"] == "7.60000000"

        assigned = await client.post(f"/v1/workers/{worker['id']}/assign")
        assert assigned.status_code == 200, assigned.text
        body = assigned.json()
        assert body["changed"] is True
        assert body["reason"] == "most_profitable"
        assert body["worker"]["assignment"]["coin_id"] == ethw["id"]
        assert body["worker"]["assignment"]["profit_usd_per_day"] == "7.60000000"

        beat = await client.post(
            f"/v1/workers/{worker['id']}/heartbeat",
            json={"algorithm": "ethash", "hashrate_hps": MH},
        )
        assert beat.status_code == 200
        assert beat.json()["status"] == "online"

        fleet = await client.get("/v1/fleet")
        assert fleet.status_code == 200
        snapshot = fleet.json()
        assert snapshot["online_count"] == 1
        assert snapshot["estimated_profit_usd_per_day"] == "7.60000000"

        events = await client.get(
            "/v1/events",
            params={"source": "mining", "kind": "mining.assignment.applied"},
        )
        assert events.status_code == 200
        assert len(events.json()["items"]) == 1

    async def test_duplicate_ticker_is_a_conflict(self, client: httpx.AsyncClient) -> None:
        await _coin(client, "ETHW")
        response = await client.post(
            "/v1/coins", json={"ticker": "ETHW", "name": "again", "algorithm": "ethash"}
        )
        assert response.status_code == 409
        assert response.json()["code"] == "conflict"

    async def test_unknown_worker_is_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/workers/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
