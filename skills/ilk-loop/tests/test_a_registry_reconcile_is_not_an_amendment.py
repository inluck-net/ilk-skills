"""Pin that a registry reconcile is not a plan amendment.

Part of sub-plan ``a-registry-reconcile-is-not-an-amendment`` (step 0).

Six acceptance criteria.  AC-1, AC-3, AC-4, AC-5 are red-first pins
(``xfail(strict=True)``): they test behaviour that does not exist yet
(the ``plan_fingerprint`` module).  AC-2 is a control that must pass
today (using the module when present, skipped otherwise).

The mechanism under test:
- ``plan_fingerprint.surface(text, master=True)`` computes the plan surface
  for a MASTER, excluding the registry Status column and the Progress log.
- ``plan_fingerprint.fingerprint(path)`` returns the sha256 hex of that
  surface.
- After ``reconcile_master_registry`` rewrites a Status cell and
  ``reconcile_master_status`` flips frontmatter ``status``, the fingerprint
  is unchanged.
"""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"
_REPO_ROOT = _TESTS.parent.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Try to import the module under test; tests will xfail if it doesn't exist.
try:
    import plan_fingerprint
    _MODULE_EXISTS = True
except ImportError:
    _MODULE_EXISTS = False

import ilk_paths


def _setup_project(tmp_path: Path) -> dict[str, Path]:
    """Set up a git project with external plans dir, matching the runner's expectations."""
    project = tmp_path / "project"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(project), capture_output=True)
    (project / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(project), capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(project),
                   capture_output=True, env={**os.environ,
                                             "GIT_AUTHOR_NAME": "test",
                                             "GIT_AUTHOR_EMAIL": "t@t",
                                             "GIT_COMMITTER_NAME": "test",
                                             "GIT_COMMITTER_EMAIL": "t@t"})

    data_home = tmp_path / "ilk-data"
    old = os.environ.get("ILK_DATA_HOME")
    os.environ["ILK_DATA_HOME"] = str(data_home)
    try:
        key = ilk_paths.project_key(project)
    finally:
        if old is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = old

    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    return {"project": project, "plans": plans, "data_home": data_home,
            "key": key, "bin": bin_dir}


def _make_stub_claude(bin_dir: Path, *, sleep_sec: int = 60) -> Path:
    """Create a stub ``claude`` binary that sleeps for *sleep_sec* seconds."""
    stub = bin_dir / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"sleep {sleep_sec}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _run_runner(args: list[str], *, cwd: str, env: dict, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the runner script, capturing output via temp files instead of pipes."""
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".stdout", delete=False) as out_f, \
         tempfile.NamedTemporaryFile(mode="w+", suffix=".stderr", delete=False) as err_f:
        out_path, err_path = out_f.name, err_f.name
    try:
        with open(out_path, "w") as out_f, open(err_path, "w") as err_f:
            proc = subprocess.Popen(
                ["bash", *args],
                stdout=out_f, stderr=err_f,
                cwd=cwd, env=env,
            )
            proc.wait(timeout=timeout)
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            stdout = f.read()
        with open(err_path, "r", encoding="utf-8", errors="replace") as f:
            stderr = f.read()
        return subprocess.CompletedProcess(
            args=args, returncode=proc.returncode,
            stdout=stdout, stderr=stderr,
        )
    finally:
        os.unlink(out_path)
        os.unlink(err_path)


