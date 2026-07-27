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
├── agent/ scheduler/ stt/ tts/ storage/   placeholders for later milestones
config/
├── config.yaml        hub config
└── modules.d/
    └── time.yaml       the time module's manifest
modules/
└── time/               the time module (its own package and venv)
tests/
```

## The design in one line

```
intent -> LLM -> tool call -> policy -> MCP server -> actuator
```

Two ideas from the plan are already visible in the code, even though the agent
and the gate are not built yet.

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
../environment/sandbox.sh test
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

`POST /api/dev/call` calls a tool directly. It is enabled by
`web.dev_tools_endpoint` in `config.yaml` and is on in the sandbox. It exists to
validate the module contract in M1. It deliberately bypasses the policy gate
(M3) and the agent (M4). Once those exist and are the only callers, this endpoint
should be removed.

## Adding a module

1. Create `modules/<name>/` with its own `pyproject.toml` and an MCP server that
   exposes a reserved `__health` tool.
2. Add a manifest at `config/modules.d/<name>.yaml` pointing `command` at the
   module's venv interpreter.
3. Give it its own venv in the `Dockerfile`.

The supervisor discovers the manifest on the next start. There is no registration
step in code, and a module cannot grant itself permissions: the tool classes in
a manifest are a suggestion, and the binding authority will be `policy.yaml`.
