"""Tests: a project may waive the verify sub-plan (plan time).

Sub-plan: a-project-may-waive-the-verify-subplan (step 0).
Covers AC-1..AC-6: ship.verification_subplan validation, the
verification_subplan_mode fail-closed helper, and the two plan-time
callers that must honour it (plan_lint's AC-2 finding and
autoplan_rails.check_master).

Hermetic: tmp project roots and tmp data homes. Never reads the real
``~/.ilk-data``. Modules are loaded the way test_plan_lint.py loads
plan_lint (sys.path insert; never delete sys.modules entries).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Script dirs: ship_config (ilk-ship), plan_lint (ilk-loop), autoplan_rails
# (ilk-self-improve). autoplan_rails also needs ilk-watchdog on the path.
_HERE = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent / "scripts"
_SHIP_SCRIPTS = _HERE.parent.parent / "ilk-ship" / "scripts"
_SELF_SCRIPTS = _HERE.parent.parent / "ilk-self-improve" / "scripts"
_WATCHDOG_SCRIPTS = _HERE.parent.parent / "ilk-watchdog" / "scripts"

for _d in (_SHIP_SCRIPTS, _LOOP_SCRIPTS, _SELF_SCRIPTS, _WATCHDOG_SCRIPTS):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import autoplan_rails  # noqa: E402
import plan_lint  # noqa: E402
import ship_config  # noqa: E402
from ship_config import (  # noqa: E402
    MalformedConfig,
    NotConfigured,
    ShipConfig,
    load_ship_config,
)

REPO_ROOT = _HERE.parent.parent.parent  # skills/ilk-loop/tests → repo root

XFAIL = pytest.mark.xfail(
    strict=True,
    raises=(AssertionError, ImportError, AttributeError, TypeError, KeyError),
    reason="not built yet",
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_project(tmp_path: Path, ship: dict | None) -> Path:
    """Minimal project root. *ship* is the ``ship:`` value, or ``None``
    for no ``.ilk-launch.json`` at all."""
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    (project / ".git").mkdir(exist_ok=True)
    if ship is not None:
        _write_json(project / ".ilk-launch.json", {"ship": ship})
    return project


def _valid_suite() -> dict:
    return {"suite": {"command": "python3 -m pytest"}}


def _hermetic_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ILK_DATA_HOME / ILK_DATA_DIR / HOME at a tmp dir so
    ``_resolve_ext_plans_dir`` cannot see the real ~/.ilk-data."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(home / "ilk-data"))
    monkeypatch.setenv("ILK_DATA_DIR", str(home / "ilk-data"))
    return home


_WORK_SUBPLAN = """---
plan: 2026-10-10-fixture-work
status: pending
current_step: 0
batch_verification: false
---

# Sub-plan: fixture work
"""

_VERIFY_SUBPLAN = """---
plan: 2026-10-10-fixture-verify
status: pending
current_step: 0
batch_verification: true
---

