"""A keyword-driven stub 'brain'.

This is NOT natural-language understanding. It exists so the whole agent loop
(message -> decide tool -> call it -> answer) is testable with zero credentials.
It recognises a handful of intents (time, lights, locks, status) and routes them
to the right tool; anything else gets an honest "I'm a stub" reply. Swap
llm.provider to openai_compat for a real model.
"""

from __future__ import annotations

import json

from .base import LLMResult, ToolSpec

_CITY_TZ = {
    "tokyo": "Asia/Tokyo", "berlin": "Europe/Berlin", "london": "Europe/London",
    "paris": "Europe/Paris", "new york": "America/New_York", "sydney": "Australia/Sydney", "utc": "UTC",
}
_ROOM_LIGHT = {
    "bedroom": "light.schlafzimmer", "schlafzimmer": "light.schlafzimmer",
    "living": "light.wohnzimmer", "wohnzimmer": "light.wohnzimmer",
    "hall": "light.flur", "flur": "light.flur",
    "kitchen": "light.kueche", "kueche": "light.kueche", "kitchen light": "light.kueche",
}


class MockLLM:
    async def complete(self, messages: list[dict], tools: list[ToolSpec]) -> LLMResult:
        last = messages[-1] if messages else {"role": "user", "content": ""}
        if last.get("role") == "tool":
            return LLMResult(text=_answer_from_result(last.get("content") or "{}"))

        text = _last_user_text(messages).lower()
        by_tool = {t.tool: t for t in tools}

        def call(tool_name: str, args: dict | None = None):
            spec = by_tool.get(tool_name)
            if not spec:
                return None
            return LLMResult(tool_calls=[{"id": "call_1", "name": spec.name, "arguments": args or {}}])

        # --- locks (unlock is destructive -> the gate will ask to confirm) ---
        if "unlock" in text and "lock_unlock" in by_tool:
            return call("lock_unlock", {"entity_id": "lock.haustuer"})
        if "lock" in text and "door" in text and "lock_lock" in by_tool:
            return call("lock_lock", {"entity_id": "lock.haustuer"})

        # --- lights ---
        light = _match_light(text)
        if light and ("light_turn_on" in by_tool or "light_turn_off" in by_tool):
            if any(k in text for k in ("turn off", "switch off", "off")):
                r = call("light_turn_off", {"entity_id": light})
                if r:
                    return r
            if any(k in text for k in ("turn on", "switch on", "on", "dim", "brighten")):
                args = {"entity_id": light}
                pct = _match_pct(text)
                if pct is not None:
                    args["brightness_pct"] = pct
                r = call("light_turn_on", args)
                if r:
                    return r

        # --- status / temperature ---
        if ("temperature" in text or "temp" in text) and "get_state" in by_tool:
            return call("get_state", {"entity_id": "sensor.temperatur"})
        if any(k in text for k in ("what devices", "list", "entities", "what's on")) and "list_entities" in by_tool:
            return call("list_entities", {})

        # --- time ---
        if any(k in text for k in ("time", "clock", "hour", "uhr", "o'clock")):
            want_spoken = any(k in text for k in ("say", "tell", "speak", "sag"))
            tool = "speak_current_time" if (want_spoken and "speak_current_time" in by_tool) else "get_current_time"
            if tool in by_tool:
                args = {}
                tz = _match_tz(text)
                if tz:
                    args["tz"] = tz
                return call(tool, args)

        available = ", ".join(t.name for t in tools) or "none"
        return LLMResult(text=(
            "I'm a keyword stub for testing the loop, not a real language model. I can tell the time and "
            "control the demo home (try 'turn on the bedroom light', 'what's the temperature', "
            "'unlock the front door'). Set llm.provider to openai_compat for real understanding. "
            f"Tools I can see: {available}."
        ))

    async def aclose(self) -> None:
        return None


def _answer_from_result(content: str) -> str:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return str(content)
    if isinstance(data, dict) and data.get("ok") is False:
        return f"That didn't work: {data.get('error')} ({data.get('detail')})."
    value = data.get("result") if isinstance(data, dict) else data
    if isinstance(value, list):
        return f"I found {len(value)} entities."
    if isinstance(value, dict):
        if "state" in value:
            return f"{value.get('entity_id', 'It')} is {value['state']}."
        if "entity_id" in value:
            return f"Done ({value['entity_id']})."
        return json.dumps(value)
    return str(value)


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _match_tz(text: str) -> str | None:
    for city, tz in _CITY_TZ.items():
        if city in text:
            return tz
    return None


def _match_light(text: str) -> str | None:
    for room, entity in _ROOM_LIGHT.items():
        if room in text:
            return entity
    if "light" in text:  # unqualified "the light" -> living room
        return "light.wohnzimmer"
    return None


def _match_pct(text: str) -> int | None:
    import re

    m = re.search(r"(\d{1,3})\s*%", text)
    if m:
        return max(1, min(100, int(m.group(1))))
    return None
