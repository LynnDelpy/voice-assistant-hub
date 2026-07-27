"""Aggregates the tools of all `ready` modules into one namespaced catalog.

Reserved tools (`__health`) never appear here. The agent-facing catalog will
additionally be intersected with the policy allowlist (M3): a tool the agent may
never call should not even be visible to it. For now `agent_catalog` == the full
ready catalog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .supervisor import State, Supervisor

if TYPE_CHECKING:
    from ..agent.policy import Gate


class Registry:
    def __init__(self, supervisor: Supervisor, gate: "Gate | None" = None) -> None:
        self._sup = supervisor
        self._gate = gate

    def list_tools(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for mod in self._sup.modules.values():
            if mod.state != State.READY:
                continue
            for t in mod.tools:
                name = t.get("name", "")
                if name.startswith("__"):  # reserved, hidden from the catalog
                    continue
                out.append(
                    {
                        "name": f"{mod.manifest.name}.{name}",
                        "module": mod.manifest.name,
                        "tool": name,
                        "description": t.get("description"),
                        "input_schema": t.get("inputSchema"),
                    }
                )
        return out

    def agent_catalog(self, principal: str = "agent") -> list[dict[str, Any]]:
        """The tools the agent may see: the ready catalog intersected with the
        policy allowlist. A tool the principal can never call is not shown, so
        the model does not plan calls that would only die at the gate."""
        tools = self.list_tools()
        if self._gate is None:
            return tools
        return [t for t in tools if self._gate.visible_to(principal, t["module"], t["tool"])]

    def unavailable_modules(self) -> list[str]:
        """Modules the agent should be told about (degraded/failed) so it does
        not hallucinate explanations for a missing backend."""
        return [
            m.manifest.name
            for m in self._sup.modules.values()
            if m.state in (State.DEGRADED, State.FAILED)
        ]
