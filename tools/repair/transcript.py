"""What the model is told, and how the conversation grows across attempts.

Two constraints shape this module. The operator's rules must survive every turn
unchanged, which is why they live in the system prompt rather than in the first
user message where fifteen turns of failure logs would bury them. And the
conversation must stay affordable, which is why failure logs are truncated
around their informative ends rather than allowed to accumulate in full.
"""

from __future__ import annotations

from tools.repair.verification import VerificationReport, truncate

__all__ = [
    "PATCH_PROTOCOL",
    "SYSTEM_PROMPT",
    "failure_prompt",
    "initial_prompt",
    "protocol_error_prompt",
    "truncated_reply_prompt",
]

PATCH_PROTOCOL = """\
## How to return your work

Return every file you changed as a fenced block whose info string is the
directive and the repository-relative path:

```file:path/to/file.py
<the complete final contents of the file>
```

To remove a file, return an empty block:

```delete:path/to/obsolete.py
```

Rules for the blocks, all of which are enforced by the parser:

- Send the **complete** file. Blocks replace the file wholesale; there is no
  patch or hunk syntax, and an abbreviated body silently truncates the file.
- One block per path per reply. A path written twice is rejected.
- Paths are relative to the repository root, never absolute and never
  containing `..`.
- If a file's own contents include a line of three backticks, open and close
  its block with four or more backticks instead.
- Prose outside the blocks is ignored by the parser. Use it to explain the
  root cause; use the blocks to fix it.
"""

SYSTEM_PROMPT = f"""\
You are acting in Max-Effort Automated Repair Mode on a real repository. Your
objective is to fix the experimental AI architecture blueprint tree completely.

Rules:
1. Do NOT simplify or replace custom experimental AI loops with standard
   paradigms. The unusual structures in this tree are the design, not the bug.
   Repair them on their own terms.
2. Produce full, production-ready, non-stubbed files. No `pass`, no `TODO`, no
   `NotImplementedError` standing in for work, no placeholder comments.
3. Every turn must attempt to fix the exact errors reported by the local test
   runner. Read the reported failure, name its root cause, and change the code
   that causes it — not the assertion that reveals it.

Working method:
- Weakening the verification gate is not a repair. Do not delete, skip,
  `xfail`, or loosen a test, and do not relax a lint or type rule, to make the
  gate pass. If a test itself is genuinely wrong, say so explicitly and explain
  why before changing it.
- Change as little as the failure requires. A reply that rewrites files the
  failure never mentioned is harder to review and more likely to regress.
- You cannot see the tree between turns. Every file you send is written to disk
  exactly as you send it, then the gate is re-run and its output comes back to
  you.

{PATCH_PROTOCOL}"""


def initial_prompt(report: VerificationReport, *, log_limit: int) -> str:
    """The opening turn: the rules are in the system prompt, the evidence here."""
    return (
        "Begin audit and repair of the codebase. Run full deep reasoning.\n\n"
        "The repository's verification gate currently fails. Its output "
        "follows. Diagnose the root cause and return the corrected files.\n\n"
        f"{report.failure_log(log_limit)}"
    )


def failure_prompt(report: VerificationReport, *, log_limit: int, attempt: int) -> str:
    """A follow-up turn carrying the gate's verdict on the previous patch."""
    return (
        f"Your patch was applied and the gate was re-run. It still fails "
        f"(attempt {attempt}). Analyse the root cause and rewrite the broken "
        "components.\n\n"
        "If these errors are the same as last turn, your previous diagnosis was "
        "wrong — change your approach rather than resending a variation of the "
        "same patch.\n\n"
        f"{report.failure_log(log_limit)}"
    )


def protocol_error_prompt(error: str, *, reply: str, log_limit: int) -> str:
    """Sent when the reply could not be parsed or applied.

    The reply is echoed back trimmed: the model reasons about its own output far
    better when it can see what actually arrived than from the error alone.
    """
    return (
        "Nothing was applied: your reply did not satisfy the patch protocol.\n\n"
        f"Parser error: {error}\n\n"
        "Resend the complete files using the block format from your "
        "instructions.\n\n"
        f"--- your reply as received ---\n{truncate(reply, log_limit)}"
    )


def truncated_reply_prompt() -> str:
    """Sent when the model hit ``max_tokens`` mid-reply."""
    return (
        "Your reply was cut off at the output token limit, so it could not be "
        "applied. Resend it covering fewer files per turn — start with the ones "
        "the failure names directly, and we will iterate on the rest."
    )
