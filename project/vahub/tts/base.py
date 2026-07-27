"""Text to speech adapter interface + factory.

`synthesize(text) -> (audio_bytes, mime) | None`. None means "no server audio",
and the browser falls back to its own speech synthesis (so the assistant talks
back with zero credentials in the demo). openai_compat produces real audio.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..core.config import TTSConfig


class TTSAdapter(Protocol):
    async def synthesize(self, text: str) -> tuple[bytes, str] | None: ...

    async def aclose(self) -> None: ...


def build_tts(cfg: "TTSConfig") -> TTSAdapter:
    if cfg.provider == "mock":
        from .mock import MockTTS

        return MockTTS()
    if cfg.provider == "openai_compat":
        from .openai_compat import OpenAICompatTTS

        return OpenAICompatTTS(cfg.base_url, cfg.api_key, cfg.model, cfg.voice, cfg.request_timeout_s)
    raise ValueError(f"unknown tts provider: {cfg.provider!r}")
