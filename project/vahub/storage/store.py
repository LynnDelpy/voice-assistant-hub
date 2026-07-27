"""SQLite persistence (WAL, one writer).

Holds everything the plan lists: conversations, messages, the tool-call audit
log, pending confirmations, module state, schedules, and daily budget usage.
The audit log is the important one: when the light comes on at night, this table
says whether the agent or the scheduler did it, with which arguments.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    last_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT,
    created_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    principal   TEXT NOT NULL,
    module      TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args        TEXT,           -- redacted JSON
    decision    TEXT NOT NULL,  -- allow | deny | confirm
    result      TEXT,           -- ok | error | <error kind>
    duration_ms REAL
);
CREATE TABLE IF NOT EXISTS pending_calls (
    id          TEXT PRIMARY KEY,   -- uuid
    ts          REAL NOT NULL,
    expires_at  REAL NOT NULL,
    principal   TEXT NOT NULL,
    module      TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args        TEXT NOT NULL,      -- frozen JSON
    status      TEXT NOT NULL       -- pending | confirmed | expired | cancelled
);
CREATE TABLE IF NOT EXISTS module_state (
    module      TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    last_error  TEXT,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_usage (
    day         TEXT PRIMARY KEY,   -- YYYY-MM-DD
    tokens      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("store not open")
        return self._db

    # --- audit -------------------------------------------------------------
    async def record_tool_call(
        self,
        principal: str,
        module: str,
        tool: str,
        args: dict,
        decision: str,
        result: str | None,
        duration_ms: float | None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO tool_calls(ts, principal, module, tool, args, decision, result, duration_ms)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (time.time(), principal, module, tool, json.dumps(args), decision, result, duration_ms),
        )
        await self.db.commit()

    async def recent_tool_calls(self, limit: int = 100) -> list[dict]:
        cur = await self.db.execute(
            "SELECT ts, principal, module, tool, args, decision, result, duration_ms"
            " FROM tool_calls ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # --- pending confirmations --------------------------------------------
    async def create_pending(
        self, pid: str, principal: str, module: str, tool: str, args: dict, ttl_s: float
    ) -> None:
        now = time.time()
        await self.db.execute(
            "INSERT INTO pending_calls(id, ts, expires_at, principal, module, tool, args, status)"
            " VALUES(?,?,?,?,?,?,?, 'pending')",
            (pid, now, now + ttl_s, principal, module, tool, json.dumps(args)),
        )
        await self.db.commit()

    async def get_pending(self, pid: str) -> dict | None:
        cur = await self.db.execute("SELECT * FROM pending_calls WHERE id=?", (pid,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_pending_status(self, pid: str, status: str) -> None:
        await self.db.execute("UPDATE pending_calls SET status=? WHERE id=?", (status, pid))
        await self.db.commit()

    async def list_pending(self) -> list[dict]:
        cur = await self.db.execute(
            "SELECT id, ts, expires_at, principal, module, tool, status FROM pending_calls"
            " WHERE status='pending' ORDER BY ts DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    # --- conversations -----------------------------------------------------
    async def upsert_conversation(self, cid: str) -> None:
        now = time.time()
        await self.db.execute(
            "INSERT INTO conversations(id, created_at, last_at) VALUES(?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET last_at=excluded.last_at",
            (cid, now, now),
        )
        await self.db.commit()

    async def add_message(self, cid: str, role: str, content: str | None) -> None:
        await self.db.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) VALUES(?,?,?,?)",
            (cid, role, content, time.time()),
        )
        await self.db.commit()

    # --- module state ------------------------------------------------------
    async def save_module_state(self, module: str, state: str, last_error: str | None) -> None:
        await self.db.execute(
            "INSERT INTO module_state(module, state, last_error, updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(module) DO UPDATE SET state=excluded.state,"
            " last_error=excluded.last_error, updated_at=excluded.updated_at",
            (module, state, last_error, time.time()),
        )
        await self.db.commit()

    # --- budget ------------------------------------------------------------
    async def add_tokens(self, day: str, tokens: int) -> int:
        await self.db.execute(
            "INSERT INTO budget_usage(day, tokens) VALUES(?,?)"
            " ON CONFLICT(day) DO UPDATE SET tokens = tokens + excluded.tokens",
            (day, tokens),
        )
        await self.db.commit()
        cur = await self.db.execute("SELECT tokens FROM budget_usage WHERE day=?", (day,))
        row = await cur.fetchone()
        return int(row["tokens"]) if row else 0

    async def tokens_today(self, day: str) -> int:
        cur = await self.db.execute("SELECT tokens FROM budget_usage WHERE day=?", (day,))
        row = await cur.fetchone()
        return int(row["tokens"]) if row else 0