def _write_master(path: Path, *,
                  sub_plan_filename: str = "2026-10-02-test-slug.md",
                  goal: str = "Six deterministic failures stop needing a human.",
                  body_above_tables: str = "## Strategy\n\nDo the thing.\n",
                  extra_registry_rows: str = "",
                  progress_log_rows: str = "") -> None:
    """Write a minimal MASTER plan file with a registry table and progress log."""
    progress_section = ""
    if progress_log_rows:
        progress_section = (
            "\n## Progress log\n"
            "\n"
            "| When | Event |\n"
            "|---|---|\n"
            f"{progress_log_rows}\n"
        )
    text = (
        "---\n"
        "master_plan: 2026-10-02-execution\n"
        "batch_date: 2026-10-02\n"
        "status: active\n"
        "total_tickets: 0\n"
        "current_subplan: 2026-10-02-test-slug\n"
        "---\n"
        "\n"
        "# MASTER plan\n"
        "\n"
        f"## Goal\n\n{goal}\n\n"
        f"{body_above_tables}"
        "\n"
        "## Sub-plan registry\n"
        "\n"
        "| # | Slug | Items | Steps (est.) | Status |\n"
        "|---|---|---|---|---|\n"
        f"| 1 | {sub_plan_filename} | test item | 2 | pending |\n"
        f"{extra_registry_rows}"
        f"{progress_section}"
    )
    path.write_text(text, encoding="utf-8")


def _write_sub_plan(path: Path, *, current_step: int = 0,
                    status: str = "in-progress",
                    body_above_findings: str = "## Steps\n\n### Step 0\n\nDo thing.\n",
                    findings: str = "## Findings\n\n_(none)_\n") -> None:
    """Write a minimal sub-plan file."""
    text = (
        f"---\n"
        f"plan: test-slug\n"
        f"status: {status}\n"
        f"current_step: {current_step}\n"
        f"estimated_steps: 2\n"
        f"last_updated: 2026-10-02\n"
        f"---\n"
        f"\n"
        f"# Sub-plan: test\n"
        f"\n"
        f"{body_above_findings}"
        f"\n"
        f"{findings}"
    )
    path.write_text(text, encoding="utf-8")


# ── AC-1: registry reconcile + status flip + progress row does NOT change fingerprint ──

class TestAC1RegistryReconcileNotAmendment:
    """AC-1: after reconcile_master_registry rewrites a Status cell,
    reconcile_master_status flips frontmatter status, and a row is
    appended to the Progress log, the fingerprint is unchanged.

    Red-first until plan_fingerprint exists.
    """

    @pytest.mark.xfail(not _MODULE_EXISTS, strict=True, reason="plan_fingerprint module does not exist yet")
    def test_registry_reconcile_preserves_fingerprint(self, tmp_path: Path) -> None:
        """After registry reconcile + status flip + progress row, fingerprint unchanged."""
        plans = tmp_path / "plans"
        plans.mkdir()

        master = plans / "MASTER-2026-10-02-execution-plan.md"
        sub = plans / "2026-10-02-test-slug.md"
        _write_sub_plan(sub, status="in-progress")
        _write_master(master)

        fp_before = plan_fingerprint.fingerprint(master)

        # Simulate reconcile_master_registry: rewrite Status cell to shipped.
        text = master.read_text(encoding="utf-8")
        text = text.replace(
            "| 1 | 2026-10-02-test-slug.md | test item | 2 | pending |",
            "| 1 | 2026-10-02-test-slug.md | test item | 2 | shipped |",
        )
        master.write_text(text, encoding="utf-8")

        # Simulate reconcile_master_status: flip frontmatter status.
        text = master.read_text(encoding="utf-8")
        text = text.replace("status: active", "status: shipped", 1)
        master.write_text(text, encoding="utf-8")

        # Simulate progress log append.
        text = master.read_text(encoding="utf-8")
        if "## Progress log" not in text:
            text += "\n## Progress log\n\n| When | Event |\n|---|---|\n| 2026-10-02 | shipped |\n"
        else:
            text = text.replace(
                "|---|---|\n",
                "|---|---|\n| 2026-10-02 | shipped |\n",
            )
        master.write_text(text, encoding="utf-8")

        fp_after = plan_fingerprint.fingerprint(master)

        assert fp_before == fp_after, (
            "fingerprint must NOT change after registry reconcile, "
            "status flip, and progress log append"
        )


# ── AC-2: content changes DO change fingerprint (control) ────────────────────

