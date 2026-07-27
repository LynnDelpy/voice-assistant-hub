"""STT adapters. One interface, swappable backends (mock, OpenAI/Whisper-compatible)."""

from .base import build_stt

__all__ = ["build_stt"]
