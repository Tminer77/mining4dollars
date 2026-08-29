"""The model's wire format, and the guardrails around writing it to disk."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.repair import patch as patch_module
from tools.repair.patch import (
    FileDelete,
    FileWrite,
    Patch,
    PatchError,
    apply_patch,
    parse_patch,
    resolve_target,
)


def block(path: str, body: str, *, fence: str = "```") -> str:
    return f"{fence}file:{path}\n{body}\n{fence}\n"


class TestParsing:
    def test_extracts_a_single_file(self) -> None:
        patch = parse_patch(block("src/m4d/a.py", "x = 1"))
        assert patch.changes == (FileWrite(path="src/m4d/a.py", content="x = 1\n"),)

    def test_ignores_prose_around_the_blocks(self) -> None:
        reply = f"The root cause is a typo.\n\n{block('a.py', 'x = 1')}\nThat should do it."
        assert parse_patch(reply).paths == ("a.py",)

    def test_keeps_declaration_order(self) -> None:
        patch = parse_patch(block("a.py", "1") + block("b.py", "2") + block("c.py", "3"))
        assert patch.paths == ("a.py", "b.py", "c.py")

    def test_reads_a_delete_directive(self) -> None:
        patch = parse_patch("```delete:src/m4d/gone.py\n```\n")
        assert patch.changes == (FileDelete(path="src/m4d/gone.py"),)

    def test_tolerates_a_language_hint_before_the_directive(self) -> None:
        """Models habitually label the fence; that should not cost an attempt."""
        assert parse_patch("```python file:a.py\nx = 1\n```\n").paths == ("a.py",)

    def test_preserves_blank_lines_and_indentation(self) -> None:
        body = "def f():\n    return 1\n\n\ndef g():\n    return 2"
        content = parse_patch(block("a.py", body)).changes[0]
        assert isinstance(content, FileWrite)
        assert content.content == body + "\n"

    def test_ends_every_file_with_a_newline(self) -> None:
        write = parse_patch(block("a.py", "x = 1")).changes[0]
        assert isinstance(write, FileWrite)
        assert write.content.endswith("\n")

    def test_writes_an_empty_file_as_empty(self) -> None:
        write = parse_patch("```file:a.py\n```\n").changes[0]
        assert isinstance(write, FileWrite)
        assert write.content == ""

    def test_a_longer_fence_can_carry_a_nested_fence(self) -> None:
        """Markdown and docs contain ``` themselves; the protocol must survive it."""
        body = "# Title\n\n```python\nx = 1\n```"
        patch = parse_patch(block("README.md", body, fence="````"))
        write = patch.changes[0]
        assert isinstance(write, FileWrite)
        assert write.content == body + "\n"

    def test_no_blocks_is_an_empty_patch_not_an_error(self) -> None:
        assert not parse_patch("I could not work out what is wrong.")

    def test_rejects_an_unterminated_block(self) -> None:
        with pytest.raises(PatchError, match="never closed"):
            parse_patch("```file:a.py\nx = 1\n")

    def test_rejects_the_same_path_twice(self) -> None:
        """Two writes to one path hide which one the model meant to keep."""
        with pytest.raises(PatchError, match="written twice"):
            parse_patch(block("a.py", "1") + block("a.py", "2"))

    def test_rejects_a_block_with_no_path(self) -> None:
        with pytest.raises(PatchError, match="names no path"):
            parse_patch("```file:\nx = 1\n```\n")


class TestPathSafety:
    def test_accepts_a_nested_repository_path(self, tmp_path: Path) -> None:
        assert resolve_target(tmp_path, "src/m4d/a.py") == tmp_path / "src/m4d/a.py"

    @pytest.mark.parametrize(
        "path",
        ["/etc/passwd", "~/.ssh/authorized_keys", "../outside.py", "src/../../outside.py"],
    )
    def test_rejects_paths_outside_the_repository(self, tmp_path: Path, path: str) -> None:
        with pytest.raises(PatchError):
            resolve_target(tmp_path, path)

    @pytest.mark.parametrize("path", [".git/config", ".venv/pyvenv.cfg", ".repair/outcome.json"])
    def test_rejects_infrastructure_the_loop_runs_on(self, tmp_path: Path, path: str) -> None:
        with pytest.raises(PatchError, match="may not modify"):
            resolve_target(tmp_path, path)

    def test_rejects_a_symlink_that_leads_out_of_the_tree(self, tmp_path: Path) -> None:
        """resolve() follows links, so a planted link is not a way out."""
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        root = tmp_path / "repo"
        root.mkdir()
        (root / "escape").symlink_to(outside, target_is_directory=True)
        with pytest.raises(PatchError, match="outside the repository"):
            resolve_target(root, "escape/secrets.txt")

    def test_rejects_the_root_itself(self, tmp_path: Path) -> None:
        with pytest.raises(PatchError, match="repository root"):
            resolve_target(tmp_path, ".")


