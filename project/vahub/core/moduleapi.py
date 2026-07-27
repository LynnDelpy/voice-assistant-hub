"""The single call path that Agent and Scheduler both use, now gated.

Order of operations for every call:
  validate -> policy gate -> (allow | deny | confirm) -> dispatch -> audit

Guarantees from the plan:
  * module not ready -> immediate structured error, never a hang
  * timeout is mandatory (has a default)
  * the result is always structured (ok/error + payload), never a raw exception
  * one in-flight call per module (serialised via the module lock)
  * default deny: the gate decides, not the prompt
  * destructive actions are confirmed out-of-band with frozen arguments
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from . import metrics
from .mcpclient import McpError
from .supervisor import State, Supervisor, _extract

if TYPE_CHECKING:
    from ..agent.policy import Gate
    from ..storage.store import Store
    from .bus import EventBus


class ModuleAPI:
    def __init__(
        self,
        supervisor: Supervisor,
        gate: "Gate | None" = None,
        store: "Store | None" = None,
        bus: "EventBus | None" = None,
        confirm_ttl_s: float = 60.0,
    ) -> None:
        self._sup = supervisor
        self._gate = gate
        self._store = store
        self._bus = bus
        self._confirm_ttl_s = confirm_ttl_s

    async def call(
        self,
        module: str,
        tool: str,
        args: dict[str, Any] | None = None,
        timeout_s: float = 10.0,
        principal: str = "agent",
    ) -> dict[str, Any]:
        args = args or {}
        mod = self._sup.modules.get(module)
        if mod is None:
            return {"ok": False, "error": "unknown_module", "detail": module}
        if mod.state != State.READY or mod.client is None:
            return {"ok": False, "error": "module_not_ready", "detail": mod.state.value}
        if tool.startswith("__"):
            return {"ok": False, "error": "reserved_tool", "detail": tool}
        if not any(t.get("name") == tool for t in mod.tools):
            return {"ok": False, "error": "unknown_tool", "detail": tool}
        if not isinstance(args, dict):
            return {"ok": False, "error": "bad_args", "detail": "args must be an object"}

        # --- policy gate -------------------------------------------------
        if self._gate is not None:
            decision = self._gate.evaluate(principal, module, tool, args)
            if decision.outcome == "deny":
                await self._audit(principal, module, tool, args, "deny", "denied", None)
                return {"ok": False, "error": "policy_denied", "detail": decision.reason}
            if decision.outcome == "confirm":
                pid = await self._create_pending(principal, module, tool, args, decision)
                await self._audit(principal, module, tool, args, "confirm", "pending", None)
                return {
                    "ok": False,
                    "error": "confirmation_required",
                    "pending_id": pid,
                    "detail": decision.reason,
                }

        return await self._dispatch(mod, module, tool, args, timeout_s, principal, "allow")

    async def confirm(self, pending_id: str, subject: str | None = None) -> dict[str, Any]:
        """Execute a previously-confirmed destructive call with its FROZEN
        arguments (not whatever is in the context now)."""
        if self._store is None:
            return {"ok": False, "error": "no_store"}
        pc = await self._store.get_pending(pending_id)
        if pc is None:
            return {"ok": False, "error": "unknown_pending"}
        if pc["status"] != "pending":
            return {"ok": False, "error": "not_pending", "detail": pc["status"]}
        if time.time() > pc["expires_at"]:
            await self._store.set_pending_status(pending_id, "expired")
            return {"ok": False, "error": "expired"}

        module, tool = pc["module"], pc["tool"]
        import json

        args = json.loads(pc["args"])
        mod = self._sup.modules.get(module)
        if mod is None or mod.state != State.READY or mod.client is None:
            return {"ok": False, "error": "module_not_ready"}
        await self._store.set_pending_status(pending_id, "confirmed")
        # subject (from the proxy's X-Auth-Subject) is recorded as the principal
        return await self._dispatch(mod, module, tool, args, 10.0, subject or "user", "allow-confirmed")

    # --- internals ---------------------------------------------------------
    async def _dispatch(
        self, mod, module: str, tool: str, args: dict, timeout_s: float, principal: str, decision: str
    ) -> dict[str, Any]:
        async with mod.lock:  # one in-flight call per module
            t0 = time.monotonic()
            try:
                raw = await mod.client.call_tool(tool, args, timeout_s)
            except asyncio.TimeoutError:
                metrics.TOOL_CALLS.labels(module=module, tool=tool, result="timeout").inc()
                await self._audit(principal, module, tool, args, decision, "timeout", _ms(t0))
                return {"ok": False, "error": "timeout", "detail": f"{timeout_s}s"}
            except McpError as e:
                metrics.TOOL_CALLS.labels(module=module, tool=tool, result="error").inc()
                await self._audit(principal, module, tool, args, decision, "error", _ms(t0))
                return {"ok": False, "error": "mcp_error", "detail": e.message}
            except Exception as e:
                metrics.TOOL_CALLS.labels(module=module, tool=tool, result="error").inc()
                await self._audit(principal, module, tool, args, decision, "error", _ms(t0))
                return {"ok": False, "error": "internal", "detail": str(e)}
            finally:
                metrics.TOOL_LATENCY.labels(module=module, tool=tool).observe(time.monotonic() - t0)

        if not isinstance(raw, dict):
            metrics.TOOL_CALLS.labels(module=module, tool=tool, result="bad_result").inc()
            await self._audit(principal, module, tool, args, decision, "bad_result", _ms(t0))
            return {"ok": False, "error": "bad_result", "detail": raw}
        if raw.get("isError"):
            metrics.TOOL_CALLS.labels(module=module, tool=tool, result="tool_error").inc()
            await self._audit(principal, module, tool, args, decision, "tool_error", _ms(t0))
            return {"ok": False, "error": "tool_error", "detail": _extract(raw)}

        metrics.TOOL_CALLS.labels(module=module, tool=tool, result="ok").inc()
        await self._audit(principal, module, tool, args, decision, "ok", _ms(t0))
        return {"ok": True, "result": _unwrap(_extract(raw))}

    async def _create_pending(self, principal, module, tool, args, decision) -> str:
        pid = uuid.uuid4().hex
        if self._store is not None:
            await self._store.create_pending(pid, principal, module, tool, args, self._confirm_ttl_s)
        if self._bus is not None:
            self._bus.publish(
                "policy.confirmation_required",
                {"pending_id": pid, "module": module, "tool": tool, "reason": decision.reason,
                 "principal": principal, "ttl_s": self._confirm_ttl_s},
            )
        return pid

    async def _audit(self, principal, module, tool, args, decision, result, duration_ms) -> None:
        if self._store is None:
            return
        redacted = _redact(args, self._redact_keys(module))
        try:
            await self._store.record_tool_call(principal, module, tool, redacted, decision, result, duration_ms)
        except Exception:  # audit must not break the call path
            pass

    def _redact_keys(self, module: str) -> list[str]:
        mod = self._sup.modules.get(module)
        return list(mod.manifest.redact) if mod else []


def _ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 2)


def _redact(args: dict, keys: list[str]) -> dict:
    if not keys:
        return args
    return {k: ("***" if k in keys else v) for k, v in args.items()}


def _unwrap(payload: Any) -> Any:
    """FastMCP wraps a scalar tool return as {"result": value}. Peel that single
    envelope so callers get the value directly instead of {"result": {...}}."""
    if isinstance(payload, dict) and set(payload.keys()) == {"result"}:
        return payload["result"]
    return payload
