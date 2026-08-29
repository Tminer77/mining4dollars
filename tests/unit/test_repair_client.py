"""Turning an API response into something the loop can act on."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import anthropic
import httpx2
import pytest

from tools.repair.client import AnthropicModelClient, ModelError, ModelReply


@dataclass
class FakeBlock:
    type: str
    text: str = ""


@dataclass
class FakeMessage:
    content: list[FakeBlock]
    stop_reason: str | None = "end_turn"
    stop_details: object | None = None
    _request_id: str | None = "req_test"


@dataclass
class FakeStopDetails:
    category: str | None = None
    explanation: str | None = None


class FakeStream:
    def __init__(self, message: FakeMessage) -> None:
        self._message = message

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get_final_message(self) -> FakeMessage:
        return self._message


@dataclass
class FakeMessages:
    message: FakeMessage | None = None
    error: Exception | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)

    def stream(self, **kwargs: Any) -> FakeStream:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        assert self.message is not None
        return FakeStream(self.message)


@dataclass
class FakeAnthropic:
    messages: FakeMessages


def client_for(
    message: FakeMessage | None = None, *, error: Exception | None = None, **kwargs: Any
) -> tuple[AnthropicModelClient, FakeMessages]:
    messages = FakeMessages(message=message, error=error)
    fake = cast(anthropic.Anthropic, FakeAnthropic(messages=messages))
    return AnthropicModelClient(client=fake, **kwargs), messages


def ask(client: AnthropicModelClient) -> ModelReply:
    return client.complete(system="rules", messages=[{"role": "user", "content": "fix it"}])


class TestReplyExtraction:
    def test_ignores_thinking_blocks(self) -> None:
        """With thinking on, content[0] is never the answer."""
        message = FakeMessage(
            content=[FakeBlock("thinking"), FakeBlock("text", "```file:a.py\nx\n```")]
        )
        client, _ = client_for(message)
        assert ask(client).text == "```file:a.py\nx\n```"

    def test_joins_every_text_block(self) -> None:
        message = FakeMessage(content=[FakeBlock("text", "one "), FakeBlock("text", "two")])
        client, _ = client_for(message)
        assert ask(client).text == "one two"

    def test_carries_the_request_id_for_support(self) -> None:
        client, _ = client_for(FakeMessage(content=[FakeBlock("text", "hi")]))
        assert ask(client).request_id == "req_test"

    def test_a_max_tokens_stop_marks_the_reply_truncated(self) -> None:
        message = FakeMessage(content=[FakeBlock("text", "half")], stop_reason="max_tokens")
        client, _ = client_for(message)
        assert ask(client).truncated

    def test_an_ordinary_reply_is_not_truncated(self) -> None:
        client, _ = client_for(FakeMessage(content=[FakeBlock("text", "done")]))
        assert not ask(client).truncated


class TestRefusal:
    def test_reports_the_explanation(self) -> None:
        message = FakeMessage(
            content=[],
            stop_reason="refusal",
            stop_details=FakeStopDetails(category="cyber", explanation="declined for safety"),
        )
        client, _ = client_for(message)
        assert ask(client).refusal == "declined for safety"

    def test_falls_back_to_the_category(self) -> None:
        message = FakeMessage(
            content=[], stop_reason="refusal", stop_details=FakeStopDetails(category="cyber")
        )
        client, _ = client_for(message)
        assert "cyber" in str(ask(client).refusal)

    def test_survives_a_refusal_with_no_details(self) -> None:
        message = FakeMessage(content=[], stop_reason="refusal", stop_details=None)
        client, _ = client_for(message)
        assert ask(client).refusal is not None

    def test_an_ordinary_reply_carries_no_refusal(self) -> None:
        client, _ = client_for(FakeMessage(content=[FakeBlock("text", "fine")]))
        assert ask(client).refusal is None


class TestRequestShape:
    def test_sends_the_configured_model_and_effort(self) -> None:
        client, messages = client_for(
            FakeMessage(content=[FakeBlock("text", "x")]), model="claude-opus-5", effort="max"
        )
        ask(client)
        assert messages.kwargs["model"] == "claude-opus-5"
        assert messages.kwargs["output_config"] == {"effort": "max"}

    def test_asks_for_adaptive_thinking(self) -> None:
        client, messages = client_for(FakeMessage(content=[FakeBlock("text", "x")]))
        ask(client)
        assert messages.kwargs["thinking"]["type"] == "adaptive"

    def test_caches_the_conversation_prefix(self) -> None:
        """Fifteen attempts resend the same history; paying full price for it is waste."""
        client, messages = client_for(FakeMessage(content=[FakeBlock("text", "x")]))
        ask(client)
        assert messages.kwargs["cache_control"] == {"type": "ephemeral"}

    def test_passes_the_system_prompt_and_messages_through(self) -> None:
        client, messages = client_for(FakeMessage(content=[FakeBlock("text", "x")]))
        ask(client)
        assert messages.kwargs["system"] == "rules"
        assert messages.kwargs["messages"] == [{"role": "user", "content": "fix it"}]


class TestErrorMapping:
    def test_authentication_failure_names_the_fix(self) -> None:
        error = anthropic.AuthenticationError(
            "bad key", response=httpx2.Response(401, request=httpx2.Request("POST", "/")), body=None
        )
        client, _ = client_for(error=error)
        with pytest.raises(ModelError, match="ANTHROPIC_API_KEY"):
            ask(client)

    def test_a_server_error_carries_its_status(self) -> None:
        error = anthropic.APIStatusError(
            "upstream",
            response=httpx2.Response(503, request=httpx2.Request("POST", "/")),
            body=None,
        )
        client, _ = client_for(error=error)
        with pytest.raises(ModelError, match="503"):
            ask(client)

    def test_a_connection_failure_is_a_model_error(self) -> None:
        error = anthropic.APIConnectionError(request=httpx2.Request("POST", "/"))
        client, _ = client_for(error=error)
        with pytest.raises(ModelError, match="Could not reach"):
            ask(client)

    def test_absent_credentials_are_reported_as_a_setup_problem(self) -> None:
        """The SDK reports "no credentials at all" as a bare TypeError."""
        error = TypeError("Could not resolve authentication method. Expected one of ...")
        client, _ = client_for(error=error)
        with pytest.raises(ModelError, match="ANTHROPIC_API_KEY"):
            ask(client)

    def test_an_unrelated_type_error_still_propagates(self) -> None:
        client, _ = client_for(error=TypeError("takes 2 positional arguments but 3 were given"))
        with pytest.raises(TypeError):
            ask(client)

    def test_unexpected_exceptions_are_not_swallowed(self) -> None:
        """A bug in the loop must not be reported as an API failure."""
        client, _ = client_for(error=ZeroDivisionError("a real bug"))
        with pytest.raises(ZeroDivisionError):
            ask(client)
