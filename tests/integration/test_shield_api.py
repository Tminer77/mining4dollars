"""Shield HTTP surface, end to end through the real application."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.integration


def enrol(**overrides: Any) -> dict[str, Any]:
    """A valid endpoint registration payload."""
    return {
        "hostname": "rig-01.site",
        "platform": "linux",
        "role": "miner",
        "agent_version": "0.1.0",
    } | overrides


class TestEndpoints:
    async def test_register_returns_201(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/endpoints", json=enrol())
        assert response.status_code == 201
        body = response.json()
        assert body["hostname"] == "rig-01.site"
        assert body["status"] == "online"
        assert response.headers["Location"] == f"/v1/endpoints/{body['id']}"

    async def test_rebind_returns_200(self, client: httpx.AsyncClient) -> None:
        first = await client.post("/v1/endpoints", json=enrol())
        second = await client.post("/v1/endpoints", json=enrol(agent_version="0.2.0"))
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["agent_version"] == "0.2.0"

    async def test_quarantine_and_release(self, client: httpx.AsyncClient) -> None:
        created = (await client.post("/v1/endpoints", json=enrol())).json()
        isolated = await client.post(
            f"/v1/endpoints/{created['id']}/quarantine", json={"reason": "manual"}
        )
        assert isolated.status_code == 200
        assert isolated.json()["status"] == "quarantined"
        released = await client.post(f"/v1/endpoints/{created['id']}/release")
        assert released.json()["status"] == "online"
        assert released.json()["quarantine_reason"] is None

    async def test_unknown_endpoint_is_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"/v1/endpoints/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_fleet_snapshot(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/endpoints", json=enrol())
        await client.post("/v1/endpoints", json=enrol(hostname="rig-02.site"))
        snapshot = (await client.get("/v1/fleet")).json()
        assert snapshot["endpoints_total"] == 2
        assert snapshot["endpoints_online"] == 2


class TestScanAndFinding:
    async def _box(self, client: httpx.AsyncClient) -> dict[str, Any]:
        payload: dict[str, Any] = (await client.post("/v1/endpoints", json=enrol())).json()
        return payload

    async def test_scan_lifecycle_and_eicar_isolation(self, client: httpx.AsyncClient) -> None:
        box = await self._box(client)
        queued = await client.post(f"/v1/endpoints/{box['id']}/scans", json={"kind": "full"})
        assert queued.status_code == 201
        scan_id = queued.json()["id"]

        started = await client.post(f"/v1/scans/{scan_id}/start")
        assert started.json()["status"] == "running"

        finding = await client.post(
            f"/v1/scans/{scan_id}/findings",
            json={
                "endpoint_id": box["id"],
                "category": "suspicious",
                "indicator": "eicar",
                "title": "EICAR test file",
            },
        )
        assert finding.status_code == 201
        body = finding.json()
        assert body["category"] == "malware"
        assert body["status"] == "quarantined"
        assert body["ai_confidence"] >= 0.9

        isolated = (await client.get(f"/v1/endpoints/{box['id']}")).json()
        assert isolated["status"] == "quarantined"

        done = await client.post(f"/v1/scans/{scan_id}/complete", json={"files_examined": 12})
        assert done.json()["findings_count"] == 1
        assert done.json()["files_examined"] == 12

    async def test_finding_replay_is_idempotent(self, client: httpx.AsyncClient) -> None:
        box = await self._box(client)
        scan_id = (await client.post(f"/v1/endpoints/{box['id']}/scans", json={})).json()["id"]
        await client.post(f"/v1/scans/{scan_id}/start")
        payload = {
            "endpoint_id": box["id"],
            "category": "pua",
            "indicator": "pua:toolbar",
            "title": "toolbar",
            "idempotency_key": "f1",
        }
        first = await client.post(f"/v1/scans/{scan_id}/findings", json=payload)
        second = await client.post(f"/v1/scans/{scan_id}/findings", json=payload)
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    async def test_disposition(self, client: httpx.AsyncClient) -> None:
        box = await self._box(client)
        scan_id = (await client.post(f"/v1/endpoints/{box['id']}/scans", json={})).json()["id"]
        await client.post(f"/v1/scans/{scan_id}/start")
        finding = (
            await client.post(
                f"/v1/scans/{scan_id}/findings",
                json={
                    "endpoint_id": box["id"],
                    "category": "pua",
                    "indicator": "pua:toolbar",
                    "title": "toolbar",
                },
            )
        ).json()
        resolved = await client.post(
            f"/v1/findings/{finding['id']}/disposition", json={"status": "resolved"}
        )
        assert resolved.json()["status"] == "resolved"


class TestOptimizer:
    async def test_propose_accept_apply_on_a_clean_miner(self, client: httpx.AsyncClient) -> None:
        box = (await client.post("/v1/endpoints", json=enrol())).json()
        proposed = await client.post(f"/v1/endpoints/{box['id']}/optimizer/plans", json={})
        assert proposed.status_code == 201
        plan = proposed.json()
        assert plan["category"] == "performance"
        assert plan["actions"]

        accepted = await client.post(f"/v1/optimizer/plans/{plan['id']}/accept")
        assert accepted.json()["status"] == "accepted"
        applied = await client.post(f"/v1/optimizer/plans/{plan['id']}/apply")
        assert applied.json()["status"] == "applied"
        assert all(action["status"] == "applied" for action in applied.json()["actions"])

    async def test_security_outranks_performance_when_malware_is_open(
        self, client: httpx.AsyncClient
    ) -> None:
        box = (await client.post("/v1/endpoints", json=enrol(hostname="rig-sec.site"))).json()
        scan_id = (await client.post(f"/v1/endpoints/{box['id']}/scans", json={})).json()["id"]
        await client.post(f"/v1/scans/{scan_id}/start")
        await client.post(
            f"/v1/scans/{scan_id}/findings",
            json={
                "endpoint_id": box["id"],
                "category": "malware",
                "indicator": "family:emotet",
                "title": "loader",
            },
        )
        plan = (await client.post(f"/v1/endpoints/{box['id']}/optimizer/plans", json={})).json()
        assert plan["category"] == "security"
        kinds = {action["kind"] for action in plan["actions"]}
        assert "av.update_signatures" in kinds
        assert "gpu.power_limit" not in kinds

    async def test_cannot_apply_performance_while_quarantined(
        self, client: httpx.AsyncClient
    ) -> None:
        box = (await client.post("/v1/endpoints", json=enrol(hostname="rig-iso.site"))).json()
        plan = (await client.post(f"/v1/endpoints/{box['id']}/optimizer/plans", json={})).json()
        await client.post(f"/v1/endpoints/{box['id']}/quarantine", json={"reason": "manual"})
        response = await client.post(f"/v1/optimizer/plans/{plan['id']}/apply")
        assert response.status_code == 409
        assert response.json()["code"] == "conflict"
