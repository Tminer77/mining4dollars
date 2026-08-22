"""The iPad apps are served as a same-origin workshop."""

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


class TestAppLibrary:
    async def test_root_is_the_ipad_app_library(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        body = response.text
        assert "apple-mobile-web-app-capable" in body
        assert "iPad apps" in body
        assert "apps.json" in body
        assert "Add to Home Screen" in body

    async def test_catalog_lists_console_and_notes(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/apps.json")

        assert response.status_code == 200
        catalog = response.json()
        ids = {app["id"] for app in catalog}
        assert {"console", "notes", "template", "inner"} <= ids


class TestConsoleApp:
    async def test_console_is_the_operator_log(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/console.html")

        assert response.status_code == 200
        body = response.text
        assert "Search this iPad" in body
        assert 'src="store.js"' in body
        assert 'data-view="settings"' in body

    async def test_assets_are_served(self, client: httpx.AsyncClient) -> None:
        css = await client.get("/app.css")
        js = await client.get("/app.js")
        store = await client.get("/store.js")

        assert css.status_code == 200
        assert js.status_code == 200
        assert store.status_code == 200
        assert "safe-area-inset" in css.text
        assert "serviceWorker" in js.text
        assert "allNotes" in store.text
        assert "navigator.share" in js.text


class TestInnerApp:
    async def test_inner_is_a_live_wire_display(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/inner.html")
        script = await client.get("/inner.js")

        assert page.status_code == 200
        assert "wire" in page.text.lower() or "INNER" in page.text
        assert script.status_code == 200
        assert "requestAnimationFrame" in script.text
        assert "icosahedron" in script.text or "ICO" in script.text
        assert "HTTP" in script.text and "DOMAIN" in script.text
        assert "hardwareConcurrency" in script.text


class TestNotesApp:
    async def test_notes_stay_on_this_ipad(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/notes.html")
        script = await client.get("/notes.js")

        assert page.status_code == 200
        assert "Field notes" in page.text or "Notes" in page.text
        assert script.status_code == 200
        assert "putNote" in script.text


class TestInstallSurface:
    async def test_manifest_is_standalone(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/manifest.webmanifest")

        assert response.status_code == 200
        manifest = response.json()
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "./"
        assert manifest["scope"] == "./"
        assert manifest["short_name"] == "M4D"

    async def test_service_worker_caches_every_app(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/sw.js")

        assert response.status_code == 200
        assert "m4d-ipad-v6" in response.text
        assert "./console.html" in response.text
        assert "./notes.html" in response.text
        assert "./inner.html" in response.text

    async def test_apple_touch_icon_is_a_png(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/apple-touch-icon.png")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_template_explains_how_to_add_an_app(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/template.html")

        assert response.status_code == 200
        assert "apps.json" in response.text
        assert "M4DStore" in response.text


class TestConsoleDoesNotStealTheApi:
    async def test_health_route_is_still_the_probe(self, client: httpx.AsyncClient) -> None:
        """``/`` is the app library; ``/healthz`` must remain a JSON probe."""
        response = await client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    def test_console_is_absent_from_openapi(self) -> None:
        schema = create_app(Settings(database_url=UNIT_TEST_DSN, log_format="console")).openapi()
        assert "/" not in schema["paths"]
        assert "/sw.js" not in schema["paths"]
