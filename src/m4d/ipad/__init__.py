"""iPad operator console.

The installable home-screen app served at ``/``. It is a delivery surface,
not a domain: it talks to the same HTTP API every other client uses.
"""

from __future__ import annotations

from m4d.ipad.routes import mount_ipad

__all__ = ["mount_ipad"]
