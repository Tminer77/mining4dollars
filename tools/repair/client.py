"""The model half of the loop.

The loop depends on :class:`ModelClient`, not on the Anthropic SDK, so the whole
control flow can be exercised in tests with a scripted client and no network.
:class:`AnthropicModelClient` is the one implementation that talks to the API.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

import anthropic
from anthropic.types import MessageParam

if TYPE_CHECKING:
    from anthropic.types import Message

__all__ = [
    "DEFAULT_EFFORT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "AnthropicModelClient",
    "Effort",
    "ModelClient",
    "ModelError",
    "ModelReply",
]

DEFAULT_MODEL = "claude-opus-5"

#: The operator's stated intent is max effort. Effort scales how much thinking
#: the model spends before answering, which is precisely the lever a repair task
#: wants: correctness matters more here than the cost of the turn.
DEFAULT_EFFORT: Effort = "max"

#: Whole-file replies are long. This is well under the model's 128K output
#: ceiling but high enough that a multi-file patch fits in one turn; the request
#: is streamed, so a large budget costs nothing when the reply is short.
DEFAULT_MAX_TOKENS = 64_000

Effort = Literal["low", "medium", "high", "xhigh", "max"]

_NO_CREDENTIALS = (
    "No API credentials. Set ANTHROPIC_API_KEY, or run `ant auth login` to "
    "store a profile the SDK can read."
)


class ModelError(Exception):
    """The model could not be reached, or answered with nothing usable."""


@dataclass(frozen=True, slots=True)
class ModelReply:
    """A single model turn, reduced to what the loop acts on."""

    text: str
    stop_reason: str | None = None
    request_id: str | None = None
    #: Set when the model declined the request; the loop stops rather than
    #: rephrasing, because a decline is not a transient failure.
    refusal: str | None = None

    @property
    def truncated(self) -> bool:
        """True when the reply hit the output token ceiling mid-sentence."""
        return self.stop_reason == "max_tokens"


class ModelClient(Protocol):
    """Everything the loop needs from a model."""

    def complete(self, *, system: str, messages: Sequence[MessageParam]) -> ModelReply:
        """Answer the conversation in ``messages`` under ``system``."""
        ...


class AnthropicModelClient:
    """A :class:`ModelClient` backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        effort: Effort = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        # Constructed without arguments on purpose: the SDK resolves credentials
        # from ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or a logged-in profile,
        # and reading the key here would defeat every source but the first.
        self._client = client if client is not None else anthropic.Anthropic()
        self._model = model
        self._effort: Effort = effort
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    def complete(self, *, system: str, messages: Sequence[MessageParam]) -> ModelReply:
        """Run one turn against the API.

        Streamed because a 64K-token budget on a non-streaming request risks an
        HTTP timeout on exactly the long replies this loop asks for.

        Raises:
            ModelError: on any API or connection failure. The distinction
                between retryable and terminal is left to the SDK, which already
                retries 429s and 5xx with backoff.
        """
        try:
            with self._client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=list(messages),
                # Thinking is on by default on this model family; asking for the
                # summary makes the reasoning visible in the journal.
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": self._effort},
                # Caches the longest stable prefix — system prompt plus every
                # earlier turn — which is most of a fifteen-attempt run.
                cache_control={"type": "ephemeral"},
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as error:
            raise ModelError(_NO_CREDENTIALS) from error
        except TypeError as error:
            # The SDK resolves credentials lazily and reports the total absence
            # of one as a TypeError at request time. Anything else is a bug in
            # this code, so only that case is translated.
            if "authentication method" not in str(error):
                raise
            raise ModelError(_NO_CREDENTIALS) from error
        except anthropic.RateLimitError as error:
            raise ModelError(f"Rate limited after the SDK's own retries: {error}") from error
        except anthropic.APIStatusError as error:
            raise ModelError(f"API returned {error.status_code}: {error.message}") from error
        except anthropic.APIConnectionError as error:
            raise ModelError(f"Could not reach the API: {error}") from error

        return _reply_from(message)


def _reply_from(message: Message) -> ModelReply:
    """Reduce an API response to a :class:`ModelReply`.

    Concatenates every text block rather than reading ``content[0]``: with
    thinking enabled the first block is a thinking block, and a reply can be
    split across several text blocks.
    """
    text = "".join(block.text for block in message.content if block.type == "text")

    refusal: str | None = None
    if message.stop_reason == "refusal":
        details = message.stop_details
        category = getattr(details, "category", None)
        explanation = getattr(details, "explanation", None)
        refusal = explanation or f"declined ({category or 'no category given'})"

    return ModelReply(
        text=text,
        stop_reason=message.stop_reason,
        request_id=message._request_id,
        refusal=refusal,
    )
