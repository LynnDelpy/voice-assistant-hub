"""Speech to text adapter interface + factory.

The core logic never programs against a provider directly: it calls
`transcribe(audio, mime) -> text`. Today: a mock (no transcription without a
model) and an OpenAI/Whisper-compatible HTTP backend. Later: local GPU STT, by
adding one file, the decision the plan defers until after the latency spike.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..core.config import STTConfig


class STTAdapter(Protocol):
    async def transcribe(self, audio: bytes, mime: str) -> str: ...

    async def aclose(self) -> None: ...


def build_stt(cfg: "STTConfig") -> STTAdapter:
    if cfg.provider == "mock":
        from .mock import MockSTT

        return MockSTT()
    if cfg.provider == "openai_compat":
        from .openai_compat import OpenAICompatSTT

        return OpenAICompatSTT(cfg.base_url, cfg.api_key, cfg.model, cfg.request_timeout_s)
    raise ValueError(f"unknown stt provider: {cfg.provider!r}")
