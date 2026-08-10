"""Liveness and readiness endpoints.

The behaviour under test is the separation between the two: a database outage
must take the instance out of the load balancer without also convincing the
orchestrator to restart it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from m4d import __version__
from m4d.api.app import create_app
from m4d.config import Environment, Settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def broken_client() -> AsyncIterator[httpx.AsyncClient]:
    """A client whose application points at a database that is not there.

    Port 1 is reserved and never listening, so the connection fails fast and
    deterministically without depending on a firewall's drop behaviour.
    """
    settings = Settings(
        environment=Environment.TEST,
        database_url="postgresql+asyncpg://postgres@127.0.0.1:1/absent",
        db_connect_timeout_seconds=0.5,
        log_format="console",
        log_level="CRITICAL",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as session:
            yield session


class TestLiveness:
    async def test_reports_alive(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "alive", "version": __version__}

    async def test_stays_alive_when_the_database_is_down(
        self, broken_client: httpx.AsyncClient
    ) -> None:
        """Liveness must not depend on a dependency.

        If it did, a brief database outage would restart every instance at once
        and turn a recoverable incident into a cold-start stampede.
        """
        assert (await broken_client.get("/healthz")).status_code == 200


class TestReadiness:
    async def test_reports_ready_when_the_database_answers(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/readyz")

        assert response.status_code == 200
        report = response.json()
        assert report["status"] == "healthy"
        assert report["checks"][0]["name"] == "database"
        assert report["checks"][0]["healthy"] is True

    async def test_measures_dependency_latency(self, client: httpx.AsyncClient) -> None:
        report = (await client.get("/readyz")).json()
        assert report["checks"][0]["latency_ms"] >= 0

    async def test_reports_503_when_the_database_is_unreachable(
        self, broken_client: httpx.AsyncClient
    ) -> None:
        response = await broken_client.get("/readyz")

        assert response.status_code == 503
        report = response.json()
        assert report["status"] == "unhealthy"
        assert report["checks"][0]["healthy"] is False
        assert report["checks"][0]["error"]

    async def test_failure_is_a_report_not_a_crash(self, broken_client: httpx.AsyncClient) -> None:
        """A prober must get an interpretable body, never a 500."""
        response = await broken_client.get("/readyz")
        assert response.status_code == 503
        assert "checks" in response.json()
