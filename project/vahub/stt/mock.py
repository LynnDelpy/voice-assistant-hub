"""Mock STT. It cannot transcribe real audio (there is no model), so it returns
an empty string; the /api/voice endpoint turns that into a clear message. The
browser voice button uses the Web Speech API instead, so voice works with no
credentials for the demo. Configure provider=openai_compat for real server STT."""

from __future__ import annotations


class MockSTT:
    async def transcribe(self, audio: bytes, mime: str) -> str:
        return ""

    async def aclose(self) -> None:
        return None
