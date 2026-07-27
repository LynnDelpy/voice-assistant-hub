"""Module lifecycle: discover, spawn, handshake, health, restart.

State machine (from the plan):

    unconfigured -> starting -> ready
                        |         |
                        |         v
                        |     degraded -> ready
                        v         |
                     failed <-----+
                        |
                        v
                     stopped

`degraded` is deliberately separate from `failed`: a module whose backend is
temporarily gone (HA unreachable) should not enter a restart loop.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from . import metrics
from .bus import EventBus
from .logging import get_logger
from .mcpclient import McpClient, McpError

log = get_logger("supervisor")

HEALTH_TOOL = "__health"


class State(str, Enum):
    UNCONFIGURED = "unconfigured"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class Manifest:
    name: str
    version: str
    command: list[str]
    cwd: str | None
    uid: str | None
    pythonpath: str | None
    required: list[str]
    optional: list[str]
    health_interval_s: float
    health_timeout_s: float
    max_retries: int
    backoff_base_s: float
    reset_after_s: float
    startup_timeout_s: float
    tools_decl: dict
    redact: list[str]

    @staticmethod
    def from_yaml(path: Path) -> "Manifest":
        d = yaml.safe_load(path.read_text()) or {}
        cfg = d.get("config", {}) or {}
        rt = d.get("runtime", {}) or {}
        h = d.get("health", {}) or {}
        r = d.get("restart", {}) or {}
        a = d.get("audit", {}) or {}
        return Manifest(
            name=d["name"],
            version=str(d.get("version", "0")),
            command=list(d["command"]),
            cwd=rt.get("cwd"),
            uid=rt.get("uid"),
            pythonpath=rt.get("pythonpath"),  # dev convenience; unused for installed modules
            required=list(cfg.get("required", []) or []),
            optional=list(cfg.get("optional", []) or []),
            health_interval_s=float(h.get("interval_s", 30)),
            health_timeout_s=float(h.get("timeout_s", 5)),
            max_retries=int(r.get("max_retries", 5)),
            backoff_base_s=float(r.get("backoff_base_s", 2)),
            reset_after_s=float(r.get("reset_after_s", 600)),
            startup_timeout_s=float(r.get("startup_timeout_s", 20)),
            tools_decl=d.get("tools", {}) or {},
            redact=list(a.get("redact", []) or []),
        )


@dataclass
class Module:
    manifest: Manifest
    state: State = State.UNCONFIGURED
    proc: asyncio.subprocess.Process | None = None
    client: McpClient | None = None
    tools: list[dict] = field(default_factory=list)
    last_error: str | None = None
    health: dict = field(default_factory=dict)
    restarts: int = 0
    ready_since: float | None = None
    stderr_ring: collections.deque = field(default_factory=lambda: collections.deque(maxlen=200))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Supervisor:
    def __init__(self, bus: EventBus, modules_dir: Path) -> None:
        self._bus = bus
        self._modules_dir = modules_dir
        self.modules: dict[str, Module] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping = False

    # --- discovery ---------------------------------------------------------
    def discover(self) -> None:
        """Read every manifest without spawning, so the dashboard can list
        modules that are present but not yet configured."""
        for path in sorted(self._modules_dir.glob("*.yaml")):
            try:
                man = Manifest.from_yaml(path)
            except Exception as e:
                log.error("manifest_invalid", path=str(path), error=str(e))
                continue
            mod = Module(manifest=man)
            missing = [k for k in man.required if k not in os.environ]
            mod.state = State.UNCONFIGURED if missing else State.STOPPED
            if missing:
                mod.last_error = f"missing config: {', '.join(missing)}"
            self.modules[man.name] = mod
            metrics.set_module_state(man.name, mod.state.value)
            log.info("module_discovered", module=man.name, state=mod.state.value)

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        for name, mod in self.modules.items():
            if mod.state == State.UNCONFIGURED:
                continue
            self._tasks[name] = asyncio.create_task(self._supervise(mod), name=f"supervise:{name}")

    async def _supervise(self, mod: Module) -> None:
        man = mod.manifest
        while not self._stopping:
            started_ok = await self._spawn_and_init(mod)
            if not started_ok:
                if not self._register_failure(mod):
                    return  # gave up, state already FAILED
                delay = man.backoff_base_s ** min(mod.restarts, 6)
                log.warning("module_restart_backoff", module=man.name, attempt=mod.restarts, delay_s=delay)
                await asyncio.sleep(delay)
                continue

            # running: watch health and the process together
            health_task = asyncio.create_task(self._health_loop(mod), name=f"health:{man.name}")
            assert mod.proc is not None
            exit_code = await mod.proc.wait()
            health_task.cancel()
            try:
                await health_task
            except (asyncio.CancelledError, Exception):
                # A health error must never take down the supervise loop, or the
                # module would be stuck: never restarted, never stopped.
                pass
            if mod.client:
                await mod.client.close()

            if self._stopping:
                return
            # unexpected exit -> maybe restart
            mod.last_error = f"process exited (code {exit_code})"
            log.warning("module_exited", module=man.name, code=exit_code)
            if not self._register_failure(mod):
                return
            delay = man.backoff_base_s ** min(mod.restarts, 6)
            await asyncio.sleep(delay)

    def _register_failure(self, mod: Module) -> bool:
        """Bump the restart counter, resetting it first if the module had been
        healthy long enough. Returns False when the retry budget is spent."""
        man = mod.manifest
        if mod.ready_since is not None and (time.monotonic() - mod.ready_since) >= man.reset_after_s:
            mod.restarts = 0
        mod.ready_since = None
        mod.restarts += 1
        if mod.restarts > man.max_retries:
            self._set_state(mod, State.FAILED)
            log.error("module_failed_permanently", module=man.name, restarts=mod.restarts)
            return False
        self._set_state(mod, State.STARTING)
        return True

    async def _spawn_and_init(self, mod: Module) -> bool:
        man = mod.manifest
        self._set_state(mod, State.STARTING)
        try:
            if man.cwd:
                Path(man.cwd).mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                *man.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._child_env(man),
                cwd=man.cwd,
                preexec_fn=self._preexec(man),
                limit=1024 * 1024,  # stdout line cap; a module flooding past this is treated as broken
            )
        except Exception as e:
            mod.last_error = f"spawn failed: {e}"
            log.error("module_spawn_failed", module=man.name, error=str(e))
            return False

        mod.proc = proc
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        asyncio.create_task(self._drain_stderr(mod, proc.stderr), name=f"stderr:{man.name}")
        client = McpClient(man.name, proc.stdin, proc.stdout, get_logger(f"mcp.{man.name}"))
        client.start()
        mod.client = client

        # Handshake within startup_timeout, else the start counts as failed and
        # the module does not hang forever in `starting`.
        try:
            async with asyncio.timeout(man.startup_timeout_s):
                await client.initialize(man.startup_timeout_s)
                mod.tools = await client.list_tools(man.startup_timeout_s)
        except (McpError, asyncio.TimeoutError, Exception) as e:
            mod.last_error = f"handshake failed: {e}"
            log.error("module_handshake_failed", module=man.name, error=str(e))
            await self._kill(proc)
            return False

        mod.ready_since = time.monotonic()
        self._set_state(mod, State.READY)
        log.info(
            "module_ready",
            module=man.name,
            server=client.server_info,
            tools=[t.get("name") for t in mod.tools],
        )
        return True

    async def _health_loop(self, mod: Module) -> None:
        man = mod.manifest
        assert mod.client is not None
        while True:
            await asyncio.sleep(man.health_interval_s)
            try:
                # Take the module lock so a probe never runs concurrently with a
                # real tool call (the "one in-flight call per module" invariant).
                async with mod.lock:
                    raw = await mod.client.call_tool(HEALTH_TOOL, {}, man.health_timeout_s)
                if not isinstance(raw, dict):
                    # An untrusted module can return a non-object result; treat it
                    # as unhealthy rather than crashing the task.
                    mod.health = {"ok": False, "detail": "malformed health result"}
                    self._set_state(mod, State.DEGRADED)
                    continue
                payload = _extract(raw)
                mod.health = payload if isinstance(payload, dict) else {"raw": payload}
                ok = raw.get("isError") is not True and (
                    not isinstance(payload, dict) or payload.get("ok", True)
                )
                self._set_state(mod, State.READY if ok else State.DEGRADED)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                mod.health = {"ok": False, "detail": "health timeout"}
                self._set_state(mod, State.DEGRADED)
            except McpError as e:
                mod.health = {"ok": False, "detail": e.message}
                self._set_state(mod, State.DEGRADED)
            except Exception as e:
                # Untrusted output or any other error must not kill the loop.
                mod.health = {"ok": False, "detail": f"health error: {e}"}
                self._set_state(mod, State.DEGRADED)

    # --- process environment ----------------------------------------------
    def _child_env(self, man: Manifest) -> dict[str, str]:
        """Minimal environment: only what the module declared, plus the bare
        essentials. No blanket os.environ passthrough, so the time module never
        sees an HA token it does not need."""
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        for key in (*man.required, *man.optional):
            if key in os.environ:
                env[key] = os.environ[key]
        if man.pythonpath:  # dev only; installed modules do not need this
            env["PYTHONPATH"] = man.pythonpath
        return env

    def _preexec(self, man: Manifest):
        """Drop to the module's own UID before exec. Best-effort: only when we
        run as root and the user exists. In the Docker sandbox we run unprivileged,
        so this is a no-op there; it is a prod (LXC) hardening step."""
        uid = man.uid
        if not uid or os.geteuid() != 0:
            return None
        try:
            import pwd

            pw = pwd.getpwnam(uid)
        except KeyError:
            log.warning("module_uid_unknown", module=man.name, uid=uid)
            return None

        def _fn() -> None:
            # Clear root's supplementary groups before dropping, else the module
            # keeps every group root belonged to (group 0, device groups, ...).
            os.initgroups(uid, pw.pw_gid)
            os.setgid(pw.pw_gid)
            os.setuid(pw.pw_uid)

        return _fn

    async def _drain_stderr(self, mod: Module, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.rstrip().decode("utf-8", "replace")
                mod.stderr_ring.append(text)
                self._bus.publish("module.log", {"module": mod.manifest.name, "line": text})
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            pass

    # --- state + shutdown --------------------------------------------------
    def _set_state(self, mod: Module, new: State) -> None:
        if mod.state == new:
            return
        old = mod.state
        mod.state = new
        metrics.set_module_state(mod.manifest.name, new.value)
        self._bus.publish(
            "module.state_changed",
            {"module": mod.manifest.name, "state": new.value, "previous": old.value, "last_error": mod.last_error},
        )
        log.info("module_state_changed", module=mod.manifest.name, previous=old.value, state=new.value)

    async def _kill(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def stop(self) -> None:
        self._stopping = True
        for name, task in self._tasks.items():
            mod = self.modules[name]
            if mod.proc and mod.proc.returncode is None:
                await self._kill(mod.proc)
            if mod.client:
                await mod.client.close()
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for mod in self.modules.values():
            if mod.state not in (State.UNCONFIGURED, State.FAILED):
                self._set_state(mod, State.STOPPED)


def _extract(raw):
    """Pull a usable payload out of an MCP tools/call result (untrusted)."""
    if not isinstance(raw, dict):
        return raw
    if raw.get("structuredContent") is not None:
        return raw["structuredContent"]
    parts = []
    for block in raw.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    text = "\n".join(parts)
    # Only reinterpret as JSON when it clearly is an object/array, so a tool
    # whose real output is the string "123" or "true" keeps its type.
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return text
    return text
