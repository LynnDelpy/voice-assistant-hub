"""The trivial first module: it only tells the time.

Its job in the build order is to validate the module contract (spawn, MCP
handshake, tool registry, the reserved `__health` tool) at something far cheaper
to debug than Home Assistant. It is a standard MCP server built on the official
SDK, so the hub's hand-rolled client is exercised against a compliant peer.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("time")

_DEFAULT_TZ = os.environ.get("TZ_DEFAULT", "Europe/Berlin")


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or _DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


@mcp.tool()
def get_current_time(tz: str | None = None) -> str:
    """Return the current time as an ISO-8601 string in the given timezone."""
    return datetime.now(_zone(tz)).isoformat(timespec="seconds")


@mcp.tool()
def speak_current_time(tz: str | None = None) -> str:
    """Return the current time as a short spoken phrase (for TTS later)."""
    now = datetime.now(_zone(tz))
    return f"It is {now:%H:%M}."


@mcp.tool(name="__health")
def health() -> dict:
    """Reserved health probe. The time module has no backend, so it is always ok."""
    return {"ok": True, "backend": "n/a", "latency_ms": 0, "detail": None}


def run() -> None:
    mcp.run(transport="stdio")
