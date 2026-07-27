"""OpenAI/Whisper-compatible STT (/audio/transcriptions)."""

from __future__ import annotations


class OpenAICompatSTT:
    def __init__(self, base_url: str, api_key: str | None, model: str, request_timeout_s: float = 60.0) -> None:
        import httpx

        self._model = model
        headers = {}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=request_timeout_s)

    async def transcribe(self, audio: bytes, mime: str) -> str:
        suffix = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3"}.get(mime, "webm")
        files = {"file": (f"audio.{suffix}", audio, mime)}
        data = {"model": self._model}
        resp = await self._client.post("/audio/transcriptions", files=files, data=data)
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()

    async def aclose(self) -> None:
        await self._client.aclose()
