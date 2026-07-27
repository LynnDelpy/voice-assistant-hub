"""The policy gate. Default deny, checked on every tool call.

This is a security boundary in code, not in the prompt. It sits in front of the
module API so the agent, the scheduler, and the dev endpoint all pass through it.
The important difference from a tool-level allowlist is that it checks
*arguments*: with a Home Assistant admin token, whatever gets through here has
full control, so `light_turn_on` is only allowed for the specific entities the
regex permits, and locks require an out-of-band confirmation.

Constraint types (all that v1 needs): in, matches (regex), range [min,max],
max_len. An argument with no constraint entry is rejected, not waved through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Decision:
    outcome: str  # "allow" | "deny" | "confirm"
    reason: str = ""
    klass: str = ""


@dataclass
class Principal:
    confirm: list[str] = field(default_factory=list)  # classes needing confirmation
    deny: list[str] = field(default_factory=list)  # glob patterns of module.tool


class Gate:
    def __init__(self, policy: dict[str, Any]) -> None:
        self._default_deny = (policy.get("default", "deny") != "allow")
        self._rules: dict[str, dict] = policy.get("rules", {}) or {}
        self._principals: dict[str, Principal] = {}
        for name, spec in (policy.get("principals", {}) or {}).items():
            self._principals[name] = Principal(
                confirm=list((spec or {}).get("confirm", []) or []),
                deny=list((spec or {}).get("deny", []) or []),
            )

    @staticmethod
    def load(path: Path) -> "Gate":
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        return Gate(data or {})

    def _principal(self, name: str) -> Principal:
        return self._principals.get(name, Principal())

    def visible_to(self, principal: str, module: str, tool: str) -> bool:
        """Catalog filter: would this tool ever be allowed for this principal
        (ignoring arguments)? Tools that fail here are hidden from the agent so
        it does not plan calls that will only die at the gate."""
        key = f"{module}.{tool}"
        if key not in self._rules and self._default_deny:
            return False
        if any(fnmatch(key, pat) for pat in self._principal(principal).deny):
            return False
        return True

    def evaluate(self, principal: str, module: str, tool: str, args: dict) -> Decision:
        key = f"{module}.{tool}"
        rule = self._rules.get(key)
        if rule is None:
            if self._default_deny:
                return Decision("deny", f"no rule for {key} (default deny)")
            rule = {}
        p = self._principal(principal)
        if any(fnmatch(key, pat) for pat in p.deny):
            return Decision("deny", f"{key} denied for principal {principal}")

        klass = rule.get("class", "read")
        constraints = rule.get("constraints", {}) or {}
        # Reject any argument the rule does not describe.
        for arg_name, value in (args or {}).items():
            if arg_name not in constraints:
                return Decision("deny", f"argument not permitted: {arg_name}", klass)
            ok, why = _check(constraints[arg_name] or {}, value)
            if not ok:
                return Decision("deny", f"argument {arg_name}: {why}", klass)

        if klass in p.confirm:
            return Decision("confirm", f"{klass} action requires confirmation", klass)
        return Decision("allow", "", klass)


def _check(constraint: dict, value: Any) -> tuple[bool, str]:
    if "in" in constraint and value not in constraint["in"]:
        return False, f"{value!r} not in {constraint['in']}"
    if "matches" in constraint:
        if not isinstance(value, str) or re.search(constraint["matches"], value) is None:
            return False, f"{value!r} does not match {constraint['matches']!r}"
    if "range" in constraint:
        lo, hi = constraint["range"]
        try:
            if not (lo <= value <= hi):
                return False, f"{value!r} out of range [{lo}, {hi}]"
        except TypeError:
            return False, f"{value!r} is not comparable to range"
    if "max_len" in constraint:
        if len(str(value)) > constraint["max_len"]:
            return False, f"longer than {constraint['max_len']}"
    return True, ""
