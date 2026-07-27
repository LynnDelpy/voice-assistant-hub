"""Policy gate: default deny, argument constraints, principals, confirmation."""

from vahub.agent.policy import Gate

POLICY = {
    "default": "deny",
    "principals": {
        "agent": {"confirm": ["destructive"], "deny": []},
        "scheduler": {"confirm": [], "deny": ["*.lock_*", "*.unlock_*"]},
    },
    "rules": {
        "homeassistant.light_turn_on": {
            "class": "write",
            "constraints": {
                "entity_id": {"matches": r"^light\.(schlafzimmer|wohnzimmer)$"},
                "brightness_pct": {"range": [1, 100]},
            },
        },
        "homeassistant.lock_unlock": {
            "class": "destructive",
            "constraints": {"entity_id": {"matches": r"^lock\.haustuer$"}},
        },
    },
}


def gate():
    return Gate(POLICY)


def test_default_deny_for_unknown_tool():
    d = gate().evaluate("agent", "homeassistant", "format_disk", {})
    assert d.outcome == "deny"


def test_allowed_write_within_constraints():
    d = gate().evaluate("agent", "homeassistant", "light_turn_on",
                        {"entity_id": "light.schlafzimmer", "brightness_pct": 40})
    assert d.outcome == "allow"


def test_entity_outside_allowlist_is_denied():
    d = gate().evaluate("agent", "homeassistant", "light_turn_on", {"entity_id": "light.keller"})
    assert d.outcome == "deny"


def test_out_of_range_argument_is_denied():
    d = gate().evaluate("agent", "homeassistant", "light_turn_on",
                        {"entity_id": "light.schlafzimmer", "brightness_pct": 500})
    assert d.outcome == "deny"


def test_unknown_argument_is_denied():
    d = gate().evaluate("agent", "homeassistant", "light_turn_on",
                        {"entity_id": "light.schlafzimmer", "color": "red"})
    assert d.outcome == "deny" and "color" in d.reason


def test_destructive_requires_confirmation_for_agent():
    d = gate().evaluate("agent", "homeassistant", "lock_unlock", {"entity_id": "lock.haustuer"})
    assert d.outcome == "confirm" and d.klass == "destructive"


def test_scheduler_may_not_touch_locks():
    d = gate().evaluate("scheduler", "homeassistant", "lock_unlock", {"entity_id": "lock.haustuer"})
    assert d.outcome == "deny"


def test_visibility_filters_the_catalog():
    g = gate()
    assert g.visible_to("agent", "homeassistant", "light_turn_on") is True
    assert g.visible_to("agent", "homeassistant", "format_disk") is False
    assert g.visible_to("scheduler", "homeassistant", "lock_unlock") is False
