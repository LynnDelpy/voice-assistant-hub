"""LLM adapters. One interface, several backends (mock, OpenAI-compatible)."""

from .base import LLMResult, ToolSpec, build_adapter

__all__ = ["LLMResult", "ToolSpec", "build_adapter"]
