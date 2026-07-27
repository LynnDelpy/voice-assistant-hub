"""Agent (LLM tool-calling loop). M4.

Runs behind the module API (the future policy gate's choke point). Has no tool
to reconfigure the hub, so a prompt injection cannot become persistent.
"""

from .loop import AgentLoop
from .session import Session, SessionStore

__all__ = ["AgentLoop", "Session", "SessionStore"]
