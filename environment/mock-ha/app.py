"""A tiny Home Assistant REST double for the sandbox.

Implements just enough of the HA API for the homeassistant module: /api/,
/api/states, /api/states/{id}, and /api/services/{domain}/{service}. State is
in memory and resets with the container. Bearer token is checked so the module's
auth path is exercised too. This is NOT Home Assistant; it is a stand-in so the
module and the gate can be tested without real hardware.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

TOKEN = os.environ.get("MOCK_HA_TOKEN", "dev-token")

app = FastAPI(title="mock-ha")

_STATES: dict[str, dict] = {
    "light.schlafzimmer": {"state": "off", "attributes": {"friendly_name": "Schlafzimmer"}},
    "light.wohnzimmer": {"state": "off", "attributes": {"friendly_name": "Wohnzimmer"}},
    "light.flur": {"state": "off", "attributes": {"friendly_name": "Flur"}},
    "light.kueche": {"state": "off", "attributes": {"friendly_name": "Kueche"}},
    "sensor.temperatur": {"state": "21.5", "attributes": {"unit_of_measurement": "°C"}},
    "lock.haustuer": {"state": "locked", "attributes": {"friendly_name": "Haustuer"}},
    "switch.kaffee": {"state": "off", "attributes": {"friendly_name": "Kaffeemaschine"}},
}


def _auth(authorization: str | None) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _entity(entity_id: str) -> dict:
    s = _STATES[entity_id]
    return {"entity_id": entity_id, "state": s["state"], "attributes": s["attributes"]}


@app.get("/api/")
async def root(authorization: str | None = Header(default=None)) -> JSONResponse:
    _auth(authorization)
    return JSONResponse({"message": "API running."})


@app.get("/api/states")
async def states(authorization: str | None = Header(default=None)) -> JSONResponse:
    _auth(authorization)
    return JSONResponse([_entity(eid) for eid in _STATES])


@app.get("/api/states/{entity_id}")
async def state(entity_id: str, authorization: str | None = Header(default=None)) -> JSONResponse:
    _auth(authorization)
    if entity_id not in _STATES:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(_entity(entity_id))


@app.post("/api/services/{domain}/{service}")
async def call_service(
    domain: str, service: str, request: Request, authorization: str | None = Header(default=None)
) -> JSONResponse:
    _auth(authorization)
    body = await request.json()
    entity_id = body.get("entity_id")
    if entity_id not in _STATES:
        return JSONResponse([])  # HA silently no-ops unknown entities
    new_state = {
        ("light", "turn_on"): "on",
        ("light", "turn_off"): "off",
        ("switch", "turn_on"): "on",
        ("switch", "turn_off"): "off",
        ("lock", "lock"): "locked",
        ("lock", "unlock"): "unlocked",
    }.get((domain, service))
    if new_state is not None:
        _STATES[entity_id]["state"] = new_state
        if domain == "light" and service == "turn_on" and "brightness_pct" in body:
            _STATES[entity_id]["attributes"]["brightness_pct"] = body["brightness_pct"]
    return JSONResponse([_entity(entity_id)])
