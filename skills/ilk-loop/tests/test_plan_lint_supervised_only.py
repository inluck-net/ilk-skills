#!/usr/bin/env python3
"""plan_lint supervised_only scope guard (decomposition-principles.md §13).

`supervised_only: true` is warranted by exactly one thing: a sub-plan whose
`scope_paths` modifies the loop's own dispatch machinery. It is NOT a readiness
gate, a risk gate, or a "needs human review" marker — and mis-setting it costs
both autonomous dispatch and (with a scheduler alive) the manual /ilk-run
fallback. These tests pin both directions of the check.

Field evidence for the guard: two shipped non-toolkit masters carried the flag
on risk-prose rationale alone (kira-cloudflare authz batch; robot-voice
API-contract batch), plus a near-miss on gh-resolve.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PLAN_LINT = _HERE.parent / "scripts" / "plan_lint.py"


def _run(tmp_path: Path, master: str, subplans: dict[str, str]):
    """Write a MASTER + sub-plans, run plan_lint with --master, return result."""
    mp = tmp_path / "MASTER-2026-07-26-execution-plan.md"
    mp.write_text(textwrap.dedent(master), encoding="utf-8")
    paths = []
    for name, content in subplans.items():
        sp = tmp_path / name
        sp.write_text(textwrap.dedent(content), encoding="utf-8")
        paths.append(str(sp))
    return subprocess.run(
        [sys.executable, str(_PLAN_LINT), "--master", str(mp), *paths],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


def _master(supervised: str | None) -> str:
    flag = "" if supervised is None else f"supervised_only: {supervised}\n"
    return (
        "---\n"
        "title: Test batch\n"
        "slug: 2026-07-26-test\n"
        "status: queued\n"
        f"{flag}"
        "master_plan: 2026-07-26-master\n"
        "---\n"
        "\n"
        "# MASTER\n"
    )


# Sub-plan bodies. The contract-change lint fires on loop-infra scope unless the
# body cites the contract docs, so infra fixtures include that reference — this
# keeps each test asserting only the supervised_only guard.
_SUBPLAN_APP = """\
---
plan: app-work
scope_paths:
  - "tools/resolver/cli.py"
  - "tests/test_cli.py"
---

# Sub-plan: app work

Ordinary product code, no loop infrastructure.
"""

_SUBPLAN_INFRA = """\
---
plan: infra-work
scope_paths:
  - "skills/ilk-loop/scripts/loop_status.py"
---

# Sub-plan: infra work

Rewrites the dispatcher.

## Reference reading

- `skills/ilk-loop/references/orchestration-collaboration.md`
"""

_SUBPLAN_INFRA_TEST_ONLY = """\
---
plan: infra-test-only
scope_paths:
  - "skills/ilk-loop/tests/test_loop_status.py"
---

# Sub-plan: test-only

Adds a test that imports loop_status.py but modifies no infra file.
"""


def _findings(result) -> str:
    return result.stdout + result.stderr


# --- AC-1: flag set, no infra in scope → hard finding ------------------------

def test_unwarranted_supervised_only_flagged(tmp_path):
    """The misuse this guard exists for: risk-prose rationale, no infra scope."""
    result = _run(tmp_path, _master("true"), {"app.md": _SUBPLAN_APP})
    assert result.returncode == 1, (
        "Expected non-zero exit for unwarranted supervised_only.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    out = _findings(result)
    assert "supervised_only" in out and "no sub-plan" in out, (
        f"Expected an unwarranted-flag finding.\nstdout={out}"
    )


def test_unwarranted_supervised_only_accepts_yes(tmp_path):
    """Truthiness matches scheduler_scan's ('true'/'yes'/'1')."""
    result = _run(tmp_path, _master("yes"), {"app.md": _SUBPLAN_APP})
    assert result.returncode == 1, f"'yes' must count as set.\n{_findings(result)}"