class TestApplication:
    def test_writes_a_new_file(self, tmp_path: Path) -> None:
        apply_patch(tmp_path, Patch((FileWrite(path="a.py", content="x = 1\n"),)))
        assert (tmp_path / "a.py").read_text() == "x = 1\n"

    def test_overwrites_an_existing_file_wholesale(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("old and much longer\n")
        apply_patch(tmp_path, Patch((FileWrite(path="a.py", content="new\n"),)))
        assert (tmp_path / "a.py").read_text() == "new\n"

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        apply_patch(tmp_path, Patch((FileWrite(path="a/b/c.py", content="x = 1\n"),)))
        assert (tmp_path / "a/b/c.py").read_text() == "x = 1\n"

    def test_deletes_a_file(self, tmp_path: Path) -> None:
        (tmp_path / "gone.py").write_text("x = 1\n")
        apply_patch(tmp_path, Patch((FileDelete(path="gone.py"),)))
        assert not (tmp_path / "gone.py").exists()

    def test_deleting_an_absent_file_is_not_an_error(self, tmp_path: Path) -> None:
        apply_patch(tmp_path, Patch((FileDelete(path="never-existed.py"),)))

    def test_writes_nothing_when_any_path_is_rejected(self, tmp_path: Path) -> None:
        """All-or-nothing: one bad path must not leave the others applied."""
        patch = Patch(
            (
                FileWrite(path="good.py", content="x = 1\n"),
                FileWrite(path="../escape.py", content="x = 1\n"),
            )
        )
        with pytest.raises(PatchError):
            apply_patch(tmp_path, patch)
        assert not (tmp_path / "good.py").exists()

    def test_refuses_to_write_over_a_directory(self, tmp_path: Path) -> None:
        (tmp_path / "blocked.py").mkdir()
        with pytest.raises(PatchError, match="existing directory"):
            apply_patch(tmp_path, Patch((FileWrite(path="blocked.py", content="x\n"),)))

    def test_rolls_back_when_a_write_fails_partway_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A full disk mid-batch must not leave half the patch applied."""
        real_write = patch_module._atomic_write
        calls: list[Path] = []

        def failing_write(target: Path, content: str) -> None:
            calls.append(target)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            real_write(target, content)

        monkeypatch.setattr(patch_module, "_atomic_write", failing_write)
        patch = Patch(
            (
                FileWrite(path="first.py", content="x = 1\n"),
                FileWrite(path="second.py", content="x = 2\n"),
            )
        )
        with pytest.raises(PatchError, match="rolled back"):
            apply_patch(tmp_path, patch)
        assert not (tmp_path / "first.py").exists()
        assert not (tmp_path / "second.py").exists()

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        apply_patch(tmp_path, Patch((FileWrite(path="a.py", content="x = 1\n"),)), dry_run=True)
        assert not (tmp_path / "a.py").exists()

    def test_dry_run_still_validates_paths(self, tmp_path: Path) -> None:
        with pytest.raises(PatchError):
            apply_patch(tmp_path, Patch((FileWrite(path="/etc/x", content=""),)), dry_run=True)

    def test_reports_the_paths_it_touched(self, tmp_path: Path) -> None:
        applied = apply_patch(
            tmp_path,
            Patch((FileWrite(path="a.py", content="1\n"), FileWrite(path="b/c.py", content="2\n"))),
        )
        assert set(applied.paths) == {"a.py", "b/c.py"}


class TestRollback:
    def test_restores_previous_contents(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("original\n")
        applied = apply_patch(tmp_path, Patch((FileWrite(path="a.py", content="replaced\n"),)))
        applied.revert()
        assert (tmp_path / "a.py").read_text() == "original\n"

    def test_removes_files_the_patch_created(self, tmp_path: Path) -> None:
        applied = apply_patch(tmp_path, Patch((FileWrite(path="a/b.py", content="new\n"),)))
        applied.revert()
        assert not (tmp_path / "a/b.py").exists()

    def test_removes_directories_the_patch_created(self, tmp_path: Path) -> None:
        applied = apply_patch(tmp_path, Patch((FileWrite(path="a/b/c.py", content="new\n"),)))
        applied.revert()
        assert not (tmp_path / "a").exists()

    def test_leaves_pre_existing_directories_alone(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        applied = apply_patch(tmp_path, Patch((FileWrite(path="src/a.py", content="new\n"),)))
        applied.revert()
        assert (tmp_path / "src").is_dir()

    def test_restores_a_deleted_file(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("original\n")
        applied = apply_patch(tmp_path, Patch((FileDelete(path="a.py"),)))
        applied.revert()
        assert (tmp_path / "a.py").read_text() == "original\n"

    def test_never_raises_when_a_path_cannot_be_restored(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Rollback runs on the failure path; raising there would hide the cause."""
        applied = apply_patch(tmp_path, Patch((FileWrite(path="a.py", content="x\n"),)))
        (tmp_path / "a.py").unlink()
        (tmp_path / "a.py").mkdir()

        applied.revert()

        assert "could not restore" in caplog.text

    def test_marks_itself_reverted(self, tmp_path: Path) -> None:
        applied = apply_patch(tmp_path, Patch((FileWrite(path="a.py", content="x\n"),)))
        assert not applied.reverted
        applied.revert()
        assert applied.reverted
