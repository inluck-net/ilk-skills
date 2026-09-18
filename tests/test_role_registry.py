"""Schema gate for the committed role registry.

Validates tools/claude-worker/role-registry.json against the contract in
docs/role-tier-registry-design.md §3: tier enum, no home under conflicting
tiers, path_command roles naming real scripts. Runs on every platform with
no environment dependency — the committed source is the subject.
"""

import json
from pathlib import Path

REGISTRY = (
    Path(__file__).resolve().parent.parent
    / "tools" / "claude-worker" / "role-registry.json"
)
VALID_TIERS = {"worker", "planner", "manager"}


def _load():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert data["version"] == 1
    return data


def test_registry_parses_with_manager_row():
    roles = _load()["roles"]
    manager = roles["manager"]
    assert manager["home"] == "~/.claude-manager"
    assert manager["tier"] == "manager"
    assert manager.get("path_command") == "claude-manager"


def test_tiers_all_in_enum():
    roles = _load()["roles"]
    for role, row in roles.items():
        assert row["tier"] in VALID_TIERS, f"{role}: {row['tier']!r}"


def test_no_home_under_conflicting_tiers():
    roles = _load()["roles"]
    seen = {}
    for role, row in roles.items():
        home = row["home"]
        if home in seen:
            assert seen[home] == row["tier"], (
                f"home {home} carries both {seen[home]} and {row['tier']}"
            )
        seen[home] = row["tier"]


def test_path_commands_name_existing_scripts():
    tools_dir = REGISTRY.parent
    for role, row in _load()["roles"].items():
        cmd = row.get("path_command")
        if not cmd:
            continue
        script = tools_dir / f"{cmd}.sh"
        assert script.is_file(), f"{role}: path_command {cmd!r} has no {script}"


def test_committed_source_matches_design_appendix():
    """The design doc's appendix is the verbatim contract — drift is red."""
    import re

    design = (
        Path(__file__).resolve().parent.parent
        / "docs" / "role-tier-registry-design.md"
    ).read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(\{.*?\})\n```", design, re.S)
    assert blocks, "design doc lost its fenced json appendix"
    appendix = json.loads(blocks[-1])
    assert appendix == _load(), (
        "committed registry differs from the design appendix"
    )
