"""LLM adapter interface and a small factory.

The agent loop speaks one shape to every backend: it hands over a list of
OpenAI-style chat messages plus the available tools, and gets back an
`LLMResult` that is either text (the final answer) or one or more tool calls.
Keeping this narrow is what makes the backend swappable (the plan's point:
API first, switch later cheaply).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ...core.config import LLMConfig


@dataclass
class ToolSpec:
    """A tool as offered to the model. `name` is a sanitized function name
    (dots are not allowed in OpenAI function names), and `module`/`tool` are how
    the loop routes the call back to the module API."""

    name: str
    module: str
    tool: str
    description: str | None
    parameters: dict


@dataclass
class LLMResult:
    text: str | None = None
    # each: {"id": str, "name": str (sanitized), "arguments": dict}
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict | None = None


class LLMAdapter(Protocol):
    async def complete(self, messages: list[dict], tools: list[ToolSpec]) -> LLMResult: ...

    async def aclose(self) -> None: ...


def build_adapter(cfg: "LLMConfig") -> LLMAdapter:
    if cfg.provider == "mock":
        from .mock import MockLLM

        return MockLLM()
    if cfg.provider == "openai_compat":
        from .openai_compat import OpenAICompatLLM

        return OpenAICompatLLM(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            request_timeout_s=cfg.request_timeout_s,
        )
    raise ValueError(f"unknown llm provider: {cfg.provider!r}")
