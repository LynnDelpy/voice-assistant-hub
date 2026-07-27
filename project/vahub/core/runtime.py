"""Core/Runtime: the process everything else lives in.

Startup order matters and shutdown is the reverse. On SIGTERM: stop accepting
new work, let the web server drain, stop the scheduler and modules (SIGTERM then
SIGKILL), close the database, then exit.
"""

from __future__ import annotations

import asyncio
import signal

import uvicorn

from ..agent.llm.base import build_adapter
from ..agent.loop import AgentLoop
from ..agent.policy import Gate
from ..agent.session import SessionStore
from ..scheduler.scheduler import Scheduler
from ..storage.store import Store
from ..stt import build_stt
from ..tts import build_tts
from .bus import EventBus
from .config import Config, load_config
from .logging import configure as configure_logging
from .logging import get_logger
from .moduleapi import ModuleAPI
from .registry import Registry
from .supervisor import Supervisor


class Runtime:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bus = EventBus()
        self.gate = Gate.load(config.policy_file)
        self.store = Store(config.db_path)
        self.supervisor = Supervisor(self.bus, config.modules_dir)
        self.moduleapi = ModuleAPI(
            self.supervisor, gate=self.gate, store=self.store, bus=self.bus, confirm_ttl_s=config.confirm_ttl_s
        )
        self.registry = Registry(self.supervisor, gate=self.gate)
        self.llm = build_adapter(config.llm)
        self.stt = build_stt(config.stt)
        self.tts = build_tts(config.tts)
        self.sessions = SessionStore()
        self.agent = AgentLoop(
            self.registry, self.moduleapi, self.llm, config.budgets,
            system_prompt=config.llm.system_prompt, store=self.store, bus=self.bus, timezone=config.timezone,
        )
        self.scheduler = Scheduler(self.moduleapi, self.bus, config.schedules_file, config.timezone)
        self._log = get_logger("runtime")
        self._stop = asyncio.Event()
        self._server: uvicorn.Server | None = None
        self._bg: list[asyncio.Task] = []

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        await self.store.open()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_stop)

        self._log.info(
            "hub_starting",
            modules_dir=str(self.config.modules_dir),
            llm_provider=self.config.llm.provider,
        )
        self.supervisor.discover()
        await self.supervisor.start()
        self.scheduler.start()
        self._bg.append(asyncio.create_task(self._persist_module_state(), name="persist-state"))

        from ..web.api import create_app

        app = create_app(self)
        server_cfg = uvicorn.Config(
            app, host=self.config.web.host, port=self.config.web.port, log_config=None, lifespan="on"
        )
        self._server = uvicorn.Server(server_cfg)
        server_task = asyncio.create_task(self._server.serve(), name="web")
        self._log.info("hub_ready", bind=f"{self.config.web.host}:{self.config.web.port}")

        await self._stop.wait()
        self._log.info("hub_stopping")

        # graceful shutdown, reverse order
        if self._server is not None:
            self._server.should_exit = True
        await server_task
        self.scheduler.stop()
        for task in self._bg:
            task.cancel()
        await self.supervisor.stop()
        for adapter in (self.llm, self.stt, self.tts):
            try:
                await adapter.aclose()
            except Exception:  # pragma: no cover - defensive
                pass
        await self.store.close()
        self._log.info("hub_stopped")

    async def _persist_module_state(self) -> None:
        """Mirror module state transitions into the DB for post-mortem."""
        sub = self.bus.subscribe("module.state_changed")
        try:
            async for ev in sub.events():
                try:
                    await self.store.save_module_state(ev["module"], ev["state"], ev.get("last_error"))
                except Exception:  # pragma: no cover
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            self.bus.unsubscribe(sub)


async def main() -> None:
    config = load_config()
    configure_logging(config.log_level)
    await Runtime(config).run()
