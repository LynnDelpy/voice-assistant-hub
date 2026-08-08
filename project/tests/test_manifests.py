"""Module manifests must line up with what the image actually builds.

The notify module once shipped broken because its manifest pointed at an
interpreter (/opt/vh/mod-notify/bin/python) that the Dockerfile never created:
the module failed to spawn, and nothing caught it until it was used. The image
now builds one venv per directory under project/modules/, named after the
directory, so these tests assert the two halves of that contract still agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[1]
MODULES_DIR = PROJECT / "modules"


def _config_dir() -> Path:
    """The structured config lives at <repo>/config, which the sandbox mounts
    into the container at /etc/vahub. Support both so the suite runs in the
    container and in a plain checkout."""
    for candidate in (Path("/etc/vahub"), PROJECT.parent / "config"):
        if (candidate / "modules.d").is_dir():
            return candidate
    raise AssertionError("could not locate the config directory")


CONFIG_DIR = _config_dir()
MANIFEST_DIR = CONFIG_DIR / "modules.d"

VENV_RE = re.compile(r"^/opt/vh/mod/([^/]+)/bin/python$")


def manifests() -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(MANIFEST_DIR.glob("*.yaml")):
        out.append((path, yaml.safe_load(path.read_text())))
    assert out, "no module manifests found"
    return out


def test_every_manifest_points_at_a_real_module_directory():
    for path, man in manifests():
        interpreter = man["command"][0]
        m = VENV_RE.match(interpreter)
        assert m, f"{path.name}: command must use /opt/vh/mod/<dir>/bin/python, got {interpreter!r}"
        module_dir = MODULES_DIR / m.group(1)
        assert module_dir.is_dir(), f"{path.name}: no module directory {module_dir}"
        assert (module_dir / "pyproject.toml").is_file(), f"{module_dir} has no pyproject.toml"


def test_every_module_directory_has_a_manifest():
    declared = set()
    for _, man in manifests():
        declared.add(VENV_RE.match(man["command"][0]).group(1))
    on_disk = {d.name for d in MODULES_DIR.iterdir() if d.is_dir() and (d / "pyproject.toml").is_file()}
    missing = on_disk - declared
    assert not missing, f"module directories with no manifest (they will never start): {sorted(missing)}"


def test_manifest_name_matches_its_filename():
    for path, man in manifests():
        assert man["name"] == path.stem, f"{path.name}: name is {man['name']!r}"


def test_declared_tools_are_allowed_by_the_policy():
    """A tool a module advertises but the gate has no rule for is dead weight:
    default-deny means the agent can never call it. Catches a module added
    without its policy entry."""
    policy = yaml.safe_load((CONFIG_DIR / "policy.yaml").read_text())
    rules = policy.get("rules", {})
    for path, man in manifests():
        for tool in (man.get("tools") or {}):
            key = f"{man['name']}.{tool}"
            assert key in rules, f"{path.name}: '{key}' has no rule in policy.yaml (default deny)"
