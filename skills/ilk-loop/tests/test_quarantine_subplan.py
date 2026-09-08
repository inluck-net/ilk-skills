"""Sub-plan ``quarantine-is-reachable`` step 0 — establish the cause.

The open question this file settles, with evidence rather than assumption:
is ``quarantine_subplan`` **invoked** on a first gate failure and simply
returning ``blocked: false`` below its threshold of 2, or is it **never
invoked** behind the driver's ``q_plans_dir`` / ``--slugs`` guard?

The sub-plan's own text is explicit that this must not be assumed, because
on the observed consumer-host runs the sub-plans carried no
``auto_block_fails`` key at all — which is consistent with BOTH stories:
a counter that never reached 1, and a script that never ran.

ANSWER, measured here: **never invoked.**  ``ilk_paths.py`` has no
``--plans-dir`` flag.  The driver calls

    q_plans_dir=$(python3 ilk_paths.py --start "$PROJECT_PATH" --plans-dir 2>/dev/null)

argparse rejects the unknown argument, exits 2, and writes its usage to
stderr — which ``2>/dev/null`` discards.  So ``q_plans_dir`` is the empty
string, the guard ``[[ -n "$q_plans_dir" && -d "$q_plans_dir" ]]`` is false,
and the whole quarantine block is skipped at EVERY failure count.  The
threshold and the ``local-checks-stuck`` no-restart interaction are real, but
they are downstream of a call that never happens.

These tests invoke the CLI as a subprocess with real ``--slug`` /
``--failing-check`` arguments, per the sub-plan: whether the driver's
invocation reaches it at all is not a question importing the module can
answer.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_QUARANTINE = _SCRIPTS / "quarantine_subplan.py"
_ILK_PATHS = _SCRIPTS / "ilk_paths.py"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"


def _write_subplan(plans: Path, slug: str, status: str = "in-progress") -> Path:
    plans.mkdir(parents=True, exist_ok=True)
    p = plans / f"2026-09-08-{slug}.md"
    p.write_text(textwrap.dedent(f"""\
        ---
        plan: {slug}
        status: {status}
        current_step: 1
        estimated_steps: 2
        local_checks:
          - command: pytest -q
            timeout: 60
        ---

        # Sub-plan: {slug}
    """), encoding="utf-8")
    return p


def _run_quarantine(plans: Path, slug: str, check: str = "pytest -q",
                    threshold: int | None = None) -> dict:
    args = [sys.executable, str(_QUARANTINE), "--plans-dir", str(plans),
            "--slug", slug, "--failing-check", check]
    if threshold is not None:
        args += ["--threshold", str(threshold)]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"CLI exited {r.returncode}: {r.stderr}"
    return json.loads(r.stdout)


# ── the CLI itself works as documented (so the defect is not in here) ───────

def test_first_failure_bumps_the_counter_but_does_not_block(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    _write_subplan(plans, "alpha")
    out = _run_quarantine(plans, "alpha")
    assert out["blocked"] is False, out
    assert out["fails"] == 1, out
    assert out["threshold"] == 2, out


def test_second_failure_reaches_the_threshold_and_blocks(tmp_path: Path) -> None:
    """The threshold is reachable when the CLI is actually called twice."""
    plans = tmp_path / "plans"
    sub = _write_subplan(plans, "alpha")
    _run_quarantine(plans, "alpha")
    out = _run_quarantine(plans, "alpha")
    assert out["blocked"] is True, out
    assert out["fails"] == 2, out
    assert "status: blocked" in sub.read_text(encoding="utf-8")


def test_an_unknown_slug_is_reported_not_silently_ignored(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    _write_subplan(plans, "alpha")
    out = _run_quarantine(plans, "nonexistent")
    assert out["blocked"] is False
    assert "error" in out, out


# ── THE CAUSE: the driver's plans-dir resolution ───────────────────────────

def test_ilk_paths_rejects_the_flag_the_driver_passes() -> None:
    """Documents the mechanism. ``--plans-dir`` is not a flag ilk_paths has."""
    r = subprocess.run(
        [sys.executable, str(_ILK_PATHS), "--start", str(Path.cwd()), "--plans-dir"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode == 0 and r.stdout.strip():
        pytest.skip("ilk_paths now supports --plans-dir; the driver call is fine")
    assert r.returncode != 0
    assert r.stdout.strip() == "", (
        "argparse wrote something to stdout on rejection; the mechanism differs"
    )


def test_the_driver_resolves_a_real_plans_dir_for_quarantine(tmp_path: Path) -> None:
    """THE pin: the driver's own quarantine plans-dir resolution must yield a
    real directory, or the quarantine block is unreachable at every failure
    count.

    Executes the driver's actual resolution line rather than re-implementing
    it, so this cannot pass while the driver does something else.
    """
    src = _DRIVER.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    resolution = [
        l.strip() for l in lines
        if "q_plans_dir=" in l and "$(" in l
    ]
    assert resolution, "could not find the q_plans_dir resolution in the driver"
    line = resolution[0]

    # Build a project whose plans live at the external, resolver-known path.
    project = tmp_path / "proj"
    plans = project / "docs" / "plans"
    plans.mkdir(parents=True)
    _write_subplan(plans, "alpha")
    # A MASTER-*.md is required: get_plans_dir's legacy walk-up only accepts a
    # docs/plans that contains one, and a temp project has no external
    # ~/.ilk-data plans dir for the resolver to find.
    (plans / "MASTER-2026-09-08-quarantine-probe.md").write_text(
        "---\nmaster_plan: 2026-09-08-quarantine-probe\nstatus: active\n---\n"
        "# quarantine probe\n", encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)

    # Source the driver so the resolution runs in its real context (it may
    # legitimately be rewritten to use the driver's own get_plans_dir helper).
    script = textwrap.dedent(f"""\
        export ILK_DOTSOURCE_ONLY=1
        source '{_DRIVER}' || exit 90
        unset ILK_DOTSOURCE_ONLY
        set +eE +o pipefail
        _SKILL_ROOT='{_SCRIPTS.parent}'
        PROJECT_PATH='{project}'
        {line}
        echo "RESOLVED=[$q_plans_dir]"
    """)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         timeout=120, encoding="utf-8", errors="replace")
    resolved = ""
    for l in out.stdout.splitlines():
        if l.startswith("RESOLVED="):
            resolved = l[len("RESOLVED=["):].rstrip("]")
    assert resolved, (
        "the driver's quarantine plans-dir resolution produced an EMPTY string, "
        "so `[[ -n \"$q_plans_dir\" && -d \"$q_plans_dir\" ]]` is false and "
        "quarantine_subplan.py is NEVER invoked -- at any failure count. "
        f"The line executed was: {line!r}"
    )
    assert Path(resolved).is_dir(), (
        f"resolution produced {resolved!r}, which is not a directory; the "
        f"quarantine guard rejects it."
    )
