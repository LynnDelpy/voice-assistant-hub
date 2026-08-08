"""Home Assistant module (MCP server over stdio).

Talks to HA's REST API with a long-lived token. It exposes narrow, named tools
(no generic call_service): the policy gate constrains which entities each tool
may touch, and a generic pass-through would make that constraint a fiction.

Config (from the manifest, injected as env): HA_URL, HA_TOKEN, HA_VERIFY_SSL.
Works against a real HA or the sandbox's mock HA identically.
"""

from __future__ import annotations

import os
import time

import httpx
from mcp.server.fastmcp import FastMCP

HA_URL = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/")
HA_VERIFY_SSL = os.environ.get("HA_VERIFY_SSL", "true").lower() not in ("false", "0", "no")


def _load_token() -> str:
    """Token from HA_TOKEN, or from a file (HA_TOKEN_FILE). The file path is how
    prod supplies it via a systemd credential; the sandbox writes it there too."""
    tok = os.environ.get("HA_TOKEN", "").strip()
    if tok:
        return tok
    path = os.environ.get("HA_TOKEN_FILE", "").strip()
    if path and os.path.exists(path):
        return open(path).read().strip()
    return ""


HA_TOKEN = _load_token()

mcp = FastMCP("homeassistant")
_client = httpx.AsyncClient(
    base_url=HA_URL,
    headers={"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"},
    verify=HA_VERIFY_SSL,
    timeout=10.0,
)


@mcp.tool()
async def list_entities(domain: str | None = None) -> list[dict]:
    """List entities with their current state.

    domain: optional filter, e.g. "light", "sensor", "lock", "switch". A real
      home has hundreds of entities and the full list is truncated before it
      reaches the model, so filter by domain whenever you know what you are
      looking for (use "sensor" for temperature, humidity and similar readings).
    """
    r = await _client.get("/api/states")
    r.raise_for_status()
    out = [{"entity_id": s["entity_id"], "state": s["state"]} for s in r.json()]
    if domain:
        prefix = f"{domain.strip().rstrip('.')}."
        out = [e for e in out if e["entity_id"].startswith(prefix)]
    return out


@mcp.tool()
async def get_state(entity_id: str) -> dict:
    """Get one entity's state and attributes."""
    r = await _client.get(f"/api/states/{entity_id}")
    if r.status_code == 404:
        return {"error": "not_found", "entity_id": entity_id}
    r.raise_for_status()
    d = r.json()
    return {"entity_id": d["entity_id"], "state": d["state"], "attributes": d.get("attributes", {})}


async def _service(domain: str, service: str, data: dict) -> dict:
    r = await _client.post(f"/api/services/{domain}/{service}", json=data)
    r.raise_for_status()
    return {"ok": True, "entity_id": data.get("entity_id")}


@mcp.tool()
async def light_turn_on(entity_id: str, brightness_pct: int | None = None) -> dict:
    """Turn a light on, optionally at a brightness percentage (1-100)."""
    data: dict = {"entity_id": entity_id}
    if brightness_pct is not None:
        data["brightness_pct"] = brightness_pct
    return await _service("light", "turn_on", data)


@mcp.tool()
async def light_turn_off(entity_id: str) -> dict:
    """Turn a light off."""
    return await _service("light", "turn_off", {"entity_id": entity_id})


@mcp.tool()
async def lock_lock(entity_id: str) -> dict:
    """Lock a lock."""
    return await _service("lock", "lock", {"entity_id": entity_id})


@mcp.tool()
async def lock_unlock(entity_id: str) -> dict:
    """Unlock a lock (destructive: gated behind confirmation)."""
    return await _service("lock", "unlock", {"entity_id": entity_id})


@mcp.tool(name="__health")
async def health() -> dict:
    """Reserved health probe: is HA reachable?"""
    t0 = time.monotonic()
    try:
        r = await _client.get("/api/")
        ok = r.status_code == 200
        return {
            "ok": ok,
            "backend": "reachable" if ok else "error",
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            "detail": None if ok else f"status {r.status_code}",
        }
    except Exception as e:
        return {"ok": False, "backend": "unreachable", "latency_ms": None, "detail": str(e)}


def run() -> None:
    mcp.run(transport="stdio")
