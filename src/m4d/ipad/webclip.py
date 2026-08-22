"""Home-screen web clip so INNER can be installed on an iPad."""

from __future__ import annotations

import plistlib
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette.responses import Response

from m4d.ipad.paths import static_directory

__all__ = ["webclip_response"]

PROFILE_UUID = "6f2c1a90-4e3b-4d7a-9c11-a1b2c3d4e5f6"
CLIP_UUID = "7a3d2b01-5f4c-4e8b-8d22-b2c3d4e5f607"
MEDIA_TYPE = "application/x-apple-aspen-config"
_INNER_PATHS = {"", "/", "/inner", "/inner.html"}


def _hosts(request: Request) -> set[str]:
    """Hosts this request is allowed to represent, including proxies."""
    found = {request.url.netloc, request.headers.get("host", "")}
    forwarded = request.headers.get("x-forwarded-host", "")
    if forwarded:
        found.add(forwarded.split(",")[0].strip())
    return {host for host in found if host}


def public_start_url(request: Request, start: str | None) -> str:
    """Return the INNER URL the home-screen icon should open."""
    hosts = _hosts(request)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", request.url.netloc).split(",")[0].strip()
    default = f"{proto}://{host}/inner.html"
    if not start:
        return default

    parsed = urlparse(start)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="start URL must be http(s)")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="start URL must not carry credentials")
    if parsed.path not in _INNER_PATHS:
        raise HTTPException(status_code=400, detail="start URL must be INNER")
    if parsed.netloc not in hosts:
        raise HTTPException(status_code=400, detail="start URL must be this origin")
    return f"{parsed.scheme}://{parsed.netloc}/inner.html"


def build_profile(start_url: str) -> bytes:
    """Build an unsigned iOS configuration profile that adds INNER."""
    icon = (static_directory() / "icons" / "icon-180.png").read_bytes()
    payload = {
        "PayloadDisplayName": "INNER",
        "PayloadDescription": "Adds INNER to this iPad home screen.",
        "PayloadIdentifier": "com.mining4dollars.inner",
        "PayloadOrganization": "mining4dollars",
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": PROFILE_UUID,
        "PayloadVersion": 1,
        "PayloadContent": [
            {
                "FullScreen": True,
                "Icon": icon,
                "IsRemovable": True,
                "Label": "INNER",
                "PayloadDescription": "INNER home screen app",
                "PayloadDisplayName": "INNER",
                "PayloadIdentifier": "com.mining4dollars.inner.webclip",
                "PayloadType": "com.apple.webClip.managed",
                "PayloadUUID": CLIP_UUID,
                "PayloadVersion": 1,
                "Precomposed": True,
                "URL": start_url,
            }
        ],
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def webclip_response(request: Request, start: str | None) -> Response:
    """Serve the profile Safari uses to put INNER on the home screen."""
    body = build_profile(public_start_url(request, start))
    return Response(
        content=body,
        media_type=MEDIA_TYPE,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="INNER.mobileconfig"',
        },
    )
