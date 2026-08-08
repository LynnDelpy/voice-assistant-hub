# project (vahub)

The hub itself. This is the code you build on. The sandbox in `../environment`
runs it, but the code has no dependency on Docker and can run on a bare Python
3.12 with the dependencies installed.

## Layout

```
vahub/
├── core/
│   ├── config.py      load and validate config (pydantic-settings, strict)
│   ├── bus.py         in-process pub/sub with decided backpressure
│   ├── logging.py     structlog JSON setup
│   ├── metrics.py     Prometheus metrics
│   ├── mcpclient.py   MCP client over stdio (protocol + request-id map)
│   ├── supervisor.py  module lifecycle: spawn, handshake, health, restart
│   ├── moduleapi.py   the one call() path used by agent and scheduler
│   ├── registry.py    aggregated, namespaced tool catalog
│   └── runtime.py     wires it together, owns startup and shutdown
├── web/
│   ├── api.py         REST, WebSocket, and the status page
│   └── static/        the status page
├── agent/          the LLM loop, the policy gate, LLM adapters
├── scheduler/      cron routines
├── stt/ tts/       speech adapters
└── storage/        SQLite (audit log, conversations, budgets)
modules/            one MCP server per module, each with its own venv
├── time/  homeassistant/  transit/  notify/
tests/
```

Configuration is not in here: it is the single `../.env` (every knob) plus
`../config/` (policy, schedules, module manifests).

## The design in one line

```
intent -> LLM -> tool call -> policy -> MCP server -> actuator
```

Two ideas from the plan are worth calling out in the code.

* **The module contract is a transport, not a function call.** The hub never
  imports a module. The supervisor spawns it as a subprocess and talks MCP over
  stdio (`mcpclient.py`). Moving a module to its own container later means
  switching the transport to HTTP, without changing the API above it.
* **The MCP capability profile is a security decision.** The client announces no
  `roots` and no `sampling`. A module that issues a server-to-client request is
  refused in `mcpclient.py`. A compromised module cannot make the hub spend LLM
  budget on its behalf.

## Running the tests

Inside the sandbox:

```bash
../vahub test
```

Or locally, with a Python 3.12 virtualenv:

```bash
cd project
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q
```

The supervisor test spawns a tiny stdlib-only fake MCP server, so it exercises
the full contract (spawn, handshake, tool registry, tool call) without the MCP
SDK installed.

## The dev call endpoint

`POST /api/dev/call` calls a tool directly, without the agent. It is enabled by
`VAHUB_WEB__DEV_TOOLS_ENDPOINT` in `../.env` and is on in the sandbox. It is
useful for testing a module in isolation (`../vahub push` uses it), but it is an
unauthenticated remote-control endpoint, so turn it off if the port is reachable
by anyone else. It still goes through the policy gate, as principal `dev`.

## Adding a module

1. Create `modules/<name>/` with its own `pyproject.toml` and an MCP server that
   exposes a reserved `__health` tool.
2. Add a manifest at `../config/modules.d/<name>.yaml` whose `command` points at
   `/opt/vh/mod/<name>/bin/python`, where `<name>` is the module directory name.
3. Add its tools to `../config/policy.yaml`, or the gate denies them by default.

The image builds one venv per directory under `modules/`, so no Dockerfile
change is needed. `tests/test_manifests.py` checks that the manifest, the module
directory and the policy agree.

The supervisor discovers the manifest on the next start. There is no registration
step in code, and a module cannot grant itself permissions: the tool classes in
a manifest are a suggestion, and the binding authority will be `policy.yaml`.
