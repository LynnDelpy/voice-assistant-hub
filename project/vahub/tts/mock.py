"""Mock TTS: no server audio. Returns None so the browser speaks the reply with
its own speech synthesis (works with no credentials)."""

from __future__ import annotations


class MockTTS:
    async def synthesize(self, text: str) -> tuple[bytes, str] | None:
        return None

    async def aclose(self) -> None:
        return None
