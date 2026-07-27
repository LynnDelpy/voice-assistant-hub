"""A minimal MCP server over stdio, stdlib only (no mcp SDK).

Used by the supervisor test to exercise the hub's client and the full contract
(handshake, tools/list, tools/call, __health) without pulling in the SDK.
"""

import json
import sys


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "0"},
            }})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "echo", "description": "echo text back",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
                {"name": "wrapped", "description": "FastMCP-style structuredContent",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "scalar", "description": "non-object result (misbehaving module)",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "__health", "description": "health",
                 "inputSchema": {"type": "object", "properties": {}}},
            ]}})
        elif method == "tools/call":
            p = msg.get("params", {})
            name = p.get("name")
            args = p.get("arguments", {})
            if name == "__health":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": json.dumps({"ok": True, "backend": "n/a"})}],
                    "isError": False}})
            elif name == "echo":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": args.get("text", "")}], "isError": False}})
            elif name == "wrapped":
                # Mirror FastMCP: a scalar return is exposed as structuredContent {"result": value}.
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "wrapped-value"}],
                    "structuredContent": {"result": "wrapped-value"}, "isError": False}})
            elif name == "scalar":
                # A misbehaving module: the tools/call result is not an object at all.
                send({"jsonrpc": "2.0", "id": mid, "result": "i am not an object"})
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown tool"}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    main()
