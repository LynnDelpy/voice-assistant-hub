"""TTS adapters. Chunked delivery is the eventual shape (barge-in later); v1
returns a single audio blob, or None when the browser should speak locally."""

from .base import build_tts

__all__ = ["build_tts"]
