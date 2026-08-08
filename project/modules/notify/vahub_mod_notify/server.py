"""Push-notification module (MCP server over stdio).

Sends push notifications on command. Two backends, picked with NOTIFY_BACKEND:

  ntfy (default, no account/key needed)
    NTFY_URL     base url, default https://ntfy.sh (self-hosted works too)
    NTFY_TOPIC   the topic to publish to (required for this backend)

  pushover (needs an app + user key from pushover.net)
    PUSHOVER_TOKEN / PUSHOVER_TOKEN_FILE   app token
    PUSHOVER_USER  / PUSHOVER_USER_FILE    user/group key

Token loading mirrors the homeassistant module: read the *_FILE path if the
plain env var isn't set, so prod can hand it in via a systemd credential while
the sandbox just sets the env var directly. Nothing here is ever logged.

Exposes one tool: send_push (title, message, priority, tags).
"""

from __future__ import annotations

import os
import time

import httpx
from mcp.server.fastmcp import FastMCP

BACKEND = os.environ.get("NOTIFY_BACKEND", "ntfy").strip().lower()

NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

# ntfy priority names -> its 1..5 scale
_NTFY_PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}
# pushover priority names -> its -2..1 scale (2/emergency deliberately not exposed here)
_PUSHOVER_PRIORITY = {"min": -2, "low": -1, "default": 0, "high": 1, "urgent": 1}


def _load(env_var: str, file_var: str) -> str:
    """Same pattern as the homeassistant module: plain env var first, else a
    file path (how prod supplies secrets via a systemd credential)."""
    val = os.environ.get(env_var, "").strip()
    if val:
        return val
    path = os.environ.get(file_var, "").strip()
    if path and os.path.exists(path):
        return open(path).read().strip()
    return ""


PUSHOVER_TOKEN = _load("PUSHOVER_TOKEN", "PUSHOVER_TOKEN_FILE")
PUSHOVER_USER = _load("PUSHOVER_USER", "PUSHOVER_USER_FILE")

mcp = FastMCP("notify")
_client = httpx.AsyncClient(timeout=10.0, headers={"user-agent": "vahub-notify/0.1"})


async def _send_ntfy(title: str, message: str, priority: str, tags: str | None) -> dict:
    if not NTFY_TOPIC:
        return {"ok": False, "error": "NTFY_TOPIC not configured"}
    headers = {
        "Title": title,
        "Priority": str(_NTFY_PRIORITY.get(priority, 3)),
    }
    if tags:
        headers["Tags"] = tags
    r = await _client.post(f"{NTFY_URL}/{NTFY_TOPIC}", content=message.encode("utf-8"), headers=headers)
    ok = r.status_code < 300
    return {"ok": ok, "backend": "ntfy", "status": r.status_code, "detail": None if ok else r.text[:200]}


async def _send_pushover(title: str, message: str, priority: str, tags: str | None) -> dict:
    if not (PUSHOVER_TOKEN and PUSHOVER_USER):
        return {"ok": False, "error": "PUSHOVER_TOKEN/PUSHOVER_USER not configured"}
    data = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": title,
        "message": message,
        "priority": _PUSHOVER_PRIORITY.get(priority, 0),
    }
    r = await _client.post("https://api.pushover.net/1/messages.json", data=data)
    ok = r.status_code == 200
    return {"ok": ok, "backend": "pushover", "status": r.status_code, "detail": None if ok else r.text[:200]}


@mcp.tool()
async def send_push(
    title: str,
    message: str,
    priority: str = "default",
    tags: str | None = None,
) -> dict:
    """Send a push notification on command.

    title: short headline shown in the notification.
    message: the body text.
    priority: one of "min", "low", "default", "high", "urgent".
    tags: optional, backend-specific (e.g. ntfy emoji short-codes like
      "warning,skull" -> shows icons; ignored by pushover).
    """
    priority = priority if priority in _NTFY_PRIORITY else "default"
    if BACKEND == "pushover":
        return await _send_pushover(title, message, priority, tags)
    return await _send_ntfy(title, message, priority, tags)


@mcp.tool(name="__health")
async def health() -> dict:
    """Reserved health probe: is the configured backend reachable/configured?"""
    t0 = time.monotonic()
    try:
        if BACKEND == "pushover":
            if not (PUSHOVER_TOKEN and PUSHOVER_USER):
                return {"ok": False, "backend": "pushover", "latency_ms": None,
                         "detail": "PUSHOVER_TOKEN/PUSHOVER_USER not configured"}
            r = await _client.post(
                "https://api.pushover.net/1/users/validate.json",
                data={"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER},
            )
            ok = r.status_code == 200
        else:
            if not NTFY_TOPIC:
                return {"ok": False, "backend": "ntfy", "latency_ms": None,
                         "detail": "NTFY_TOPIC not configured"}
            r = await _client.head(f"{NTFY_URL}/{NTFY_TOPIC}")
            ok = r.status_code < 500
        return {"ok": ok, "backend": BACKEND, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "detail": None if ok else f"status {r.status_code}"}
    except Exception as e:
        return {"ok": False, "backend": BACKEND, "latency_ms": None, "detail": str(e)}


def run() -> None:
    mcp.run(transport="stdio")
