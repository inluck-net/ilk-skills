"""Red-first pins: a worker edit into the kernel is refused.

Part of sub-plan ``an-unattended-build-cannot-edit-the-kernel``.

Guard 1 at edit time: the hook ``no-foreign-plan-edit.py`` denies an Edit/Write
of a rules-tier file, and denies an Edit/Write of a kernel-tier file when the
iteration's master has ``auto_planned: true``.  A target in a repo without
``safety-kernel.json`` is allowed; with ``ILK_ITERATION_SUBPLAN`` unset
everything is allowed (an interactive session is never blocked).

Five acceptance criteria:

  AC-1  with ILK_ITERATION_SUBPLAN set and a session master: an Edit of
        tests/invariants/test_x.py is denied naming the rules tier; an Edit of
        skills/ilk-loop/scripts/batch_gate.py is allowed.  Red-first.
  AC-2  the same with an auto_planned: true master: the batch_gate.py edit is
        denied naming tier-0; an edit of skills/ilk-feedback/scripts/collect.py
        is allowed.  Red-first.
  AC-3  a target in a repo without safety-kernel.json is allowed; with
        ILK_ITERATION_SUBPLAN unset everything is allowed.  Red-first for the
        first half; the second half is a control.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "no-foreign-plan-edit.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _toolkit_env(tmp_path: Path):
    """Create a fake toolkit repo with safety-kernel.json and a plans dir.

    Layout::

        tmp/
          toolkit/               ← a fake toolkit repo with .git
            .git/
            skills/ilk-loop/
              safety-kernel.json
              scripts/
                batch_gate.py
            tests/invariants/
              test_x.py
            skills/ilk-feedback/
              scripts/
                collect.py
          plans/                 ← a fake plans dir
            MASTER-2026-10-03k.md
    """
    toolkit = tmp_path / "toolkit"
    toolkit.mkdir()

    # Init git repo
    subprocess.run(["git", "init"], cwd=toolkit, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=toolkit,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=toolkit,
                   capture_output=True, check=True)

    # Copy safety-kernel.json from the real repo
    real_kernel = REPO_ROOT / "skills" / "ilk-loop" / "safety-kernel.json"
    kernel_dir = toolkit / "skills" / "ilk-loop"
    kernel_dir.mkdir(parents=True)
    (kernel_dir / "safety-kernel.json").write_text(
        real_kernel.read_text(encoding="utf-8"), encoding="utf-8"
    )

    # Create target files
    scripts_dir = kernel_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "batch_gate.py").write_text("# batch gate\n", encoding="utf-8")

    invariants_dir = toolkit / "tests" / "invariants"
    invariants_dir.mkdir(parents=True)
    (invariants_dir / "test_x.py").write_text("# test\n", encoding="utf-8")

    feedback_dir = toolkit / "skills" / "ilk-feedback" / "scripts"
    feedback_dir.mkdir(parents=True)
    (feedback_dir / "collect.py").write_text("# collect\n", encoding="utf-8")

    # Commit everything
    subprocess.run(["git", "add", "."], cwd=toolkit, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init toolkit"], cwd=toolkit,
                   capture_output=True, check=True)

    # Create plans dir
    plans = tmp_path / "plans"
    plans.mkdir()

    return {"toolkit": toolkit, "plans": plans, "tmp": tmp_path}


def _session_master(plans: Path) -> None:
    """Write a session master (no auto_planned)."""
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-10-03k
        status: active
        ---
        # session batch

        | # | Slug |
        |---|---|
        | 0 | 2026-10-03k-sub.md |
    """), encoding="utf-8")


def _auto_planned_master(plans: Path) -> None:
    """Write an auto_planned: true master."""
    (plans / "MASTER-2026-10-03k.md").write_text(textwrap.dedent("""\
        ---
        master_plan: 2026-10-03k
        status: active
        auto_planned: true
        ---
        # auto-planned batch

        | # | Slug |
        |---|---|
        | 0 | 2026-10-03k-sub.md |
    """), encoding="utf-8")


def _event(tool_name: str, tool_input: dict | None = None) -> str:
    """Build a JSON event string as Claude Code would."""
    return json.dumps({"tool_name": tool_name,
                       "tool_input": tool_input or {}})


def _run_hook(event: str, env: dict[str, str]) -> dict:
    """Run the hook as a subprocess and return {allowed, payload?, stdout}."""
    if not HOOK_PATH.exists():
        return {"allowed": True, "missing": True}
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=event,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, f"hook exited {result.returncode}: {result.stderr}"
    if not result.stdout.strip():
        return {"allowed": True}
    return {"allowed": False, "payload": json.loads(result.stdout)}


# ---------------------------------------------------------------------------
# AC-1: session master — rules denied, kernel allowed
# ---------------------------------------------------------------------------


