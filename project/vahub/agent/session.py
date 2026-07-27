"""In-memory conversation sessions.

Sessions live for the process lifetime and reset on restart. Durable storage
(SQLite) and the rolling-summary context strategy are later work; for M4 a short
in-memory history per session is enough, and voice sessions are short anyway.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    id: str
    messages: list[dict] = field(default_factory=list)


class SessionStore:
    def __init__(self, max_messages: int = 40) -> None:
        self._sessions: dict[str, Session] = {}
        self._max_messages = max_messages

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        sid = session_id or uuid.uuid4().hex
        session = Session(id=sid)
        self._sessions[sid] = session
        return session

    def trim(self, session: Session) -> None:
        """Keep history bounded: the system message plus the most recent turns."""
        if len(session.messages) <= self._max_messages:
            return
        head = session.messages[:1] if session.messages and session.messages[0].get("role") == "system" else []
        tail = session.messages[-(self._max_messages - len(head)):]
        session.messages = head + tail
