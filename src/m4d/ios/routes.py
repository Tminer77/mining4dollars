"""HTTP routes that deliver Blueprint as an iPad / iOS app."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, HTMLResponse, Response

from m4d.ios.icons import write_icons
from m4d.ios.paths import static_directory
from m4d.ios.webclip import launcher_html, webclip_response

__all__ = ["mount_ios"]

_NO_STORE = {"Cache-Control": "no-cache"}

router = APIRouter(include_in_schema=False)


def _file(name: str, media_type: str, headers: dict[str, str] | None = None) -> FileResponse:
    return FileResponse(static_directory() / name, media_type=media_type, headers=headers)


async def blueprint_app() -> FileResponse:
    """Launch Blueprint — the iOS mining-for-dollars app."""
    return _file("blueprint.html", "text/html; charset=utf-8", _NO_STORE)


async def download_html(request: Request) -> HTMLResponse:
    """Save Blueprint.html into Files, then open it to launch the app."""
    return HTMLResponse(
        content=launcher_html(request),
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="Blueprint.html"',
        },
    )


async def download_webclip(request: Request, start: str | None = None) -> Response:
    """iOS profile that puts Blueprint on the home screen."""
    return webclip_response(request, start)


async def manifest() -> FileResponse:
    return _file("manifest.webmanifest", "application/manifest+json", _NO_STORE)


async def service_worker() -> FileResponse:
    return _file("sw.js", "application/javascript; charset=utf-8", _NO_STORE)


async def stylesheet() -> FileResponse:
    return _file("blueprint.css", "text/css; charset=utf-8")


async def script() -> FileResponse:
    return _file("blueprint.js", "application/javascript; charset=utf-8")


async def apple_touch_icon() -> FileResponse:
    return FileResponse(static_directory() / "icons" / "icon-180.png", media_type="image/png")


def _get(path: str, endpoint: Callable[..., object]) -> None:
    router.add_api_route(path, endpoint, methods=["GET"], include_in_schema=False)


def mount_ios(app: FastAPI) -> None:
    """Attach the iOS app after API routers so /v1 and probes keep their handlers."""
    write_icons(static_directory() / "icons")
    for path in ("/", "/ios", "/ios/", "/ios/blueprint.html"):
        _get(path, blueprint_app)
    _get("/ios/download", download_html)
    _get("/ios/Blueprint.html", download_html)
    _get("/ios/Blueprint.mobileconfig", download_webclip)
    _get("/ios/install", download_webclip)
    _get("/manifest.webmanifest", manifest)
    _get("/ios/manifest.webmanifest", manifest)
    _get("/sw.js", service_worker)
    _get("/ios/sw.js", service_worker)
    _get("/ios/blueprint.css", stylesheet)
    _get("/ios/blueprint.js", script)
    _get("/apple-touch-icon.png", apple_touch_icon)
    _get("/apple-touch-icon-precomposed.png", apple_touch_icon)
    _get("/ios/apple-touch-icon.png", apple_touch_icon)
    app.include_router(router)
    app.mount("/ios/icons", StaticFiles(directory=static_directory() / "icons"), name="ios-icons")
