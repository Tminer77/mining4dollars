"""CLI behaviour."""

from __future__ import annotations

import pytest

from m4d import __version__
from m4d.cli import main, redact_credentials


class TestRedactCredentials:
    def test_masks_a_password(self) -> None:
        assert (
            redact_credentials("postgresql+asyncpg://user:hunter2@db:5432/m4d")
            == "postgresql+asyncpg://user:***@db:5432/m4d"
        )

    def test_leaves_a_url_without_a_password_alone(self) -> None:
        url = "postgresql+asyncpg://postgres@127.0.0.1:5432/m4d"
        assert redact_credentials(url) == url

    def test_masks_a_password_containing_symbols(self) -> None:
        masked = redact_credentials("postgresql+asyncpg://u:p%40ss-w0rd!@db:5432/m4d")
        assert "p%40ss-w0rd!" not in masked
        assert masked.endswith("@db:5432/m4d")

    def test_keeps_the_username_visible(self) -> None:
        """The username is useful when diagnosing a permissions problem."""
        assert "svc_api" in redact_credentials("postgresql+asyncpg://svc_api:s3cret@db/m4d")


class TestParser:
    def test_reports_the_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exit_info:
            main(["--version"])
        assert exit_info.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_requires_a_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_rejects_an_unknown_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            main(["teleport"])


class TestConfigCommand:
    def test_prints_settings_with_the_password_masked(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from m4d.config import get_settings

        monkeypatch.setenv("M4D_DATABASE_URL", "postgresql+asyncpg://u:topsecret@db:5432/m4d")
        get_settings.cache_clear()
        try:
            assert main(["config"]) == 0
            output = capsys.readouterr().out
        finally:
            get_settings.cache_clear()

        assert "topsecret" not in output
        assert "database_url=postgresql+asyncpg://u:***@db:5432/m4d" in output
        assert "app_name=mining4dollars" in output
