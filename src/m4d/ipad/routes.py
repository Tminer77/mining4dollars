"""HTTP routes that deliver the iPad console.

Kept out of the OpenAPI document: this is a product surface, not an API.
The service worker and the web manifest live at the site root so they can
claim ``/`` — that is what lets Safari install the app on the home screen
and keep the shell available offline.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from m4d.ipad.paths import static_directory

__all__ = ["mount_ipad"]

_NO_STORE = {"Cache-Control": "no-cache"}

router = APIRouter(include_in_schema=False)


def _file(name: str, media_type: str, headers: dict[str, str] | None = None) -> FileResponse:
    """Serve one packaged file with a stable media type."""
    return FileResponse(static_directory() / name, media_type=media_type, headers=headers)


@router.get("/")
async def console() -> FileResponse:
    """The iPad app shell."""
    return _file("index.html", "text/html; charset=utf-8", _NO_STORE)


@router.get("/manifest.webmanifest")
async def manifest() -> FileResponse:
    """Web app manifest used by Add to Home Screen."""
    return _file("manifest.webmanifest", "application/manifest+json", _NO_STORE)


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """Service worker. Scope is this directory so GitHub Pages can host it too."""
    return _file("sw.js", "application/javascript; charset=utf-8", _NO_STORE)


@router.get("/app.css")
async def stylesheet() -> FileResponse:
    """Console stylesheet, same file as ``/ipad/app.css``."""
    return _file("app.css", "text/css; charset=utf-8")


@router.get("/app.js")
async def script() -> FileResponse:
    """Console script, same file as ``/ipad/app.js``."""
    return _file("app.js", "application/javascript; charset=utf-8")


@router.get("/store.js")
async def store() -> FileResponse:
    """On-device store, same file as ``/ipad/store.js``."""
    return _file("store.js", "application/javascript; charset=utf-8")


@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon() -> FileResponse:
    """Icon Safari requests when adding the app to the home screen."""
    return FileResponse(static_directory() / "icons" / "icon-180.png", media_type="image/png")


def mount_ipad(app: FastAPI) -> None:
    """Attach the console to ``app``.

    Call after the API routers so ``/healthz``, ``/readyz``, and ``/v1`` keep
    their handlers. The ``/ipad`` mount is last and only answers asset paths
    that nothing else claimed.
    """
    app.include_router(router)
    root = static_directory()
    app.mount("/icons", StaticFiles(directory=root / "icons"), name="ipad-icons")
    app.mount("/ipad", StaticFiles(directory=root), name="ipad-assets")