class TestSessionMasterDeniesRules:
    """With ILK_ITERATION_SUBPLAN set and a session master:
    editing a rules-tier file is denied; editing a kernel-tier file is allowed."""

    def test_edit_rules_file_denied(self, _toolkit_env: dict) -> None:
        """AC-1: Edit tests/invariants/test_x.py (rules tier) ⇒ deny."""
        toolkit = _toolkit_env["toolkit"]
        plans = _toolkit_env["plans"]
        _session_master(plans)

        target = toolkit / "tests" / "invariants" / "test_x.py"
        event = _event("Edit", {
            "file_path": str(target),
            "old_string": "# test",
            "new_string": "# edited",
        })
        env = {
            **os.environ,
            "ILK_ITERATION_SUBPLAN": "an-unattended-build-cannot-edit-the-kernel",
            "ILK_PLANS_DIR": str(plans),
        }
        result = _run_hook(event, env)
        assert result["allowed"] is False
        # Deny message must name the rules tier
        reason = result.get("payload", {}).get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", "")
        assert "rules" in reason.lower(), f"deny reason must name rules tier: {reason}"

    def test_edit_kernel_file_allowed_under_session_master(self, _toolkit_env: dict) -> None:
        """AC-1 (control): Edit batch_gate.py (kernel tier) under session master ⇒ allow."""
        toolkit = _toolkit_env["toolkit"]
        plans = _toolkit_env["plans"]
        _session_master(plans)

        target = toolkit / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
        event = _event("Edit", {
            "file_path": str(target),
            "old_string": "# batch gate",
            "new_string": "# edited",
        })
        env = {
            **os.environ,
            "ILK_ITERATION_SUBPLAN": "an-unattended-build-cannot-edit-the-kernel",
            "ILK_PLANS_DIR": str(plans),
        }
        result = _run_hook(event, env)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# AC-2: auto_planned master — kernel denied, non-kernel allowed
# ---------------------------------------------------------------------------


class TestAutoPlannedMasterDeniesKernel:
    """With ILK_ITERATION_SUBPLAN set and an auto_planned master:
    editing a kernel-tier file is denied; editing a non-kernel file is allowed."""

    def test_edit_kernel_file_denied_under_auto_planned(self, _toolkit_env: dict) -> None:
        """AC-2: Edit batch_gate.py (kernel tier) under auto_planned master ⇒ deny."""
        toolkit = _toolkit_env["toolkit"]
        plans = _toolkit_env["plans"]
        _auto_planned_master(plans)

        target = toolkit / "skills" / "ilk-loop" / "scripts" / "batch_gate.py"
        event = _event("Edit", {
            "file_path": str(target),
            "old_string": "# batch gate",
            "new_string": "# edited",
        })
        env = {
            **os.environ,
            "ILK_ITERATION_SUBPLAN": "an-unattended-build-cannot-edit-the-kernel",
            "ILK_PLANS_DIR": str(plans),
            "ILK_MASTER": "MASTER-2026-10-03k.md",
        }
        result = _run_hook(event, env)
        assert result["allowed"] is False
        # Deny message must name tier-0 or kernel
        reason = result.get("payload", {}).get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", "")
        assert "tier-0" in reason.lower() or "kernel" in reason.lower(), \
            f"deny reason must name tier-0 or kernel: {reason}"

    def test_edit_non_kernel_file_allowed(self, _toolkit_env: dict) -> None:
        """AC-2 (control): Edit collect.py (not in kernel) under auto_planned master ⇒ allow."""
        toolkit = _toolkit_env["toolkit"]
        plans = _toolkit_env["plans"]
        _auto_planned_master(plans)

        target = toolkit / "skills" / "ilk-feedback" / "scripts" / "collect.py"
        event = _event("Edit", {
            "file_path": str(target),
            "old_string": "# collect",
            "new_string": "# edited",
        })
        env = {
            **os.environ,
            "ILK_ITERATION_SUBPLAN": "an-unattended-build-cannot-edit-the-kernel",
            "ILK_PLANS_DIR": str(plans),
        }
        result = _run_hook(event, env)
        assert result["allowed"] is True


# ---------------------------------------------------------------------------
# AC-3: no kernel.json → allowed; SUBPLAN unset → allowed
# ---------------------------------------------------------------------------


class TestNoKernelOrUnsetSubplan:
    """A target in a repo without safety-kernel.json is allowed;
    with ILK_ITERATION_SUBPLAN unset everything is allowed."""

    def test_repo_without_kernel_json_allowed(self, _toolkit_env: dict) -> None:
        """AC-3 (control): Edit in a repo without safety-kernel.json ⇒ allow."""
        tmp = _toolkit_env["tmp"]
        plans = _toolkit_env["plans"]
        _auto_planned_master(plans)

        # Create a product repo without safety-kernel.json
        product = tmp / "product"
        product.mkdir()
        subprocess.run(["git", "init"], cwd=product, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=product,
                       capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=product,
                       capture_output=True, check=True)
        test_dir = product / "tests" / "invariants"
        test_dir.mkdir(parents=True)
        (test_dir / "test_y.py").write_text("# test\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=product, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=product,
                       capture_output=True, check=True)

        target = test_dir / "test_y.py"
        event = _event("Edit", {
            "file_path": str(target),
            "old_string": "# test",
            "new_string": "# edited",
        })
        env = {
            **os.environ,
            "ILK_ITERATION_SUBPLAN": "an-unattended-build-cannot-edit-the-kernel",
            "ILK_PLANS_DIR": str(plans),
        }
        result = _run_hook(event, env)
        assert result["allowed"] is True

    def test_subplan_unset_allows_everything(self, _toolkit_env: dict) -> None:
        """AC-3 (control): Edit with ILK_ITERATION_SUBPLAN unset ⇒ allow."""
        toolkit = _toolkit_env["toolkit"]
        plans = _toolkit_env["plans"]
        _auto_planned_master(plans)

        # Even a rules-tier file should be allowed when SUBPLAN is unset
        target = toolkit / "tests" / "invariants" / "test_x.py"
        event = _event("Edit", {
            "file_path": str(target),
            "old_string": "# test",
            "new_string": "# edited",
        })
        env = {**os.environ, "ILK_PLANS_DIR": str(plans)}
        env.pop("ILK_ITERATION_SUBPLAN", None)
        result = _run_hook(event, env)
        assert result["allowed"] is True