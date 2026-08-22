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
    async def test_root_launches_inner(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/")

        assert response.status_code == 200
        assert "INNER" in response.text
        assert "Keep on this iPad" in response.text
        assert "Add to Home Screen" in response.text
        assert "apple-mobile-web-app-capable" in response.text

    async def test_library_is_at_index(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/index.html")

        assert response.status_code == 200
        assert "iPad apps" in response.text
        assert "apps.json" in response.text

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
        assert ".sheet[hidden]" in css.text
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
        assert "Keep on this iPad" in page.text
        assert "Add to Home Screen" in page.text
        assert 'id="install-chip"' in page.text
        assert 'src="icons/icon-180.png"' in page.text
        assert 'id="splash"' in page.text
        assert "inner-icon" in page.text
        assert "inner-dock" in page.text
        assert 'id="install-profile"' in page.text
        assert "inner.mobileconfig" in page.text
        assert "bindInstall" in script.text or "install-chip" in script.text
        assert "inner.mobileconfig" in script.text


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
        assert manifest["start_url"] == "./inner.html"
        assert manifest["scope"] == "./"
        assert manifest["name"] == "INNER"
        assert manifest["short_name"] == "INNER"

    async def test_service_worker_caches_every_app(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/sw.js")

        assert response.status_code == 200
        assert "m4d-ipad-v11" in response.text
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


class TestHomeScreenProfile:
    async def test_profile_puts_inner_on_the_home_screen(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/inner.mobileconfig")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-apple-aspen-config")
        body = response.text
        assert "com.apple.webClip.managed" in body
        assert "INNER" in body
        assert "http://testserver/inner.html" in body
        assert "INNER.mobileconfig" in response.headers.get("content-disposition", "")

    async def test_profile_uses_the_public_host(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/inner.mobileconfig",
            headers={
                "host": "donor-implies-ips-voted.trycloudflare.com",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "donor-implies-ips-voted.trycloudflare.com",
            },
        )

        assert response.status_code == 200
        assert "https://donor-implies-ips-voted.trycloudflare.com/inner.html" in response.text

    async def test_profile_accepts_a_same_origin_start(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/inner.mobileconfig",
            params={"start": "http://testserver/inner.html"},
        )

        assert response.status_code == 200
        assert "http://testserver/inner.html" in response.text

    async def test_profile_rejects_a_foreign_start(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/inner.mobileconfig",
            params={"start": "https://evil.example/inner.html"},
        )

        assert response.status_code == 400


class TestConsoleDoesNotStealTheApi:
    async def test_health_route_is_still_the_probe(self, client: httpx.AsyncClient) -> None:
        """``/`` is INNER; ``/healthz`` must remain a JSON probe."""
        response = await client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    def test_console_is_absent_from_openapi(self) -> None:
        schema = create_app(Settings(database_url=UNIT_TEST_DSN, log_format="console")).openapi()
        assert "/" not in schema["paths"]
        assert "/sw.js" not in schema["paths"]
        assert "/inner.mobileconfig" not in schema["paths"]
