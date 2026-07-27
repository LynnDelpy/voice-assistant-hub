"""Headless onboarding for the sandbox Home Assistant.

Real HA needs a user and a token before its API is usable. This script does that
once, over HA's own APIs (so it is not tied to a specific .storage layout):
  1. wait for HA to be up
  2. if a valid token already exists, stop
  3. onboard the owner (fresh install) or log in (already onboarded)
  4. create a long-lived access token over the WebSocket API
  5. write the token to HA_TOKEN_OUT for the hub to read

The token file is the same shape the hub reads in production from a systemd
credential, so nothing about the hub's auth path is sandbox-specific.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import websockets

HA = os.environ.get("HA_URL", "http://homeassistant:8123").rstrip("/")
USER = os.environ.get("HA_ONBOARD_USER", "vahub")
PW = os.environ.get("HA_ONBOARD_PASSWORD", "vahub-sandbox-pw")
NAME = os.environ.get("HA_ONBOARD_NAME", "vahub")
TOKEN_OUT = os.environ.get("HA_TOKEN_OUT", "/tokens/ha_token")
CLIENT_ID = HA + "/"


def log(*a):
    print("[onboard]", *a, flush=True)


async def wait_up(client: httpx.AsyncClient) -> None:
    for _ in range(150):
        try:
            r = await client.get(f"{HA}/manifest.json")
            if r.status_code == 200:
                return
        except Exception:
            pass
        await asyncio.sleep(2)
    raise SystemExit("HA did not come up in time")


async def existing_token_ok(client: httpx.AsyncClient) -> bool:
    try:
        with open(TOKEN_OUT) as f:
            tok = f.read().strip()
    except FileNotFoundError:
        return False
    if not tok:
        return False
    r = await client.get(f"{HA}/api/", headers={"authorization": f"Bearer {tok}"})
    return r.status_code == 200


async def onboard_or_login(client: httpx.AsyncClient) -> str:
    """Return an authorization code exchangeable for an access token."""
    r = await client.post(
        f"{HA}/api/onboarding/users",
        json={"client_id": CLIENT_ID, "name": NAME, "username": USER, "password": PW, "language": "en"},
    )
    if r.status_code == 200:
        log("onboarded owner user")
        return r.json()["auth_code"]
    log(f"onboarding/users returned {r.status_code}; assuming already onboarded, logging in")
    r = await client.post(
        f"{HA}/auth/login_flow",
        json={"client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID, "type": "authorize"},
    )
    r.raise_for_status()
    flow_id = r.json()["flow_id"]
    r = await client.post(
        f"{HA}/auth/login_flow/{flow_id}",
        json={"client_id": CLIENT_ID, "username": USER, "password": PW},
    )
    r.raise_for_status()
    data = r.json()
    if data.get("type") != "create_entry":
        raise SystemExit(f"login failed: {json.dumps(data)}")
    return data["result"]


async def access_token(client: httpx.AsyncClient, code: str) -> str:
    r = await client.post(
        f"{HA}/auth/token",
        data={"grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID},
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def create_llt(access: str) -> str:
    ws_url = HA.replace("https", "wss", 1).replace("http", "ws", 1) + "/api/websocket"
    async with websockets.connect(ws_url) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": access}))
        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_ok":
            raise SystemExit(f"ws auth failed: {json.dumps(msg)}")
        await ws.send(json.dumps({"id": 1, "type": "auth/long_lived_access_token",
                                  "client_name": "vahub-hub", "lifespan": 3650}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == 1:
                if not msg.get("success"):
                    raise SystemExit(f"long-lived token failed: {json.dumps(msg)}")
                return msg["result"]


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        await wait_up(client)
        if await existing_token_ok(client):
            log("existing token is valid; nothing to do")
            return
        code = await onboard_or_login(client)
        access = await access_token(client, code)
        llt = await create_llt(access)
        os.makedirs(os.path.dirname(TOKEN_OUT), exist_ok=True)
        with open(TOKEN_OUT, "w") as f:
            f.write(llt)
        log(f"wrote long-lived token to {TOKEN_OUT}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit as e:
        log("ERROR:", e)
        sys.exit(1)
