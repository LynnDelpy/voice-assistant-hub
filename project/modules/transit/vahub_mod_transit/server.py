"""Swiss public-transport module (MCP server over stdio).

Uses the free transport.opendata.ch API (no key, no account). Read-only. Exposes
two tools: find_connections (journeys between two places, optionally arriving by
a given time) and next_departures (a station's departure board).

Config (optional, from the manifest env): TRANSIT_API_URL, TZ_DEFAULT.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("TRANSIT_API_URL", "https://transport.opendata.ch/v1").rstrip("/")
TZ = os.environ.get("TZ_DEFAULT", "Europe/Zurich")

mcp = FastMCP("transit")
_client = httpx.AsyncClient(base_url=BASE, timeout=12.0, headers={"user-agent": "vahub-transit/0.1"})


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Zurich")


def _fmt(iso: str | None) -> str | None:
    """API times look like 2026-07-27T07:52:00+0200 -> 'HH:MM'."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return iso


def _dur(s: str | None) -> str | None:
    """API duration '00d00:23:00' -> '23 min' or '1h05'."""
    if not s:
        return s
    try:
        days, hms = s.split("d")
        h, m, _ = hms.split(":")
        total = int(days) * 1440 + int(h) * 60 + int(m)
        hh, mm = divmod(total, 60)
        return f"{hh}h{mm:02d}" if hh else f"{mm} min"
    except Exception:
        return s


def _legs(sections: list | None) -> list[dict]:
    out: list[dict] = []
    for s in sections or []:
        dep = s.get("departure") or {}
        arr = s.get("arrival") or {}
        j = s.get("journey")
        if j:
            line = f'{(j.get("category") or "").strip()} {(j.get("number") or j.get("name") or "").strip()}'.strip()
            out.append({
                "line": line or None,
                "from": (dep.get("station") or {}).get("name"),
                "to": (arr.get("station") or {}).get("name"),
                "departure": _fmt(dep.get("departure")),
                "arrival": _fmt(arr.get("arrival")),
            })
        elif s.get("walk"):
            out.append({
                "walk": True,
                "from": (dep.get("station") or {}).get("name"),
                "to": (arr.get("station") or {}).get("name"),
            })
    return out


@mcp.tool()
async def find_connections(
    origin: str,
    destination: str,
    arrive_by: str | None = None,
    depart_at: str | None = None,
    date: str | None = None,
    limit: int = 4,
) -> dict:
    """Find public-transport connections between two places in Switzerland.

    origin / destination: stop or place names. Include the city for a street
      address, e.g. "Haldenweg 15, Basel", and station names like "Basel SBB".
    arrive_by: desired arrival time as "HH:MM" (returns journeys arriving by then).
    depart_at: desired departure time as "HH:MM" (ignored when arrive_by is set).
    date: "YYYY-MM-DD"; defaults to today.
    """
    params: dict = {"from": origin, "to": destination, "limit": max(1, min(int(limit), 6))}
    params["date"] = date or datetime.now(_tz()).strftime("%Y-%m-%d")
    if arrive_by:
        params["time"] = arrive_by
        params["isArrivalTime"] = 1
    elif depart_at:
        params["time"] = depart_at

    r = await _client.get("/connections", params=params)
    r.raise_for_status()
    data = r.json()
    conns = []
    for c in (data.get("connections") or [])[: params["limit"]]:
        frm, to = c.get("from") or {}, c.get("to") or {}
        conns.append({
            "depart": _fmt(frm.get("departure")),
            "arrive": _fmt(to.get("arrival")),
            "from": (frm.get("station") or {}).get("name"),
            "to": (to.get("station") or {}).get("name"),
            "duration": _dur(c.get("duration")),
            "transfers": c.get("transfers"),
            "legs": _legs(c.get("sections")),
        })
    return {"from": origin, "to": destination, "date": params["date"],
            "arrive_by": arrive_by, "depart_at": depart_at, "connections": conns}


@mcp.tool()
async def next_departures(station: str, limit: int = 6) -> dict:
    """Upcoming departures from a station (a departure board)."""
    r = await _client.get("/stationboard", params={"station": station, "limit": max(1, min(int(limit), 12))})
    r.raise_for_status()
    data = r.json()
    board = []
    for e in (data.get("stationboard") or [])[:limit]:
        stop = e.get("stop") or {}
        board.append({
            "line": f'{(e.get("category") or "").strip()} {(e.get("number") or e.get("name") or "").strip()}'.strip(),
            "to": e.get("to"),
            "departure": _fmt(stop.get("departure")),
            "platform": stop.get("platform"),
        })
    return {"station": (data.get("station") or {}).get("name") or station, "departures": board}


@mcp.tool(name="__health")
async def health() -> dict:
    """Reserved health probe: is the transit API reachable?"""
    t0 = time.monotonic()
    try:
        r = await _client.get("/stationboard", params={"station": "Basel SBB", "limit": 1})
        ok = r.status_code == 200
        return {"ok": ok, "backend": "reachable" if ok else "error",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "detail": None if ok else f"status {r.status_code}"}
    except Exception as e:
        return {"ok": False, "backend": "unreachable", "latency_ms": None, "detail": str(e)}


def run() -> None:
    mcp.run(transport="stdio")
