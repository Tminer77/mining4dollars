"""Command line entry point.

Exposed as the ``m4d`` console script.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence

from m4d import __version__

__all__ = ["main", "redact_credentials"]

#: Matches the ``user:password@`` portion of a URL so it can be masked.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z+]+)://(?P<user>[^:/@\s]+):[^@\s]+@")


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(prog="m4d", description="mining4dollars platform CLI")
    parser.add_argument("--version", action="version", version=f"m4d {__version__}")

    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Run the HTTP API server.")
    serve.add_argument("--host", default=None, help="Override the configured bind address.")
    serve.add_argument("--port", type=int, default=None, help="Override the configured port.")
    serve.add_argument(
        "--reload",
        action="store_true",
        help="Restart on source changes. Development only.",
    )

    subcommands.add_parser("config", help="Print the resolved configuration and exit.")

    return parser


def _serve(host: str | None, port: int | None, reload: bool) -> int:
    """Run the API under uvicorn."""
    import uvicorn  # imported lazily so `m4d config` does not pay for it

    from m4d.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "m4d.api.app:create_app",
        factory=True,
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_config=None,  # logging is configured by the application itself
    )
    return 0


def redact_credentials(value: str) -> str:
    """Mask the password in a URL, if it has one.

    Matches on the URL's own structure rather than reading a ``password``
    attribute: ``PostgresDsn`` is a multi-host URL type and does not expose one.
    """
    return _URL_CREDENTIALS.sub(r"\g<scheme>://\g<user>:***@", value)


def _print_config() -> int:
    """Print the resolved configuration.

    This command is the thing people paste into issues and chat, so the
    database password is masked on the way out.
    """
    from m4d.config import get_settings

    settings = get_settings()
    for name, value in sorted(settings.model_dump().items()):
        rendered = redact_credentials(str(value)) if name == "database_url" else value
        print(f"{name}={rendered}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    args = _build_parser().parse_args(argv)

    if args.command == "serve":
        return _serve(args.host, args.port, args.reload)
    if args.command == "config":
        return _print_config()

    return 1  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
