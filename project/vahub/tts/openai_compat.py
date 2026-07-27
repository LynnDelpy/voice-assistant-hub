"""OpenAI-compatible TTS (/audio/speech)."""

from __future__ import annotations


class OpenAICompatTTS:
    def __init__(
        self, base_url: str, api_key: str | None, model: str, voice: str, request_timeout_s: float = 60.0
    ) -> None:
        import httpx

        self._model = model
        self._voice = voice
        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=request_timeout_s)

    async def synthesize(self, text: str) -> tuple[bytes, str] | None:
        resp = await self._client.post(
            "/audio/speech",
            json={"model": self._model, "voice": self._voice, "input": text, "response_format": "mp3"},
        )
        resp.raise_for_status()
        return resp.content, "audio/mpeg"

    async def aclose(self) -> None:
        await self._client.aclose()
