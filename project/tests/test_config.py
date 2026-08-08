import os

import pytest
from pydantic import ValidationError

from vahub.core.config import load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Most of these tests exercise YAML loading, and env vars take priority
    over YAML. The sandbox passes the whole .env into the container, so without
    this the ambient VAHUB_* values would shadow what each test writes."""
    for key in list(os.environ):
        if key.startswith("VAHUB_"):
            monkeypatch.delenv(key, raising=False)


def test_loads_and_validates(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "log_level: DEBUG\n"
        "modules_dir: /tmp/mods\n"
        "state_dir: /tmp/state\n"
        "web:\n"
        "  port: 9000\n"
        "  dev_tools_endpoint: true\n"
    )
    cfg = load_config(cfg_file)
    assert cfg.log_level == "DEBUG"
    assert str(cfg.modules_dir) == "/tmp/mods"
    assert cfg.web.port == 9000
    assert cfg.web.dev_tools_endpoint is True


def test_env_alone_is_enough(monkeypatch):
    """The sandbox ships no config.yaml: everything comes from the single .env.
    Missing file plus env vars must produce a fully valid config."""
    monkeypatch.setenv("VAHUB_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("VAHUB_TIMEZONE", "Europe/Zurich")
    monkeypatch.setenv("VAHUB_WEB__PORT", "9111")
    monkeypatch.setenv("VAHUB_WEB__ORIGIN_ALLOWLIST", '["https://example.test"]')
    monkeypatch.setenv("VAHUB_LLM__MODEL", "some/model")
    monkeypatch.setenv("VAHUB_BUDGETS__ITERATIONS_PER_TURN", "3")

    cfg = load_config("/nonexistent/config.yaml")

    assert cfg.log_level == "WARNING"
    assert cfg.timezone == "Europe/Zurich"
    assert cfg.web.port == 9111
    assert cfg.web.origin_allowlist == ["https://example.test"]
    assert cfg.llm.model == "some/model"
    assert cfg.budgets.iterations_per_turn == 3


def test_env_overrides_yaml(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("log_level: DEBUG\nweb:\n  port: 9000\n")
    monkeypatch.setenv("VAHUB_WEB__PORT", "9222")
    cfg = load_config(cfg_file)
    assert cfg.web.port == 9222       # env wins
    assert cfg.log_level == "DEBUG"   # yaml still applies where env is silent


def test_unknown_key_aborts(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("nonsense_key: 1\n")
    with pytest.raises(ValidationError):
        load_config(cfg_file)


def test_nested_unknown_key_aborts(tmp_path):
    # A typo under web: must abort, not silently ship an empty origin_allowlist.
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("web:\n  dev_tools_endpont: true\n")
    with pytest.raises(ValidationError):
        load_config(cfg_file)


def test_load_config_path_has_no_global_side_effect(tmp_path):
    good = tmp_path / "a.yaml"
    good.write_text("log_level: WARNING\n")
    load_config(good)
    # A later call with no path must not reuse the previous path.
    os.environ.pop("VAHUB_CONFIG", None)
    cfg = load_config()  # default path does not exist -> defaults
    assert cfg.log_level == "INFO"