def test_trailing_comment_does_not_hide_the_flag(tmp_path):
    """Masters in the wild carry rationale comments after the value."""
    master = _master("true   # guard against autonomous dispatch of this batch")
    result = _run(tmp_path, master, {"app.md": _SUBPLAN_APP})
    assert result.returncode == 1, (
        f"Comment-suffixed value must still parse as set.\n{_findings(result)}"
    )


# --- AC-2: infra in scope, flag absent/false → hard finding ------------------

def test_infra_scope_without_flag_flagged(tmp_path):
    """A self-modifying batch must not be autonomously dispatchable."""
    result = _run(tmp_path, _master("false"), {"infra.md": _SUBPLAN_INFRA})
    assert result.returncode == 1, (
        f"Expected non-zero exit for infra scope with flag off.\n{_findings(result)}"
    )
    out = _findings(result)
    assert "loop-infra" in out and "not set" in out, (
        f"Expected a missing-flag finding.\nstdout={out}"
    )


def test_infra_scope_with_flag_absent_flagged(tmp_path):
    """Absent frontmatter key behaves as false."""
    result = _run(tmp_path, _master(None), {"infra.md": _SUBPLAN_INFRA})
    assert result.returncode == 1, (
        f"Absent flag must be treated as unset.\n{_findings(result)}"
    )


# --- AC-3: warranted combinations → clean -----------------------------------

def test_infra_scope_with_flag_set_clean(tmp_path):
    """The one legitimate use: infra scope + flag set."""
    result = _run(tmp_path, _master("true"), {"infra.md": _SUBPLAN_INFRA})
    assert result.returncode == 0, (
        f"Warranted supervised_only must lint clean.\n{_findings(result)}"
    )


def test_app_scope_without_flag_clean(tmp_path):
    """The autonomous default: product work, flag off."""
    result = _run(tmp_path, _master("false"), {"app.md": _SUBPLAN_APP})
    assert result.returncode == 0, (
        f"Ordinary batch must lint clean.\n{_findings(result)}"
    )


# --- AC-4: narrowness — importing infra is not modifying it -----------------

def test_test_only_infra_reference_does_not_demand_flag(tmp_path):
    """§13: a test that imports loop_status.py does not warrant the flag."""
    result = _run(tmp_path, _master("false"), {"t.md": _SUBPLAN_INFRA_TEST_ONLY})
    assert result.returncode == 0, (
        "A test file named after an infra module must not demand "
        f"supervised_only.\n{_findings(result)}"
    )


def test_glob_scope_covering_infra_justifies_flag(tmp_path):
    """A directory glob that pulls in infra makes the flag defensible (no
    unwarranted-flag finding), but is too loose to *demand* it."""
    subplan = """\
    ---
    plan: broad-glob
    scope_paths:
      - "skills/ilk-loop/scripts/**"
    ---

    # Sub-plan: broad

    Touches the scripts dir.

    ## Reference reading

    - `skills/ilk-loop/references/orchestration-collaboration.md`
    """
    set_result = _run(tmp_path, _master("true"), {"g.md": subplan})
    assert set_result.returncode == 0, (
        f"Glob covering infra must justify the flag.\n{_findings(set_result)}"
    )
    off_result = _run(tmp_path, _master("false"), {"g2.md": subplan})
    assert off_result.returncode == 0, (
        f"Glob alone must not demand the flag.\n{_findings(off_result)}"
    )


# --- AC-5: no --master → check is inert -------------------------------------

def test_check_requires_master_context(tmp_path):
    """Without --master there is no flag to evaluate; stay silent."""
    sp = tmp_path / "app.md"
    sp.write_text(textwrap.dedent(_SUBPLAN_APP), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_PLAN_LINT), str(sp)],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"Expected clean.\n{_findings(result)}"
    assert "supervised_only" not in result.stdout, (
        f"Guard must not fire without master context.\nstdout={result.stdout}"
    )
