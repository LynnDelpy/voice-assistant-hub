import pytest
from pydantic import ValidationError

from vahub.core.config import load_config


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
    import os

    os.environ.pop("VAHUB_CONFIG", None)
    cfg = load_config()  # falls back to the default path, which does not exist -> defaults
    assert cfg.log_level == "INFO"
