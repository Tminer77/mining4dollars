"""The iPad console is served as a same-origin web app."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from m4d.api.app import create_app
from m4d.config import Environment, Settings
from tests.conftest import UNIT_TEST_DSN


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client against the app without opening a database."""
    app = create_app(
        Settings(
            environment=Environment.TEST,
            database_url=UNIT_TEST_DSN,
            log_format="console",
            log_level="WARNING",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as session:
        yield session


class TestConsoleShell:
    async def test_root_is_the_ipad_app(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        body = response.text
        assert "apple-mobile-web-app-capable" in body
        assert 'rel="manifest"' in body
        assert "Add to Home Screen" in body
        assert "this iPad" in body
        assert "/ipad/store.js" in body
        assert "Search this iPad" in body
        assert 'data-view="settings"' in body

    async def test_manifest_is_standalone(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/manifest.webmanifest")

        assert response.status_code == 200
        assert "manifest" in response.headers["content-type"]
        manifest = response.json()
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "/"
        assert manifest["short_name"] == "M4D"

    async def test_service_worker_is_allowed_at_root(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/sw.js")

        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]
        assert response.headers["service-worker-allowed"] == "/"
        assert "m4d-ipad-v3" in response.text
        assert "/ipad/store.js" in response.text

    async def test_apple_touch_icon_is_a_png(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/apple-touch-icon.png")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_assets_are_served_under_ipad(self, client: httpx.AsyncClient) -> None:
        css = await client.get("/ipad/app.css")
        js = await client.get("/ipad/app.js")
        store = await client.get("/ipad/store.js")

        assert css.status_code == 200
        assert js.status_code == 200
        assert store.status_code == 200
        assert "safe-area-inset" in css.text
        assert "serviceWorker" in js.text
        assert "indexedDB" in store.text
        assert "M4DStore" in store.text
        assert "exportBundle" in store.text
        assert "kept on this iPad" in js.text
        assert "navigator.share" in js.text


class TestConsoleDoesNotStealTheApi:
    async def test_health_route_is_still_the_probe(self, client: httpx.AsyncClient) -> None:
        """``/`` is the app; ``/healthz`` must remain a JSON probe."""
        response = await client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    def test_console_is_absent_from_openapi(self) -> None:
        schema = create_app(Settings(database_url=UNIT_TEST_DSN, log_format="console")).openapi()
        assert "/" not in schema["paths"]
        assert "/sw.js" not in schema["paths"]
