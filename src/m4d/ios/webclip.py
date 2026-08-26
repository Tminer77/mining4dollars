"""Unsigned iOS configuration profile: Blueprint on the home screen."""

from __future__ import annotations

import plistlib
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette.responses import Response

from m4d.ios.paths import static_directory

__all__ = ["launcher_html", "public_start_url", "webclip_response"]

PROFILE_UUID = "a4e8c2b1-9d70-4f3a-8c55-1f6e0d2a9b11"
CLIP_UUID = "b5f9d3c2-0e81-504b-9d66-2a7f1e3b0c22"
MEDIA_TYPE = "application/x-apple-aspen-config"
_START_PATHS = {"", "/", "/ios", "/ios/", "/ios/blueprint.html"}


def _hosts(request: Request) -> set[str]:
    found = {request.url.netloc, request.headers.get("host", "")}
    forwarded = request.headers.get("x-forwarded-host", "")
    if forwarded:
        found.add(forwarded.split(",")[0].strip())
    return {host for host in found if host}


def public_origin(request: Request) -> str:
    """This request's public origin, honouring reverse-proxy headers."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def public_start_url(request: Request, start: str | None = None) -> str:
    """HTTPS URL the home-screen icon should open."""
    origin = public_origin(request)
    default = f"{origin}/"
    if not start:
        return default
    parsed = urlparse(start)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="start URL must be http(s)")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="start URL must not carry credentials")
    if parsed.path not in _START_PATHS:
        raise HTTPException(status_code=400, detail="start URL must be Blueprint")
    if parsed.netloc not in _hosts(request):
        raise HTTPException(status_code=400, detail="start URL must be this origin")
    return f"{parsed.scheme}://{parsed.netloc}/"


def build_profile(start_url: str) -> bytes:
    """Build an unsigned web-clip profile named Blueprint."""
    icon = (static_directory() / "icons" / "icon-180.png").read_bytes()
    payload = {
        "PayloadDisplayName": "Blueprint",
        "PayloadDescription": "Adds Blueprint to this iPad home screen.",
        "PayloadIdentifier": "com.mining4dollars.blueprint",
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
                "Label": "Blueprint",
                "PayloadDescription": "Blueprint home screen app",
                "PayloadDisplayName": "Blueprint",
                "PayloadIdentifier": "com.mining4dollars.blueprint.webclip",
                "PayloadType": "com.apple.webClip.managed",
                "PayloadUUID": CLIP_UUID,
                "PayloadVersion": 1,
                "Precomposed": True,
                "URL": start_url,
            }
        ],
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def webclip_response(request: Request, start: str | None = None) -> Response:
    """Serve the profile Safari / Files uses to install Blueprint."""
    body = build_profile(public_start_url(request, start))
    return Response(
        content=body,
        media_type=MEDIA_TYPE,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="Blueprint.mobileconfig"',
        },
    )


def launcher_html(request: Request) -> str:
    """A Files-friendly HTML that opens the live iOS app in Safari."""
    origin = public_origin(request)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Blueprint">
  <title>Blueprint</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0; min-height: 100dvh; display: grid; place-items: center;
      font-family: ui-rounded, system-ui, sans-serif; background: #12100c; color: #f4e7c3;
      padding: env(safe-area-inset-top) 24px env(safe-area-inset-bottom);
    }}
    a {{
      display: inline-block; padding: 16px 28px; border-radius: 16px;
      background: #d4af37; color: #12100c; font-weight: 700; text-decoration: none;
      font-size: 20px;
    }}
    p {{ opacity: .7; text-align: center; }}
  </style>
  <meta http-equiv="refresh" content="0;url={origin}/">
</head>
<body>
  <main>
    <p>Opening Blueprint…</p>
    <p><a href="{origin}/">Launch Blueprint</a></p>
  </main>
</body>
</html>
"""
