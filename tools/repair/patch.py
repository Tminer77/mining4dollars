"""The wire format the model answers in, and how it reaches disk.

A repair loop is only as safe as the step that writes files. Free-form prose
cannot be applied, and a diff the model composes from memory cannot be trusted
to apply cleanly, so the protocol asks for whole files inside fenced blocks
whose info string names the path::

    ```file:src/m4d/config.py
    ...the complete new contents...
    ```

    ```delete:src/m4d/obsolete.py
    ```

Whole-file replacement is the one form that either parses or does not; there is
no partial-application failure mode where half a hunk lands. The cost is tokens,
which is the right trade when the alternative is a corrupted working tree.

Every write is validated against the repository root before anything is opened,
and the batch is applied through a snapshot that can restore the previous state,
so a failure halfway through a multi-file patch does not leave a mixed tree.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "AppliedPatch",
    "Change",
    "FileDelete",
    "FileWrite",
    "Patch",
    "PatchError",
    "apply_patch",
    "parse_patch",
    "resolve_target",
]

logger = logging.getLogger(__name__)

#: Path prefixes a patch may never touch. Version control metadata and the
#: virtualenv are infrastructure the loop runs on top of; letting a model
#: rewrite them turns a bad suggestion into an unrecoverable one.
FORBIDDEN_ROOTS = frozenset({".git", ".venv", ".repair"})

#: Opens a block. The fence run length is captured because the closing fence
#: must be at least as long, which is what lets a file containing ``` be sent
#: inside a longer fence.
_OPENING_FENCE = re.compile(
    r"^(?P<fence>`{3,})[ \t]*"
    r"(?:[A-Za-z0-9_+.-]+[ \t]+)?"  # tolerate a language hint before the directive
    r"(?P<directive>file|delete)[ \t]*:[ \t]*"
    r"(?P<path>.*?)[ \t]*$"
)


class PatchError(Exception):
    """The model's reply could not be turned into a set of file changes.

    Raised for both malformed output and paths the loop refuses to write. The
    message is fed back to the model verbatim, so it reads as an instruction.
    """


@dataclass(frozen=True, slots=True)
class FileWrite:
    """Replace ``path`` with ``content`` in full, creating it if absent."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class FileDelete:
    """Remove ``path`` if it exists."""

    path: str


Change = FileWrite | FileDelete


@dataclass(frozen=True, slots=True)
class Patch:
    """An ordered set of changes, at most one per path."""

    changes: tuple[Change, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.changes)

    @property
    def paths(self) -> tuple[str, ...]:
        """Every path the patch touches, in the order it was declared."""
        return tuple(change.path for change in self.changes)


def parse_patch(reply: str) -> Patch:
    """Extract the file changes from a model reply.

    Text outside fenced blocks is ignored; the model is free to explain itself.

    Raises:
        PatchError: if a block is unterminated, names no path, or a path is
            claimed twice. A patch that touches one path twice is ambiguous
            about which write is meant to survive, and guessing there is how a
            loop silently discards work.
    """
    lines = reply.splitlines()
    changes: list[Change] = []
    seen: dict[str, int] = {}
    index = 0

    while index < len(lines):
        match = _OPENING_FENCE.match(lines[index])
        if match is None:
            index += 1
            continue

        fence = match["fence"]
        directive = match["directive"]
        path = match["path"].strip().strip("`").strip()
        if not path:
            raise PatchError(f"Block opened at line {index + 1} names no path.")

        closing = re.compile(rf"^`{{{len(fence)},}}[ \t]*$")
        body: list[str] = []
        index += 1
        while index < len(lines) and not closing.match(lines[index]):
            body.append(lines[index])
            index += 1

        if index >= len(lines):
            raise PatchError(
                f"Block for {path!r} is never closed. Close every block with a run of "
                f"at least {len(fence)} backticks on a line of its own."
            )
        index += 1  # step over the closing fence

        if path in seen:
            raise PatchError(
                f"{path!r} is written twice (blocks {seen[path]} and {len(changes) + 1}). "
                "Send one block per file, containing its final contents."
            )
        seen[path] = len(changes) + 1

        if directive == "delete":
            changes.append(FileDelete(path=path))
        else:
            # splitlines() dropped the trailing newline every text file wants.
            content = "".join(f"{line}\n" for line in body)
            changes.append(FileWrite(path=path, content=content))

    return Patch(changes=tuple(changes))


