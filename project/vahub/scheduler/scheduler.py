"""Deterministic routines (the morning routine).

Runs steps through the module API with principal="scheduler", so the gate still
applies, but without the agent and without an LLM: no latency, no token cost.
The scheduler gets its own principal in policy.yaml (it may do more than the
agent, e.g. skip confirmation, but not everything).

Rules from the plan: overlap is skip (never queue), a failing step aborts the
routine and lands on the bus, and the timezone is explicit so daylight-saving
jumps are deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..core.logging import get_logger

if TYPE_CHECKING:
    from ..core.bus import EventBus
    from ..core.moduleapi import ModuleAPI

log = get_logger("scheduler")


class Scheduler:
    def __init__(self, moduleapi: "ModuleAPI", bus: "EventBus", schedules_file: Path, timezone: str) -> None:
        self._api = moduleapi
        self._bus = bus
        self._file = schedules_file
        self._tz = timezone
        self._schedules: dict[str, dict] = {}
        self._sched = AsyncIOScheduler(timezone=timezone)

    def load(self) -> None:
        if not self._file.exists():
            log.info("no_schedules_file", path=str(self._file))
            return
        data = yaml.safe_load(self._file.read_text()) or {}
        for entry in data.get("schedules", []) or []:
            self._schedules[entry["id"]] = entry

    def start(self) -> None:
        self.load()
        for sid, entry in self._schedules.items():
            cron = entry.get("cron")
            if not cron:
                continue
            try:
                trigger = CronTrigger.from_crontab(cron, timezone=self._tz)
            except ValueError as e:
                log.error("bad_cron", schedule=sid, cron=cron, error=str(e))
                continue
            # coalesce + max_instances=1 => overlaps are skipped, never queued.
            self._sched.add_job(
                self.run_schedule, trigger, args=[sid], id=sid, max_instances=1, coalesce=True,
                misfire_grace_time=30,
            )
            log.info("schedule_registered", schedule=sid, cron=cron)
        self._sched.start()

    def stop(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)

    def list_schedules(self) -> list[dict]:
        out = []
        for sid, entry in self._schedules.items():
            job = self._sched.get_job(sid)
            out.append({
                "id": sid,
                "cron": entry.get("cron"),
                "steps": len(entry.get("steps", []) or []),
                "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
            })
        return out

    async def run_schedule(self, sid: str) -> dict:
        entry = self._schedules.get(sid)
        if entry is None:
            return {"ok": False, "error": "unknown_schedule"}
        log.info("schedule_fired", schedule=sid)
        results = []
        for i, step in enumerate(entry.get("steps", []) or []):
            result = await self._api.call(
                module=step["module"],
                tool=step["tool"],
                args=step.get("args", {}) or {},
                timeout_s=float(step.get("timeout_s", 10)),
                principal="scheduler",
            )
            results.append({"step": i, "module": step["module"], "tool": step["tool"], "result": result})
            if not result.get("ok"):
                # A failing step aborts the routine and lands on the bus.
                self._bus.publish("schedule.fired", {"schedule": sid, "ok": False, "failed_step": i, "results": results})
                log.warning("schedule_step_failed", schedule=sid, step=i, error=result.get("error"))
                return {"ok": False, "failed_step": i, "results": results}
        self._bus.publish("schedule.fired", {"schedule": sid, "ok": True, "results": results})
        return {"ok": True, "results": results}
