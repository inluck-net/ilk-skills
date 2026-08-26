"""Red-first tests: the batch-gate record has ONE location, and the audit reads it.

Two defects found by /ilk-ship Phase 0 on 2026-08-25, which together made the
gate verdict unreadable no matter how correctly the gate computed it:

D5  The writer disagreed with itself about where the record lives.
    ``run_ilk_loop_claude.sh`` resolved ``runtime_dir`` from
    ``get_ilk_runtime_dir`` → ``ilk_paths.external_launcher_dir``
    (``<data>/runtime/launcher``), while ``batch_gate.main()``'s own default
    is ``external_runtime_dir`` (``<data>/runtime``).  The runner's gate wrote
    ``batch-gate.running`` and ``batch-gate-suite.output`` into
    ``runtime/launcher/``; the record ``ship_audit`` reads lives in
    ``runtime/``.  Measured: gh-resolve run 20260825-234253 left a marker in
    ``runtime/launcher/`` and 0 ``batch-gate.json`` anywhere.

D6  ``ship_audit``'s CLI had no ``--runtime-dir`` argument at all — only
    ``--subplan`` and ``--gate-passed`` — so ``_resolve_batch_record(None)``
    always took the "no runtime_dir supplied" branch and every sub-plan
    reported "gate declared but no gate result recorded", regardless of what
    the gate had written.  SP3 wired the library; the CLI never reached it.

The fix is one resolver, shared: ``batch_gate.resolve_runtime_dir``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHIP_AUDIT_CLI = SCRIPTS / "ship_audit.py"
RUNNER = SCRIPTS / "run_ilk_loop_claude.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=project, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _make_project(tmp: Path) -> Path:
    project = tmp / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True,
                   capture_output=True)
    _git(project, "commit", "-q", "--allow-empty", "-m", "init")
    (project / ".ilk-launch.json").write_text(
        json.dumps({"ship": {"suite": {
            "command": "python3 -m pytest",
            "flags": ["--timeout=60", "--timeout-method=signal"],
        }}}),
        encoding="utf-8",
    )
    return project


def _make_subplan(tmp: Path, slug: str) -> Path:
    """A shipped sub-plan whose one step has a commit, so only the gate is at issue."""
    sp = tmp / f"{slug}.md"
    # Frontmatter key is `plan:`, not `slug:` — see read_subplan_for_audit.
    sp.write_text(textwrap.dedent(f"""\
        ---
        plan: {slug}
        status: shipped
        current_step: 1
        estimated_steps: 1
        local_checks:
          - command: python3 -m pytest -q
            timeout: 120
        ---

        # {slug}

        ### Step 0 — do the thing

        Body.
        """), encoding="utf-8")
    return sp


def _write_record(runtime: Path, head: str, invocation: str,
                  verdict: str = "pass") -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "batch-gate.json").write_text(json.dumps({
        "verdict": verdict,
        "head_sha": head,
        "invocation": invocation,
        "timestamp": "2026-08-26T02:00:00+08:00",
    }, indent=2) + "\n", encoding="utf-8")


# ── D5: one resolver, and it is NOT the launcher dir ────────────────────────

class TestD5OneRecordLocation:

    def test_batch_gate_exposes_a_shared_resolver(self, tmp_path: Path) -> None:
        """Both writers and readers must be able to ask the same function."""
        import batch_gate
        assert hasattr(batch_gate, "resolve_runtime_dir"), (
            "batch_gate must expose resolve_runtime_dir() so the runner, the "
            "gate CLI and ship_audit cannot disagree about the record location"
        )

    def test_resolver_matches_ilk_paths_runtime_dir(self, tmp_path: Path) -> None:
        import batch_gate
        import ilk_paths

        project = _make_project(tmp_path)
        key = ilk_paths.resolve_project_key(project)
        assert key is not None

        resolved = batch_gate.resolve_runtime_dir(project)
        assert resolved == ilk_paths.external_runtime_dir(key)

    def test_resolver_is_not_the_launcher_dir(self, tmp_path: Path) -> None:
        """The launcher dir holds launcher state; the record is project state."""
        import batch_gate
        import ilk_paths

        project = _make_project(tmp_path)
        key = ilk_paths.resolve_project_key(project)
        resolved = batch_gate.resolve_runtime_dir(project)

        assert resolved != ilk_paths.external_launcher_dir(key)
        assert resolved.name == "runtime"

    def test_runner_does_not_send_the_gate_to_the_launcher_dir(
        self, tmp_path: Path,
    ) -> None:
        """invoke_batch_gate must not pin the record under runtime/launcher.

        Drives the real bash function with a stub gate that records its argv.
        """
        skill_root = tmp_path / "fake-skill-root"
        (skill_root / "ilk-loop" / "scripts").mkdir(parents=True)
        argv_dump = tmp_path / "argv.json"
        (skill_root / "ilk-loop" / "scripts" / "batch_gate.py").write_text(
            textwrap.dedent(f"""\
                import json, sys
                json.dump(sys.argv[1:], open({str(argv_dump)!r}, "w"))
                print("stub gate")
            """), encoding="utf-8")

        launcher_dir = tmp_path / "data" / "runtime" / "launcher"
        launcher_dir.mkdir(parents=True)

        script = (
            f"export ILK_DOTSOURCE_ONLY=1; source '{RUNNER}' 2>/dev/null; "
            f"_SKILL_ROOT='{skill_root}'; set +e; "
            f"invoke_batch_gate '{tmp_path}' '{launcher_dir}'"
        )
        subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30, env={"ILK_DOTSOURCE_ONLY": "1"})

        assert argv_dump.is_file(), "stub gate was never invoked"
        argv = json.loads(argv_dump.read_text(encoding="utf-8"))
        if "--runtime-dir" in argv:
            got = Path(argv[argv.index("--runtime-dir") + 1])
            assert got.name != "launcher", (
                f"runner pinned the gate record under {got} — ship_audit reads "
                "<data>/runtime/batch-gate.json, so the verdict is unreadable"
            )


# ── D6: the audit CLI can reach the record ──────────────────────────────────

class TestD6AuditCliReadsRecord:

    def _run_cli(self, subplan: Path, project: Path,
                 extra: list[str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SHIP_AUDIT_CLI), "--subplan", str(subplan),
             *(extra or [])],
            capture_output=True, text=True, timeout=60, cwd=project,
            encoding="utf-8",
        )

    def test_cli_accepts_runtime_dir(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(SHIP_AUDIT_CLI), "--help"],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        assert "--runtime-dir" in result.stdout, (
            "ship_audit's CLI has no --runtime-dir, so _resolve_batch_record "
            "always gets None and every sub-plan reads as ungated"
        )

    def test_fresh_pass_record_is_read_as_a_pass(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        sp = _make_subplan(tmp_path, "a-slug-with-a-step")
        _git(project, "commit", "-q", "--allow-empty", "-m",
             "feat: do the thing [plan:a-slug-with-a-step#step-0]")

        # Record must be written against the HEAD the gate would have run at —
        # i.e. after the step commit, or it is legitimately stale.
        runtime = tmp_path / "rt"
        _write_record(
            runtime, _git(project, "rev-parse", "HEAD"),
            "python3 -m pytest --timeout=60 --timeout-method=signal")

        result = self._run_cli(sp, project, ["--runtime-dir", str(runtime)])
        combined = result.stdout + result.stderr
        assert "no gate result recorded" not in combined, (
            f"CLI did not reach the record.  Output: {combined}"
        )
        assert "'final_gate': 'pass'" in combined, combined

    def test_stale_record_is_still_refused_through_the_cli(
        self, tmp_path: Path,
    ) -> None:
        project = _make_project(tmp_path)
        runtime = tmp_path / "rt"
        _write_record(
            runtime, "0" * 40,   # not this project's HEAD
            "python3 -m pytest --timeout=60 --timeout-method=signal")

        sp = _make_subplan(tmp_path, "another-slug")
        _git(project, "commit", "-q", "--allow-empty", "-m",
             "feat: thing [plan:another-slug#step-0]")

        result = self._run_cli(sp, project, ["--runtime-dir", str(runtime)])
        combined = result.stdout + result.stderr
        assert "'final_gate': 'pass'" not in combined, (
            f"a stale record must never read as a pass.  Output: {combined}"
        )
        assert result.returncode != 0

    def test_default_runtime_dir_is_resolved_not_none(self, tmp_path: Path) -> None:
        """With no flag, the CLI must resolve the dir rather than give up.

        Regression guard for the exact Phase 0 symptom: every sub-plan
        reporting "gate declared but no gate result recorded".
        """
        import batch_gate

        project = _make_project(tmp_path)
        resolved = batch_gate.resolve_runtime_dir(project)
        assert resolved is not None

        head = _git(project, "rev-parse", "HEAD")
        _write_record(
            resolved, head,
            "python3 -m pytest --timeout=60 --timeout-method=signal")
        try:
            sp = _make_subplan(tmp_path, "defaulted-slug")
            _git(project, "commit", "-q", "--allow-empty", "-m",
                 "feat: thing [plan:defaulted-slug#step-0]")

            result = self._run_cli(sp, project)   # no --runtime-dir
            combined = result.stdout + result.stderr
            assert "no gate result recorded" not in combined, (
                f"CLI fell back to None instead of resolving.  Output: {combined}"
            )
        finally:
            (resolved / "batch-gate.json").unlink(missing_ok=True)
