"""Keep `quality_scale.yaml` complete and well formed.

hassfest does not check this file for a custom integration --
`script/hassfest/quality_scale.py::validate_iqs_file` returns immediately
when `not integration.core` -- so nothing outside this test would notice a
rule silently disappearing, a `todo` with no reason, or a status that is
not one of the three the schema allows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

INTEGRATION_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "cast_notifier"
)

# The rule list hassfest enforces, from `ALL_RULES` in
# `script/hassfest/quality_scale.py` (Home Assistant 2026.9.1). Pinned
# here rather than imported: the core checkout is a developer's tool, not
# a test dependency. A newer Home Assistant adding a rule shows up as this
# test passing while the real scale has moved on, which is the same
# blind spot every custom integration has.
BRONZE = (
    "action-setup",
    "appropriate-polling",
    "brands",
    "common-modules",
    "config-flow",
    "config-flow-test-coverage",
    "dependency-transparency",
    "docs-actions",
    "docs-conditions",
    "docs-high-level-description",
    "docs-installation-instructions",
    "docs-removal-instructions",
    "docs-triggers",
    "entity-event-setup",
    "entity-unique-id",
    "has-entity-name",
    "runtime-data",
    "test-before-configure",
    "test-before-setup",
    "unique-config-entry",
)
SILVER = (
    "action-exceptions",
    "config-entry-unloading",
    "docs-configuration-parameters",
    "docs-installation-parameters",
    "entity-unavailable",
    "integration-owner",
    "log-when-unavailable",
    "parallel-updates",
    "reauthentication-flow",
    "test-coverage",
)
GOLD = (
    "devices",
    "diagnostics",
    "discovery",
    "discovery-update-info",
    "docs-data-update",
    "docs-examples",
    "docs-known-limitations",
    "docs-supported-devices",
    "docs-supported-functions",
    "docs-troubleshooting",
    "docs-use-cases",
    "dynamic-devices",
    "entity-category",
    "entity-device-class",
    "entity-disabled-by-default",
    "entity-translations",
    "exception-translations",
    "icon-translations",
    "reconfiguration-flow",
    "repair-issues",
    "stale-devices",
)
PLATINUM = (
    "async-dependency",
    "inject-websession",
    "strict-typing",
)
ALL_RULES = BRONZE + SILVER + GOLD + PLATINUM


def _rules() -> dict[str, Any]:
    path = INTEGRATION_DIR / "quality_scale.yaml"
    loaded: dict[str, Any] = yaml.safe_load(path.read_text())
    rules: dict[str, Any] = loaded["rules"]
    return rules


def test_every_rule_of_every_tier_is_assessed() -> None:
    """No rule is missing, and none is invented either."""
    assert set(_rules()) == set(ALL_RULES)


@pytest.mark.parametrize("rule", ALL_RULES)
def test_each_rule_has_a_valid_status(rule: str) -> None:
    """`done`, or `todo`/`exempt` with a reason worth reading.

    The shorthand `<rule>: done` is what hassfest's schema allows without
    a comment; anything less than done has to say why.
    """
    value = _rules()[rule]
    if isinstance(value, str):
        assert value == "done", f"{rule}: only `done` may be written as a bare status"
        return

    assert set(value) == {"status", "comment"}, f"{rule}: unexpected keys"
    assert value["status"] in ("done", "todo", "exempt")
    assert len(value["comment"].strip()) > 20, f"{rule}: the comment says nothing"


def test_bronze_is_fully_met() -> None:
    """Bronze is the tier this integration claims; nothing there is todo."""
    rules = _rules()
    unmet = [
        rule
        for rule in BRONZE
        if isinstance(rules[rule], dict) and rules[rule]["status"] == "todo"
    ]
    assert not unmet
