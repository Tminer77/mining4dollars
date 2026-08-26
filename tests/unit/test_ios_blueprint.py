"""iOS Blueprint is a real product surface: home screen, Files download, live app."""

from __future__ import annotations

import httpx

from m4d.api.app import create_app
from tests.unit.test_app_composition import build


def _client() -> httpx.AsyncClient:
    app = create_app(build())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestIosBlueprint:
    async def test_root_launches_the_app(self) -> None:
        async with _client() as client:
            response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "apple-mobile-web-app-capable" in response.text
        assert "Blueprint" in response.text

    async def test_html_download_goes_to_files(self) -> None:
        async with _client() as client:
            response = await client.get("/ios/download")
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        assert "Blueprint.html" in disposition
        assert "Launch Blueprint" in response.text

    async def test_mobileconfig_is_an_ios_profile(self) -> None:
        async with _client() as client:
            response = await client.get("/ios/install")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-apple-aspen-config")
        assert "attachment" in response.headers["content-disposition"]
        assert "Blueprint.mobileconfig" in response.headers["content-disposition"]
        assert b"com.apple.webClip.managed" in response.content
        assert b"Blueprint" in response.content

    async def test_manifest_is_standalone(self) -> None:
        async with _client() as client:
            response = await client.get("/manifest.webmanifest")
        assert response.status_code == 200
        body = response.json()
        assert body["display"] == "standalone"
        assert body["short_name"] == "Blueprint"

    async def test_home_screen_icon_is_png(self) -> None:
        async with _client() as client:
            response = await client.get("/apple-touch-icon.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