class TestAC2ContentChangesFingerprint:
    """AC-2: a changed Goal, changed Slug cell, or added registry row
    changes the fingerprint.  Control — must pass today (module or not).
    """

    def _fingerprint(self, path: Path) -> str:
        """Compute fingerprint, using the module if available, else a local impl."""
        if _MODULE_EXISTS:
            return plan_fingerprint.fingerprint(path)
        # Fallback: use the same logic as the current _plan_fingerprint in the runner.
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        cleaned = []
        for line in lines:
            stripped = line.lstrip()
            if any(stripped.startswith(f"{k}:") for k in ("current_step", "status", "last_updated")):
                continue
            cleaned.append(line)
        text = "".join(cleaned)
        if "## Findings" in text:
            text = text.split("## Findings")[0]
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_goal_change_changes_fingerprint(self, tmp_path: Path) -> None:
        """Changing the Goal sentence changes the fingerprint."""
        plans = tmp_path / "plans"
        plans.mkdir()
        master = plans / "MASTER-2026-10-02-execution-plan.md"
        _write_master(master, goal="Original goal.")
        fp1 = self._fingerprint(master)

        _write_master(master, goal="Amended goal.")
        fp2 = self._fingerprint(master)

        assert fp1 != fp2, "fingerprint must change when Goal changes"

    def test_slug_change_changes_fingerprint(self, tmp_path: Path) -> None:
        """Changing a Slug cell changes the fingerprint."""
        plans = tmp_path / "plans"
        plans.mkdir()
        master = plans / "MASTER-2026-10-02-execution-plan.md"
        _write_master(master, sub_plan_filename="2026-10-02-test-slug.md")
        fp1 = self._fingerprint(master)

        _write_master(master, sub_plan_filename="2026-10-02-other-slug.md")
        fp2 = self._fingerprint(master)

        assert fp1 != fp2, "fingerprint must change when Slug cell changes"

    def test_added_registry_row_changes_fingerprint(self, tmp_path: Path) -> None:
        """Adding a registry row changes the fingerprint."""
        plans = tmp_path / "plans"
        plans.mkdir()
        master = plans / "MASTER-2026-10-02-execution-plan.md"
        _write_master(master)
        fp1 = self._fingerprint(master)

        _write_master(master, extra_registry_rows="| 2 | 2026-10-02-other.md | other | 3 | pending |\n")
        fp2 = self._fingerprint(master)

        assert fp1 != fp2, "fingerprint must change when a registry row is added"


# ── AC-3: inline `## Findings` text above real heading does not truncate surface ──

class TestAC3InlineFindingsNotTruncated:
    """AC-3: a sub-plan whose body contains the inline text ``## Findings``
    above its real ``## Findings`` heading: an edit between the inline
    mention and the heading changes the fingerprint.

    Red-first until plan_fingerprint exists.
    """

    @pytest.mark.xfail(not _MODULE_EXISTS, strict=True, reason="plan_fingerprint module does not exist yet")
    def test_inline_findings_does_not_truncate_surface(self, tmp_path: Path) -> None:
        """An edit between inline ``## Findings`` text and the real heading changes fingerprint."""
        body = (
            "## Steps\n\n"
            "### Step 0\n\n"
            "The docs mention `## Findings` as a convention.\n\n"
            "Original content here.\n\n"
            "## Findings\n\n"
            "_(none)_\n"
        )
        plan = tmp_path / "sub-plan.md"
        _write_sub_plan(plan, body_above_findings=body)
        fp1 = plan_fingerprint.fingerprint(plan)

        body2 = body.replace("Original content here.", "Amended content here.")
        _write_sub_plan(plan, body_above_findings=body2)
        fp2 = plan_fingerprint.fingerprint(plan)

        assert fp1 != fp2, (
            "fingerprint must change when content between inline `## Findings` "
            "text and the real heading is edited"
        )


