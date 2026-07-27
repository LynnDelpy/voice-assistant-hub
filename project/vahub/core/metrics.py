"""Prometheus metrics.

Per the plan: latency histograms per stage, a counter for policy decisions
(added with the gate in M3), a gauge for module states, and a counter for
dropped bus messages. Without per-stage split you cannot tell whether voice
latency sits in STT or in the model.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# --- bus -------------------------------------------------------------------
BUS_DROPPED = Counter(
    "vahub_bus_dropped_total",
    "Bus messages dropped or subscribers disconnected, per topic",
    ["topic"],
)

# --- modules ---------------------------------------------------------------
_STATES = ("unconfigured", "starting", "ready", "degraded", "failed", "stopped")
MODULE_STATE = Gauge(
    "vahub_module_state",
    "1 for the module's current state, 0 otherwise",
    ["module", "state"],
)


def set_module_state(module: str, state: str) -> None:
    for s in _STATES:
        MODULE_STATE.labels(module=module, state=s).set(1.0 if s == state else 0.0)


# --- tool calls ------------------------------------------------------------
TOOL_CALLS = Counter(
    "vahub_tool_calls_total",
    "Tool calls by result",
    ["module", "tool", "result"],
)
TOOL_LATENCY = Histogram(
    "vahub_tool_latency_seconds",
    "Tool call latency",
    ["module", "tool"],
)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
