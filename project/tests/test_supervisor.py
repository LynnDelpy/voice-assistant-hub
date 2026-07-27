"""End-to-end contract test: spawn a module, handshake, register tools, call one.

Uses the stdlib fake MCP server so it runs without the mcp SDK installed.
"""

import asyncio
import sys
from pathlib import Path

import pytest

from vahub.core.bus import EventBus
from vahub.core.moduleapi import ModuleAPI
from vahub.core.supervisor import State, Supervisor

FAKE = Path(__file__).parent / "fake_mcp_server.py"


def _write_manifest(modules_dir: Path) -> None:
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "fake.yaml").write_text(
        f"""
name: fake
version: 0.1.0
transport: stdio
command: ["{sys.executable}", "{FAKE}"]
config: {{ required: [], optional: [] }}
runtime: {{ uid: null, cwd: null }}
health: {{ interval_s: 1, timeout_s: 3 }}
restart: {{ max_retries: 2, backoff_base_s: 1, reset_after_s: 5, startup_timeout_s: 10 }}
tools:
  echo: {{ class: read }}
"""
    )


async def _wait_state(sup: Supervisor, name: str, state: State, timeout: float = 15.0) -> None:
    async with asyncio.timeout(timeout):
        while sup.modules[name].state != state:
            await asyncio.sleep(0.05)


async def test_spawn_handshake_and_call(tmp_path):
    modules_dir = tmp_path / "modules.d"
    _write_manifest(modules_dir)

    bus = EventBus()
    sup = Supervisor(bus, modules_dir)
    sup.discover()
    assert "fake" in sup.modules
    await sup.start()
    try:
        await _wait_state(sup, "fake", State.READY)

        # tools registered, reserved tool hidden from the module's tool list surface
        names = [t["name"] for t in sup.modules["fake"].tools]
        assert "echo" in names and "__health" in names

        api = ModuleAPI(sup)
        ok = await api.call("fake", "echo", {"text": "hallo"}, timeout_s=5)
        assert ok == {"ok": True, "result": "hallo"}

        # FastMCP-style {"result": value} envelope is unwrapped, not double-wrapped
        wrapped = await api.call("fake", "wrapped", {}, timeout_s=5)
        assert wrapped == {"ok": True, "result": "wrapped-value"}

        # a non-object tool result stays structured (never a raw exception)
        scalar = await api.call("fake", "scalar", {}, timeout_s=5)
        assert scalar["ok"] is False and scalar["error"] == "bad_result"

        # reserved tools cannot be called through the module API
        reserved = await api.call("fake", "__health", {}, timeout_s=5)
        assert reserved["ok"] is False and reserved["error"] == "reserved_tool"

        # a tool the module never advertised is rejected before dispatch
        unknown = await api.call("fake", "not_a_tool", {}, timeout_s=5)
        assert unknown["ok"] is False and unknown["error"] == "unknown_tool"

        # unknown module -> structured error, no hang
        missing = await api.call("nope", "echo", {}, timeout_s=1)
        assert missing["ok"] is False and missing["error"] == "unknown_module"
    finally:
        await sup.stop()

    assert sup.modules["fake"].state == State.STOPPED