def resolve_target(root: Path, path: str) -> Path:
    """Resolve ``path`` inside ``root``, refusing anything that escapes it.

    Symlinks are resolved before the containment check, so a link planted inside
    the tree cannot be used as a door out of it.

    Raises:
        PatchError: if the path is absolute, walks upwards, lands outside the
            repository, or targets one of :data:`FORBIDDEN_ROOTS`.
    """
    candidate = Path(path)
    if candidate.is_absolute() or path.startswith("~"):
        raise PatchError(f"{path!r} is not repository-relative.")
    if any(part == ".." for part in candidate.parts):
        raise PatchError(f"{path!r} walks outside the repository.")
    if candidate.parts and candidate.parts[0] in FORBIDDEN_ROOTS:
        raise PatchError(f"{path!r} is under a directory the repair loop may not modify.")

    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PatchError(f"{path!r} resolves outside the repository.")
    if resolved == resolved_root:
        raise PatchError(f"{path!r} is the repository root, not a file.")
    if resolved.is_dir():
        raise PatchError(f"{path!r} is an existing directory, not a file.")
    return resolved


@dataclass(slots=True)
class AppliedPatch:
    """The previous state of everything a patch touched.

    Held so the batch can be undone as a unit — on a write that fails partway
    through, or on a caller's decision that the attempt made things worse.
    """

    root: Path
    #: path -> contents before the patch, or ``None`` if it did not exist.
    _snapshots: dict[Path, bytes | None] = field(default_factory=dict)
    #: Directories the patch created, deepest first, for cleanup on revert.
    _created_dirs: list[Path] = field(default_factory=list)
    reverted: bool = False

    @property
    def paths(self) -> tuple[str, ...]:
        """The absolute paths this patch wrote, relative to the repository root."""
        return tuple(str(path.relative_to(self.root.resolve())) for path in self._snapshots)

    def revert(self) -> None:
        """Restore every touched path to the state recorded at apply time.

        Never raises. Reverting runs on the failure path, where an exception
        here would replace the error that caused the rollback with a less
        informative one; a path that cannot be restored is logged instead.
        """
        unrestored: list[Path] = []
        for target, previous in self._snapshots.items():
            try:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(previous)
            except OSError:
                unrestored.append(target)

        if unrestored:
            logger.warning(
                "could not restore %s during rollback; inspect the tree by hand",
                ", ".join(str(path) for path in unrestored),
            )

        for directory in self._created_dirs:
            # Only ever removes directories this patch brought into existence,
            # and only while they are empty.
            with contextlib.suppress(OSError):
                directory.rmdir()

        self.reverted = True


def apply_patch(root: Path, patch: Patch, *, dry_run: bool = False) -> AppliedPatch:
    """Write ``patch`` into ``root`` atomically.

    Every path is validated before the first byte is written, so a patch that
    names one bad path changes nothing at all. If a write fails after others
    have landed, the whole batch is rolled back before the error propagates.

    Args:
        root: Repository root. Nothing outside it is ever opened.
        patch: The changes to apply.
        dry_run: Validate and snapshot without touching the filesystem.

    Returns:
        The snapshot needed to undo the batch. Empty under ``dry_run``.

    Raises:
        PatchError: if any path is out of bounds or a write fails.
    """
    targets = [(change, resolve_target(root, change.path)) for change in patch.changes]
    applied = AppliedPatch(root=root)
    if dry_run:
        return applied

    try:
        for change, target in targets:
            applied._snapshots[target] = target.read_bytes() if target.is_file() else None

            if isinstance(change, FileDelete):
                target.unlink(missing_ok=True)
                continue

            for parent in reversed(target.parents):
                if not parent.exists() and root.resolve() in parent.parents:
                    parent.mkdir()
                    applied._created_dirs.insert(0, parent)

            _atomic_write(target, change.content)
    except (PatchError, OSError) as error:
        applied.revert()
        raise PatchError(f"Applying the patch failed and was rolled back: {error}") from error

    return applied


def _atomic_write(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` via a same-directory temporary file.

    ``os.replace`` is atomic within a filesystem, so a reader — including the
    verification command, which may already be running — never observes a
    half-written source file.
    """
    handle, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
