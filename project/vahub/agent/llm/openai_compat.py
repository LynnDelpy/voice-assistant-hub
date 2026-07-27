"""OpenAI-compatible Chat Completions adapter.

Works against any endpoint speaking the OpenAI /chat/completions shape: OpenAI,
OpenRouter, Groq, Together, a local Ollama/llama.cpp server, or Anthropic's
compatible endpoint. The agent loop stays provider-neutral; only this file
knows the wire format.
"""

from __future__ import annotations

import json

from .base import LLMResult, ToolSpec


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        request_timeout_s: float = 60.0,
    ) -> None:
        import httpx  # imported lazily so the mock path needs no httpx

        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=request_timeout_s,
        )

    async def complete(self, messages: list[dict], tools: list[ToolSpec]) -> LLMResult:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"

        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}

        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, ValueError):
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append({"id": tc.get("id") or "call", "name": fn.get("name") or "", "arguments": args})

        return LLMResult(
            text=message.get("content"),
            tool_calls=tool_calls,
            usage=body.get("usage"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
