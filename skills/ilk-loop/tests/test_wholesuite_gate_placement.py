"""Tests for whole-suite gate placement lint (SP6, decomposition-principles.md §16).

4 tests covering:
  AC-5: ``lint_wholesuite_gate_outside_verification_subplan`` fires on non-verification
        sub-plans with whole-suite gates, not on verification sub-plans.
  AC-6: runner-prefix forms (``bun run <script>``, ``bunx vitest run``) are caught.
  AC-7: ``lint_batch_has_no_suite``'s ``has_broad`` short-circuit is gone.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
_PLAN_LINT = _SCRIPTS / "plan_lint.py"

sys.path.insert(0, str(_SCRIPTS))


# ── Fixtures ─────────────────────────────────────────────────────────────

BROAD_GATE_NO_MARKER = """\
---
plan: alpha
status: pending
current_step: 0
local_checks:
  - command: "python3 -m pytest"
    timeout: 300
---

# Alpha sub-plan — broad gate, no batch_verification marker.
"""

BROAD_GATE_WITH_MARKER = """\
---
plan: verify
batch_verification: true
status: pending
current_step: 0
local_checks:
  - command: "python3 -m pytest"
    timeout: 300
---

# Verification sub-plan — broad gate AND the marker.
"""

SCOPED_GATE_NO_MARKER = """\
---
plan: beta
status: pending
current_step: 0
local_checks:
  - command: "python3 -m pytest tests/test_foo.py"
    timeout: 60
---

# Beta sub-plan — scoped gate, no marker.
"""

RUNNER_PREFIX_BUN_RUN = """\
---
plan: convex-tests
status: pending
current_step: 0
local_checks:
  - command: "bun run test:non-ui:convex"
    timeout: 300
---

# Convex tests — script-form gate, no verification marker.
"""

RUNNER_PREFIX_BUNX = """\
---
plan: vitest-suite
status: pending
current_step: 0
local_checks:
  - command: "bunx vitest run"
    timeout: 300
---

# Vitest suite — bunx prefix, no verification marker.
"""

MASTER_BATCH_B = """\
---
title: Kira batch B
slug: 2026-08-28-kira-b
status: queued
base_branch: main
master_plan: 2026-08-28-master
---

# MASTER plan: kira batch B

## Sub-plan registry

| # | Order | Slug | Items | Steps (est.) | Status |
|---|---|---|---|---|---|
| 1 | 1 | [2026-08-28-issue-sync.md](./2026-08-28-issue-sync.md) | X | 3 | pending |
"""

DIRECTORY_GATE_SUBPLAN = """\
---
plan: issue-sync
status: pending
current_step: 0
local_checks:
  - command: "bunx vitest run -c tests/convex-tests/vitest.config.ts convex/__tests__/"
    timeout: 300
---

# Issue sync — directory gate.
"""


def _run_lint_subplan(tmp_path: Path, content: str, slug: str = "test-slug") -> subprocess.CompletedProcess:
    sp = tmp_path / f"2026-08-29-{slug}.md"
    sp.write_text(textwrap.dedent(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(sp)],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


def _run_lint_master(
    tmp_path: Path, master: str, subplans: dict[str, str],
    project_root: Path | None = None,
) -> subprocess.CompletedProcess:
    mp = tmp_path / "MASTER-2026-08-28-execution-plan.md"
    mp.write_text(textwrap.dedent(master), encoding="utf-8")
    paths = []
    for name, content in subplans.items():
        sp = tmp_path / name
        sp.write_text(textwrap.dedent(content), encoding="utf-8")
        paths.append(str(sp))
    cmd = [sys.executable, str(_PLAN_LINT), "--master", str(mp), *paths]
    if project_root:
        cmd.extend(["--project-root", str(project_root)])
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


# ── AC-5: both directions in one test ────────────────────────────────────

class TestAC5WholeSuiteGatePlacement:
    """Whole-suite gate outside verification sub-plan is reported."""

    def test_fires_on_non_verification_not_on_verification(self, tmp_path):
        """New lint fires for non-verification sub-plan, not for verification."""
        # Non-verification sub-plan with broad gate → must fire.
        r_no_marker = _run_lint_subplan(tmp_path, BROAD_GATE_NO_MARKER, "alpha")
        out_no = r_no_marker.stdout + r_no_marker.stderr
        assert "verification sub-plan" in out_no.lower() or "batch_verification" in out_no.lower(), (
            f"Expected lint about gate outside verification sub-plan; got:\n{out_no}"
        )
        # Verification sub-plan with broad gate → must NOT fire.
        r_with_marker = _run_lint_subplan(tmp_path, BROAD_GATE_WITH_MARKER, "verify")
        out_with = r_with_marker.stdout + r_with_marker.stderr
        assert "outside verification" not in out_with.lower(), (
            f"Expected no 'outside verification' finding for marked sub-plan; got:\n{out_with}"
        )


# ── AC-6: runner-prefix forms caught ─────────────────────────────────────

class TestAC6RunnerPrefixForms:
    """Runner-prefix whole-suite gates are caught in non-verification sub-plans."""

    def test_bun_run_and_bunx_forms(self, tmp_path):
        """Both 'bun run <script>' and 'bunx vitest run' are flagged."""
        for content, label in [
            (RUNNER_PREFIX_BUN_RUN, "bun run"),
            (RUNNER_PREFIX_BUNX, "bunx"),
        ]:
            r = _run_lint_subplan(tmp_path, content, label.replace(" ", "-"))
            out = r.stdout + r.stderr
            assert "verification sub-plan" in out.lower() or "batch_verification" in out.lower(), (
                f"Expected '{label}' gate to be flagged; got:\n{out}"
            )


# ── AC-7: has_broad short-circuit gone ───────────────────────────────────

class TestAC7HasBroadGone:
    """lint_batch_has_no_suite reports NotConfigured even when sub-plan gates exist."""

    def test_directory_gate_no_longer_short_circuits(self, tmp_path):
        """The has_broad short-circuit in lint_batch_has_no_suite is removed.

        Pre-existing lints may also fire on a broad gate; this test checks
        specifically that the batch-has-no-suite finding appears (the one
        from lint_batch_has_no_suite that mentions NotConfigured / the
        batch gate recording 'not_configured').
        """
        # Precondition: the short-circuit must be gone.
        lint_text = _PLAN_LINT.read_text(encoding="utf-8")
        # The old short-circuit: ``if has_broad: return findings``
        assert "if has_broad:" not in lint_text or "return findings" not in lint_text.split("if has_broad:")[1].split("\n")[0], (
            "lint_batch_has_no_suite still has the has_broad short-circuit"
        )
        project_root = tmp_path / "project"
        project_root.mkdir()
        r = _run_lint_master(
            tmp_path, MASTER_BATCH_B,
            {"2026-08-28-issue-sync.md": DIRECTORY_GATE_SUBPLAN},
            project_root=project_root,
        )
        out = r.stdout + r.stderr
        # The batch-has-no-suite finding specifically mentions "not_configured"
        # or "batch gate" — distinguish from other lints that mention .ilk-launch.json.
        assert "not_configured" in out.lower() or "batch gate" in out.lower(), (
            f"Expected batch-has-no-suite finding about NotConfigured; got:\n{out}"
        )
