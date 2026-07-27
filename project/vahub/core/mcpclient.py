"""A minimal MCP client speaking JSON-RPC 2.0 over stdio.

The Supervisor owns the subprocess (spawn, uid, restart, health). This class
owns only the wire protocol and the request/response id map. Keeping them apart
is the whole point of the module contract: swap stdio for HTTP later and the
Supervisor barely changes.

MCP stdio framing is newline-delimited JSON: one JSON-RPC message per line, no
embedded newlines. stderr is for logs and is read separately by the Supervisor.

Capability profile (a security decision, see the plan's MCP section):
we announce *no* client capabilities. No `roots`, no `sampling`. If a module
issues a server->client request anyway, we refuse it here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

# A protocol revision current MCP servers accept. We send this and, for the
# tools-only surface we use, tolerate the server echoing a different one.
PROTOCOL_VERSION = "2025-06-18"


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class McpClient:
    def __init__(
        self,
        name: str,
        stdin: asyncio.StreamWriter,
        stdout: asyncio.StreamReader,
        log: Any,
    ) -> None:
        self._name = name
        self._stdin = stdin
        self._stdout = stdout
        self._log = log
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: asyncio.Task | None = None
        self._closed = False
        self.server_info: dict = {}
        self.server_capabilities: dict = {}

    def start(self) -> None:
        self._read_task = asyncio.create_task(self._read_loop(), name=f"mcp-read:{self._name}")

    # --- read side ---------------------------------------------------------
    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                try:
                    line = await self._stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as e:
                    # An oversized line (a module flooding stdout past the buffer
                    # limit) leaves the stream in an unrecoverable state. Treat it
                    # as a broken connection rather than crashing the task.
                    self._log.warning("mcp_line_too_long", error=str(e))
                    break
                if not line:
                    break  # EOF: the process is gone
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    self._log.warning("mcp_bad_json", raw=line[:200].decode("utf-8", "replace"))
                    continue
                # One malformed message must never take down the whole read loop.
                try:
                    if isinstance(msg, dict):
                        self._dispatch(msg)
                    else:
                        self._log.warning("mcp_non_object_message")
                except Exception as e:  # pragma: no cover - defensive
                    self._log.warning("mcp_dispatch_error", error=str(e))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            self._log.warning("mcp_read_loop_error", error=str(e))
        finally:
            self._fail_all(McpError(-1, "connection closed"))

    def _dispatch(self, msg: dict) -> None:
        mid = msg.get("id")
        # A JSON-RPC id must be a string or number. Reject anything else (e.g. a
        # list) before using it as a dict key, so a malformed id cannot raise.
        id_ok = isinstance(mid, (str, int, float)) and not isinstance(mid, bool)
        # response to one of our requests
        if "id" in msg and id_ok and ("result" in msg or "error" in msg):
            fut = self._pending.pop(mid, None)
            if fut is None:
                # Orphan: the caller already timed out and dropped its waiter.
                # Discard it. Never hand it to the next caller. This is exactly
                # the "light in the living room turns on when I ask about the
                # hallway" bug the plan warns about.
                self._log.debug("mcp_orphan_response", id=msg.get("id"))
                return
            if not fut.done():
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(McpError(err.get("code", -1), err.get("message", ""), err.get("data")))
                else:
                    fut.set_result(msg["result"])
            return

        method = msg.get("method")
        # server -> client request: refused, we announced no such capability
        if method and "id" in msg:
            self._log.warning("mcp_server_request_refused", method=method)
            self._send({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32601, "message": f"capability not supported: {method}"},
            })
            return
        # notification (server -> client): received, logged, not acted on in v1
        if method:
            self._log.debug("mcp_notification", method=method)

    # --- write side --------------------------------------------------------
    def _send(self, msg: dict) -> None:
        if self._closed or self._stdin.is_closing():
            raise McpError(-1, "transport closing")
        self._stdin.write((json.dumps(msg) + "\n").encode("utf-8"))

    async def request(self, method: str, params: dict | None, timeout_s: float) -> Any:
        if self._closed:
            raise McpError(-1, "client closed")
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        except Exception:
            # If the write fails, clean up our own waiter rather than leaking it.
            self._pending.pop(rid, None)
            raise
        try:
            return await asyncio.wait_for(fut, timeout_s)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # Timeout is not cancellation. Drop the waiter; a late reply with
            # this id will find nothing in _pending and be discarded.
            self._pending.pop(rid, None)
            raise

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # --- high level --------------------------------------------------------
    async def initialize(self, timeout_s: float) -> dict:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},  # announce nothing: no roots, no sampling
                "clientInfo": {"name": "vahub", "version": "0.1.0"},
            },
            timeout_s,
        )
        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})
        if "tools" not in self.server_capabilities:
            raise McpError(-1, "module announces no tools capability")
        self.notify("notifications/initialized")
        return result

    async def list_tools(self, timeout_s: float) -> list[dict]:
        result = await self.request("tools/list", {}, timeout_s)
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict, timeout_s: float) -> dict:
        return await self.request("tools/call", {"name": name, "arguments": arguments}, timeout_s)

    # --- teardown ----------------------------------------------------------
    def _fail_all(self, exc: Exception) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def close(self) -> None:
        self._closed = True
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        self._fail_all(McpError(-1, "closed"))