# ── AC-4: frontmatter-like lines inside fenced blocks above Findings are kept ──

class TestAC4FencedFrontmatterKept:
    """AC-4: a sub-plan whose body has a line beginning ``status:`` inside
    a fenced block above Findings: editing that line changes the fingerprint.

    Red-first until plan_fingerprint exists (today's filter drops any line
    starting with those keys anywhere in the file).
    """

    @pytest.mark.xfail(not _MODULE_EXISTS, strict=True, reason="plan_fingerprint module does not exist yet")
    def test_fenced_status_line_changes_fingerprint(self, tmp_path: Path) -> None:
        """Editing a ``status:`` line inside a fenced block changes fingerprint."""
        body = (
            "## Steps\n\n"
            "### Step 0\n\n"
            "```yaml\n"
            "status: in-progress\n"
            "```\n\n"
            "## Findings\n\n"
            "_(none)_\n"
        )
        plan = tmp_path / "sub-plan.md"
        _write_sub_plan(plan, body_above_findings=body)
        fp1 = plan_fingerprint.fingerprint(plan)

        body2 = body.replace("status: in-progress", "status: shipped")
        _write_sub_plan(plan, body_above_findings=body2)
        fp2 = plan_fingerprint.fingerprint(plan)

        assert fp1 != fp2, (
            "fingerprint must change when a `status:` line inside a fenced "
            "block is edited"
        )


# ── AC-5 (runtime): registry reconcile during iteration does NOT kill agent ──

class TestAC5RuntimeReconcileNoKill:
    """AC-5: the real driver, run with a stub claude.  While the agent sleeps,
    the test calls reconcile_master_registry on the master.  No
    ``plan-amended-1.flag`` is written and the agent is not killed.

    Red-first until plan_fingerprint exists.
    """

    @pytest.mark.xfail(not _MODULE_EXISTS, strict=True, reason="plan_fingerprint module does not exist yet")
    def test_registry_reconcile_does_not_kill_agent(self, tmp_path: Path) -> None:
        """A mid-iteration registry reconcile does not trigger plan-amended."""
        world = _setup_project(tmp_path)
        plans_dir = world["plans"]
        sub = plans_dir / "2026-10-02-test-slug.md"
        master = plans_dir / "MASTER-2026-10-02-execution-plan.md"
        _write_sub_plan(sub)
        _write_master(master, sub_plan_filename="2026-10-02-test-slug.md")

        _make_stub_claude(world["bin"], sleep_sec=60)

        env = {
            **os.environ,
            "PATH": f"{world['bin']}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(world["data_home"]),
        }
        env.pop("ILK_DOTSOURCE_ONLY", None)
        env.pop("ILK_SKILL_HOME", None)  # let auto-detect find the worktree's scripts

        # Reconcile the registry in a background thread after a short delay.
        def reconcile() -> None:
            time.sleep(5)
            # Simulate reconcile_master_registry rewriting Status cell.
            text = master.read_text(encoding="utf-8")
            text = text.replace("| pending |", "| shipped |")
            master.write_text(text, encoding="utf-8")

        editor = threading.Thread(target=reconcile, daemon=True)
        editor.start()

        _RUNNER = _SCRIPTS / "run_ilk_loop_claude.sh"

        result = _run_runner(
            [str(_RUNNER),
             "--project-path", str(world["project"]),
             "--max-iterations", "1",
             "--iteration-timeout-min", "1",
             "--model", "test-model"],
            cwd=str(world["project"]), env=env, timeout=120,
        )

        combined = result.stdout + result.stderr

        # The agent must NOT be killed by plan-amended.
        assert "plan-amended" not in combined and "plan_amended" not in combined, (
            f"registry reconcile must NOT trigger plan-amended, got: {combined[-500:]}"
        )

        # No plan-amended flag file should exist.
        flag_files = list(world["project"].rglob("*plan-amended*"))
        assert not flag_files, f"plan-amended flag files found: {flag_files}"