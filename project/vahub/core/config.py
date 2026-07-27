"""Configuration loading and validation.

The plan is explicit: on a config error we abort, we do not keep running with
defaults. That is why everything goes through a strict pydantic model
(`extra="forbid"`) and validation happens once at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class _Strict(BaseModel):
    """Nested config sections reject unknown keys too, so a typo under web:/
    budgets:/llm: aborts startup instead of being silently dropped (which for
    origin_allowlist would ship an empty, fail-open list)."""

    model_config = ConfigDict(extra="forbid")


class WebConfig(_Strict):
    host: str = "0.0.0.0"
    port: int = 8080
    # Dev-only escape hatch: a REST endpoint that calls a tool directly, without
    # the agent and (later) without the policy gate. Off in prod. See web/api.py.
    dev_tools_endpoint: bool = False
    # WebSocket handshakes are checked against this list. Same-origin policy does
    # not apply to WebSockets, so without this any web page could open a socket.
    origin_allowlist: list[str] = Field(default_factory=list)


class BudgetConfig(_Strict):
    """Per-turn and per-day limits. Enforced by the agent loop (M4)."""

    iterations_per_turn: int = 8
    tool_result_bytes: int = 8192
    tokens_per_turn: int = 20_000
    tokens_per_day: int | None = None
    wall_clock_text_s: float = 30.0
    wall_clock_voice_s: float = 8.0


class LLMConfig(_Strict):
    """Which brain the agent talks to.

    provider="mock" is a keyword stub that needs no API key: it exercises the
    full agent loop so the path is testable without credentials. Switch to
    "openai_compat" and set base_url/api_key/model for a real model (any
    OpenAI-compatible endpoint: OpenAI, OpenRouter, Groq, Ollama, llama.cpp,
    Anthropic's compat endpoint).

    The api_key is intentionally not put in the YAML. Set it via the environment
    variable VAHUB_LLM__API_KEY (in prod it would come from a systemd credential).
    """

    provider: str = "mock"  # "mock" | "openai_compat"
    base_url: str = "http://localhost:11434/v1"
    api_key: str | None = None
    model: str = "mock"
    temperature: float = 0.2
    max_tokens: int = 1024
    request_timeout_s: float = 60.0
    system_prompt: str | None = None


class STTConfig(_Strict):
    """Speech to text. mock = none (browser Web Speech API handles the demo).
    openai_compat = a Whisper-compatible /audio/transcriptions endpoint."""

    provider: str = "mock"  # "mock" | "openai_compat"
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    model: str = "whisper-1"
    request_timeout_s: float = 60.0


class TTSConfig(_Strict):
    """Text to speech. mock = none (browser speaks the reply locally).
    openai_compat = an /audio/speech endpoint."""

    provider: str = "mock"  # "mock" | "openai_compat"
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    model: str = "tts-1"
    voice: str = "alloy"
    request_timeout_s: float = 60.0


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VAHUB_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    log_level: str = "INFO"
    modules_dir: Path = Path("/etc/vahub/modules.d")
    state_dir: Path = Path("/var/lib/vahub")
    policy_file: Path = Path("/etc/vahub/policy.yaml")
    schedules_file: Path = Path("/etc/vahub/schedules.yaml")
    db_path: Path = Path("/var/lib/vahub/hub.db")
    timezone: str = "Europe/Berlin"
    confirm_ttl_s: float = 60.0
    web: WebConfig = Field(default_factory=WebConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Resolve the path at call time (Config() ), not at import, so env
        # overrides and tests work. An explicit load_config(path) wins for the
        # duration of that call only. YAML is the source; env (VAHUB_*) overrides.
        yaml_path = _YAML_PATH_OVERRIDE or os.environ.get("VAHUB_CONFIG", "/etc/vahub/config.yaml")
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
        return (init_settings, env_settings, yaml_source, file_secret_settings)


_YAML_PATH_OVERRIDE: str | None = None


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate. Raises pydantic.ValidationError on a bad config.

    A `path` applies only to this call and leaves no global state behind (no
    os.environ mutation), so a later `load_config()` still uses the default."""
    global _YAML_PATH_OVERRIDE
    previous = _YAML_PATH_OVERRIDE
    if path is not None:
        _YAML_PATH_OVERRIDE = str(path)
    try:
        return Config()
    finally:
        _YAML_PATH_OVERRIDE = previous