# Sub-plan: fixture verify
"""


def _master(subplan_names: list[str]) -> str:
    rows = "\n".join(
        f"| {i} | [{n}](./{n}) | 2 | pending |"
        for i, n in enumerate(subplan_names)
    )
    return (
        "---\n"
        "master_plan: 2026-10-10-fixture-batch\n"
        "status: queued\n"
        "---\n\n"
        "# MASTER plan: fixture batch\n\n"
        "## Sub-plan registry\n\n"
        "| # | Slug | Steps (est.) | Status |\n"
        "|---|---|---|---|\n"
        f"{rows}\n"
    )


# ── AC-1: load_ship_config accepts and normalises the key ────────────────────


class TestAC1:
    """``ship.verification_subplan`` is validated and normalised."""

    @XFAIL
    def test_optional_sometimes_absent(self, tmp_path: Path) -> None:
        optional = _valid_suite() | {"verification_subplan": "optional"}
        result = load_ship_config(
            _make_project(tmp_path / "a", optional),
            ext_plans_dir=tmp_path / "a" / "empty_ext",
        )
        assert isinstance(result, ShipConfig), result
        assert result.ship["verification_subplan"] == "optional"

        sometimes = _valid_suite() | {"verification_subplan": "sometimes"}
        result = load_ship_config(
            _make_project(tmp_path / "b", sometimes),
            ext_plans_dir=tmp_path / "b" / "empty_ext",
        )
        assert isinstance(result, MalformedConfig), result
        assert "verification_subplan" in result.detail

        result = load_ship_config(
            _make_project(tmp_path / "c", _valid_suite()),
            ext_plans_dir=tmp_path / "c" / "empty_ext",
        )
        assert isinstance(result, ShipConfig), result
        assert result.ship["verification_subplan"] == "required"


# ── AC-2: verification_subplan_mode fails closed ─────────────────────────────


class TestAC2:
    """``verification_subplan_mode`` is ``optional`` only for a live
    ``optional`` ShipConfig; everything else is ``required``."""

    @XFAIL
    def test_optional_only_for_the_optional_shipconfig(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic_data_home(tmp_path, monkeypatch)

        optional = _valid_suite() | {"verification_subplan": "optional"}
        project = _make_project(tmp_path / "opt", optional)
        assert ship_config.verification_subplan_mode(project) == "optional"

        # no file at all
        absent = _make_project(tmp_path / "absent", None)
        assert ship_config.verification_subplan_mode(absent) == "required"

        # file present, no ship: block
        no_ship = tmp_path / "noship"
        no_ship.mkdir()
        (no_ship / ".git").mkdir()
        _write_json(no_ship / ".ilk-launch.json", {"max_iterations": 5})
        assert ship_config.verification_subplan_mode(no_ship) == "required"

        # malformed (bad enum value) → fail closed
        bad = _valid_suite() | {"verification_subplan": "sometimes"}
        malformed = _make_project(tmp_path / "bad", bad)
        assert ship_config.verification_subplan_mode(malformed) == "required"


# ── AC-3: the lint honours the key ───────────────────────────────────────────


class TestAC3:
    """A master with no verify sub-plan is a finding in a required
    project and clean in an optional one."""

    @XFAIL
    def test_required_finds_optional_silences(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic_data_home(tmp_path, monkeypatch)
        name = "2026-10-10-fixture-work.md"
        master_text = _master([name])
        subplans = [(name.removesuffix(".md"), _WORK_SUBPLAN)]

        required = _make_project(tmp_path / "req", _valid_suite())
        findings = plan_lint.lint_master_has_verification_subplan(
            master_text, subplans, project_root=required,
        )
        assert len(findings) == 1, findings
        assert "batch_verification" in findings[0]

        optional = _make_project(
            tmp_path / "opt", _valid_suite() | {"verification_subplan": "optional"},
        )
        findings = plan_lint.lint_master_has_verification_subplan(
            master_text, subplans, project_root=optional,
        )
        assert findings == [], findings


# ── AC-4: ordering still applies in an optional project ──────────────────────


class TestAC4:
    """A verify sub-plan that is not last is still a finding."""

    @XFAIL
    def test_verify_not_last_is_still_a_finding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic_data_home(tmp_path, monkeypatch)
        verify_name = "2026-10-10-fixture-verify.md"
        work_name = "2026-10-10-fixture-work.md"
        master_text = _master([verify_name, work_name])
        subplans = [
            (verify_name.removesuffix(".md"), _VERIFY_SUBPLAN),
            (work_name.removesuffix(".md"), _WORK_SUBPLAN),
        ]

        optional = _make_project(
            tmp_path / "opt", _valid_suite() | {"verification_subplan": "optional"},
        )
        findings = plan_lint.lint_master_has_verification_subplan(
            master_text, subplans, project_root=optional,
        )
        assert len(findings) == 1, findings
        assert "not last" in findings[0]


# ── AC-5: the autoplan rail honours the key ──────────────────────────────────


class TestAC5:
    """``check_master`` drops only the "no batch_verification" problem
    for an optional repo; ``repo=None`` keeps today's finding."""

    @XFAIL
    def test_repo_optional_silences_the_no_verify_problem(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _hermetic_data_home(tmp_path, monkeypatch)
        work_name = "2026-10-10-fixture-work.md"
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        master_path = plans_dir / "MASTER-2026-10-10-fixture-batch-execution-plan.md"
        master_path.write_text(_master([work_name]), encoding="utf-8")
        (plans_dir / work_name).write_text(_WORK_SUBPLAN, encoding="utf-8")

        optional = _make_project(
            tmp_path / "opt", _valid_suite() | {"verification_subplan": "optional"},
        )
        problems = autoplan_rails.check_master(
            master_path, plans_dir, repo=optional,
        )
        batch_verdict = [p for p in problems if "batch_verification" in p]
        assert batch_verdict == [], problems

        problems = autoplan_rails.check_master(master_path, plans_dir, repo=None)
        batch_verdict = [p for p in problems if "batch_verification" in p]
        assert len(batch_verdict) == 1, problems


# ── AC-6: the planner prose names the key ────────────────────────────────────


class TestAC6:
    """``commands/ilk-plan.md`` teaches the opt-out."""

    @XFAIL
    def test_ilk_plan_names_the_key(self) -> None:
        text = (REPO_ROOT / "commands" / "ilk-plan.md").read_text(encoding="utf-8")
        # The bare token `verification_subplan` already appears as a
        # substring of `lint_verification_subplan_hardcodes_suite`; pin
        # the key form that the opt-out sentence must introduce.
        assert "ship.verification_subplan" in text
