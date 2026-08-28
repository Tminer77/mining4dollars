"""An automated repair loop driven by Claude.

The loop is deliberately closed around the repository's own verification gate:
the model never decides whether it succeeded. It proposes a patch, the patch is
applied atomically, ``make check`` (or whatever gate was configured) is run, and
the exit status of that command is the only definition of "fixed". Every turn
after the first carries the exact output of the failing gate back to the model.

Entry point: ``python -m tools.repair`` (see :mod:`tools.repair.cli`).
"""

from __future__ import annotations

from tools.repair.client import AnthropicModelClient, ModelClient, ModelError, ModelReply
from tools.repair.journal import Journal
from tools.repair.loop import AttemptRecord, RepairLoop, RepairOutcome
from tools.repair.patch import (
    AppliedPatch,
    FileDelete,
    FileWrite,
    Patch,
    PatchError,
    apply_patch,
    parse_patch,
)
from tools.repair.verification import CommandResult, VerificationReport, Verifier

__all__ = [
    "AnthropicModelClient",
    "AppliedPatch",
    "AttemptRecord",
    "CommandResult",
    "FileDelete",
    "FileWrite",
    "Journal",
    "ModelClient",
    "ModelError",
    "ModelReply",
    "Patch",
    "PatchError",
    "RepairLoop",
    "RepairOutcome",
    "VerificationReport",
    "Verifier",
    "apply_patch",
    "parse_patch",
]
