"""Application wiring, checked without starting the lifespan."""

from __future__ import annotations

from m4d.api.app import create_app
from m4d.config import Environment, Settings
from tests.conftest import UNIT_TEST_DSN


def build(**overrides: object) -> Settings:
    """Settings for an app under test."""
    return Settings(database_url=UNIT_TEST_DSN, log_format="console", **overrides)  # type: ignore[arg-type]


class TestRoutes:
    def test_registers_every_endpoint(self) -> None:
        paths = {route.path for route in create_app(build()).routes if hasattr(route, "path")}
        # Included routers may be nested, so fall back to the OpenAPI document,
        # which is the flattened public surface either way.
        schema_paths = set(create_app(build()).openapi()["paths"])

        assert {
            "/healthz",
            "/readyz",
            "/v1/events",
            "/v1/events/{event_id}",
            "/v1/protocol/bootstrap",
            "/v1/protocol/terms",
            "/v1/protocol/interpret",
            "/v1/protocol/nodes",
            "/v1/protocol/tape",
            "/v1/protocol/head",
            "/v1/protocol/tree",
        } <= (paths | schema_paths)

    def test_documents_both_create_outcomes(self) -> None:
        """201 for a new event and 200 for a replay must both be discoverable."""
        create = create_app(build()).openapi()["paths"]["/v1/events"]["post"]["responses"]
        assert {"200", "201"} <= create.keys()


class TestDocumentationExposure:
    def test_docs_are_served_outside_production(self) -> None:
        app = create_app(build(environment=Environment.LOCAL))
        assert app.docs_url == "/docs"
        assert app.openapi_url == "/openapi.json"

    def test_docs_are_withheld_in_production(self) -> None:
        """Interactive docs describe internals; production should not publish them."""
        app = create_app(build(environment=Environment.PRODUCTION))
        assert app.docs_url is None
        assert app.openapi_url is None


class TestState:
    def test_settings_are_available_before_lifespan_runs(self) -> None:
        """Dependencies read app.state, so it must be populated eagerly."""
        settings = build()
        assert create_app(settings).state.settings is settings

    def test_cors_is_absent_by_default(self) -> None:
        """CORS is opt-in; an unconfigured service should not relax origins."""
        app = create_app(build())
        assert not any("CORS" in str(middleware.cls) for middleware in app.user_middleware)

    def test_cors_is_installed_when_origins_are_configured(self) -> None:
        app = create_app(build(cors_origins="https://app.example"))
        assert any("CORS" in str(middleware.cls) for middleware in app.user_middleware)
