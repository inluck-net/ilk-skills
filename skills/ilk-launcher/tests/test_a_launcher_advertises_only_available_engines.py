"""A launcher advertises only packaged engines.

Handoff section 15: the launcher accepted/advertised ``codex`` while
``skills/ilk-loop/scripts/run_ilk_loop_codex.sh`` was absent.  A name in the
advertised set whose runner is missing is not an engine — it is an error the
user meets at the worst possible moment.

AC-1: launcher validation/help advertise only implemented packaged engines on
the current platform.  Requesting unsupported ``codex`` refuses before
launching or writing running metadata, with an actionable missing-engine
error; do not silently select Claude.

AC-2 (step 0 half): existing ``claude`` / ``claude-worker`` / ``claude-manager``
routing keeps its per-engine home through the real launcher boundary.  The
detached stub-backed smoke belongs to step 1.

AC-3: Bash and PowerShell declarations/help stay consistent with their actual
runner paths.  Runtime tests run where the runtime exists (bash here);
static parity assertions cover the other host and never claim a hosted test
that was not performed.

Everything runs against a temporary git project with ``HOME``,
``ILK_DATA_HOME`` and ``ILK_DATA_DIR`` pinned in every spawned process, so
the ambient release-layout guard and the real ``~/.ilk-data`` stay untouched.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SKILLS = _HERE.parent.parent  # skills/
_LAUNCH_SH = _SKILLS / "ilk-launcher" / "scripts" / "launch.sh"
_LAUNCH_PS1 = _SKILLS / "ilk-launcher" / "scripts" / "launch.ps1"
_CLAUDE_RUNNER = _SKILLS / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
_CLAUDE_RUNNER_PS1 = _SKILLS / "ilk-loop" / "scripts" / "run_ilk_loop_claude.ps1"

# Artifacts a launch writes into the external launcher dir.  Their absence is
# how "refuses before writing running metadata" is measured, not asserted.
_RUNNING_METADATA = ("last-launch.json", "running.pid", "mcp-worker.json")


# ── hermetic process helpers ─────────────────────────────────────────────────

def _clean_env(home: Path) -> dict[str, str]:
    """Minimal env with the three roots pinned, and no ambient ILK state.

    Inheriting the driver's ``ILK_*`` would leak ``ILK_DEFAULT_ENGINE``,
    ``ILK_WORKER_MODE`` and friends into the launcher and change the routing
    under test.  Only PATH survives, plus the pins.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "ILK_DATA_HOME": str(home / ".ilk-data"),
        "ILK_DATA_DIR": str(home / ".ilk-data"),
    }


def _make_project(root: Path) -> Path:
    """A minimal git project so ilk_paths.py can resolve a project key."""
    project = root / "project"
    (project / "docs" / "plans").mkdir(parents=True)
    (project / "docs" / "plans" / "MASTER-test.md").write_text(
        "---\nstatus: active\n---\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "init", "-q"], cwd=project, check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return project


def _run_launch(
    home: Path,
    project: Path,
    *args: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run the real ``launch.sh`` CLI entry point."""
    return subprocess.run(
        ["bash", str(_LAUNCH_SH), "--project-path", str(project), *args],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace", env=_clean_env(home),
    )


def _advertised_engines(source: str, pattern: str) -> list[str]:
    match = re.search(pattern, source, re.M)
    assert match, f"engine declaration not found in launcher source: {pattern!r}"
    return re.findall(r"[A-Za-z][A-Za-z0-9_-]*", match.group(1))


def _running_metadata(home: Path) -> list[str]:
    """Relative paths of any launch-state artifacts under the pinned roots."""
    found: list[str] = []
    for root in (home / ".ilk-data", home):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.name in _RUNNING_METADATA:
                found.append(str(path.relative_to(home)))
    return sorted(found)


# ── AC-1: codex is refused before side effects, with a missing-engine error ──

class TestUnsupportedEngineIsRefusedBeforeSideEffects:
    """AC-1, the incident: ``--engine codex`` with no Codex runner packaged."""

    def test_codex_refuses_before_launch_or_running_metadata(
        self, tmp_path: Path
    ) -> None:
        """Non-dry ``--engine codex`` exits non-zero and writes nothing.

        A non-dry invocation on purpose: ``--dry-run`` short-circuits before
        the spawn path, so it cannot show that a real launch is refused.
        """
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(home, project, "--engine", "codex")

        assert result.returncode != 0, (
            f"--engine codex was accepted (exit 0).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "not packaged" in combined, (
            "codex refusal is not a missing-engine error. "
            f"output was:\n{combined}"
        )
        assert re.search(r"valid engines", combined, re.I), (
            "codex refusal does not name the engines that are available. "
            f"output was:\n{combined}"
        )
        artifacts = _running_metadata(home)
        assert artifacts == [], (
            f"refusal wrote running metadata anyway: {artifacts}"
        )

    def test_codex_refusal_does_not_silently_select_claude(
        self, tmp_path: Path
    ) -> None:
        """A refused codex request must not fall through to a Claude run."""
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(home, project, "--engine", "codex")
        combined = result.stdout + result.stderr

        assert result.returncode != 0
        assert "launched" not in combined.lower(), (
            f"codex request produced a launch. output was:\n{combined}"
        )
        assert not re.search(r"WorkerEngine:\s*claude\b", combined), (
            f"codex request selected a Claude engine. output was:\n{combined}"
        )

    def test_unknown_engine_is_also_refused(self, tmp_path: Path) -> None:
        """Negative control: an unknown name is refused the same way."""
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(home, project, "--engine", "bogus-engine")

        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "not packaged" in combined, (
            f"unknown engine refusal lost the missing-engine wording:\n{combined}"
        )
        assert _running_metadata(home) == []


# ── AC-1/AC-3: help and declarations advertise only packaged engines ─────────

class TestAdvertisedEnginesArePackaged:
    """The advertised set is exactly the set with a runner on disk."""

    def test_every_advertised_engine_has_a_packaged_runner(self) -> None:
        source = _LAUNCH_SH.read_text(encoding="utf-8")
        engines = _advertised_engines(source, r'^VALID_ENGINES="([^"]+)"')
        assert engines, "launch.sh advertised no engines at all"
        for engine in engines:
            runner = _runner_path_for_engine(engine)
            assert runner.is_file(), (
                f"launch.sh advertises {engine!r} but its runner is missing: "
                f"{runner}"
            )

    def test_help_offers_only_packaged_engines_and_documents_codex(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(home, project, "--help")
        assert result.returncode == 0, result.stderr
        help_text = result.stdout

        block = re.search(r"--engine ENGINE(.*?)(?=\n  --|\Z)", help_text, re.S)
        assert block, f"--help has no --engine section:\n{help_text}"
        engine_help = block.group(1)

        source = _LAUNCH_SH.read_text(encoding="utf-8")
        engines = _advertised_engines(source, r'^VALID_ENGINES="([^"]+)"')
        for engine in engines:
            assert engine in engine_help, (
                f"--help does not offer packaged engine {engine!r}.\n"
                f"--engine block was:\n{engine_help}"
            )
        assert "codex" in engine_help, (
            f"--help does not document the unsupported Codex case.\n"
            f"--engine block was:\n{engine_help}"
        )
        assert re.search(r"codex[^.]*not\s+packaged", engine_help, re.I), (
            "--help mentions codex but does not say it is un-packaged. "
            f"--engine block was:\n{engine_help}"
        )


def _runner_path_for_engine(engine: str) -> Path:
    """Resolve the runner the way the launcher does, via its own function.

    Uses ``runner_script_for_engine`` so the test can never disagree with the
    launcher about which script an engine maps to.
    """
    body = re.search(
        r"^runner_script_for_engine\(\) \{.*?^\}",
        _LAUNCH_SH.read_text(encoding="utf-8"), re.M | re.S,
    )
    assert body, "runner_script_for_engine not found in launch.sh"
    probe = subprocess.run(
        ["bash", "-c",
         f'set -euo pipefail\n_SKILL_ROOT="{_SKILLS}"\n{body.group()}\n'
         f'runner_script_for_engine "{engine}"'],
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    assert probe.returncode == 0, (
        f"runner_script_for_engine({engine!r}) failed: {probe.stderr}"
    )
    return Path(probe.stdout.strip())


# ── AC-2: each packaged engine keeps its own home through the CLI boundary ───

class TestPackagedEnginesRouteToTheirOwnHomes:
    """Positive routing: the three packaged engines are not interchangeable."""

    def test_claude_routes_to_the_planner_home(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(home, project, "--engine", "claude", "--dry-run")

        assert result.returncode == 0, result.stderr
        assert "WorkerEngine: claude" in result.stdout
        assert "ClaudeConfigDir: (default ~/.claude)" in result.stdout, (
            f"claude did not report the planner home:\n{result.stdout}"
        )

    def test_claude_worker_routes_to_the_worker_home(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(
            home, project, "--engine", "claude-worker", "--dry-run"
        )

        assert result.returncode == 0, result.stderr
        assert "WorkerEngine: claude-worker" in result.stdout
        expected_home = home / ".claude-worker"
        assert f"ClaudeConfigDir: {expected_home}" in result.stdout, (
            f"claude-worker did not report the worker home:\n{result.stdout}"
        )
        assert f"IlkSkillHome: {expected_home}/skills" in result.stdout

    def test_claude_manager_routes_to_the_manager_home(self, tmp_path: Path) -> None:
        """claude-manager is not the planner: the manager home carries the
        session identity, the toolkit stays this checkout.

        This is the "do not silently select Claude" control for a *supported*
        engine: before the fix the dry-run branch reported ``(default
        ~/.claude)`` here, which is the planner's home.
        """
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(
            home, project, "--engine", "claude-manager", "--dry-run"
        )

        assert result.returncode == 0, result.stderr
        assert "WorkerEngine: claude-manager" in result.stdout
        expected_home = home / ".claude-manager"
        assert f"ClaudeConfigDir: {expected_home}" in result.stdout, (
            f"claude-manager reported a home other than the manager home:\n"
            f"{result.stdout}"
        )
        assert "IlkSkillHome: (this checkout)" in result.stdout


# ── AC-3: static parity for the other host (PowerShell) ──────────────────────

def _powershell_valid_engines(source: str) -> list[str]:
    match = re.search(r"^\$ValidEngines = (.*)$", source, re.M)
    assert match, "launch.ps1 has no $ValidEngines declaration"
    return re.findall(r"'([^']+)'", match.group(1))


def _powershell_runner_cases(source: str) -> dict[str, str]:
    """Map each Get-RunnerScript switch arm to its runner expression."""
    body = re.search(
        r"^function Get-RunnerScript \{.*?^\}", source, re.M | re.S
    )
    assert body, "Get-RunnerScript not found in launch.ps1"
    arms = re.findall(
        r"'([^']+)'\s*\{([^}]*)\}", body.group(0)
    )
    return {name: code for name, code in arms}


class TestPowerShellDeclarationsMatchRunnerPaths:
    """Static parity assertions on the host we cannot execute here.

    These read ``launch.ps1`` as text.  They do not claim a PowerShell test
    ran; ``test_powershell_cli_refuses_codex_before_running_metadata`` is the
    runtime test and it skips where ``pwsh`` is absent.
    """

    def test_every_advertised_engine_maps_to_a_packaged_runner(self) -> None:
        source = _LAUNCH_PS1.read_text(encoding="utf-8")
        engines = _powershell_valid_engines(source)
        assert engines, "launch.ps1 advertised no engines at all"
        cases = _powershell_runner_cases(source)
        for engine in engines:
            assert engine in cases, (
                f"launch.ps1 advertises {engine!r} but Get-RunnerScript has "
                f"no arm for it"
            )
            assert "run_ilk_loop_claude.ps1" in cases[engine], (
                f"Get-RunnerScript maps {engine!r} to {cases[engine]!r}, "
                f"not the packaged Claude runner"
            )
        assert _CLAUDE_RUNNER_PS1.is_file(), (
            f"the PowerShell runner launch.ps1 points at is missing: "
            f"{_CLAUDE_RUNNER_PS1}"
        )

    def test_codex_arm_is_a_missing_engine_refusal(self) -> None:
        source = _LAUNCH_PS1.read_text(encoding="utf-8")
        cases = _powershell_runner_cases(source)
        assert "codex" in cases, (
            "Get-RunnerScript has no codex arm; an un-packaged engine would "
            "fall through to 'Unknown engine' instead of a missing-engine error"
        )
        assert "not packaged" in cases["codex"], (
            f"codex arm does not report a missing engine: {cases['codex']!r}"
        )

    def test_resolve_engine_refuses_unadvertised_engines(self) -> None:
        source = _LAUNCH_PS1.read_text(encoding="utf-8")
        body = re.search(
            r"^function Resolve-Engine \{.*?^\}", source, re.M | re.S
        )
        assert body, "Resolve-Engine not found in launch.ps1"
        assert "not packaged" in body.group(0), (
            "Resolve-Engine does not use the missing-engine wording for an "
            "unadvertised engine"
        )
        assert "Get-RunnerScript" in body.group(0), (
            "Resolve-Engine does not consult the runner resolver, so it "
            "validates names without checking availability"
        )

    def test_help_lists_the_advertised_set_and_documents_codex(self) -> None:
        source = _LAUNCH_PS1.read_text(encoding="utf-8")
        engines = _powershell_valid_engines(source)
        comment = re.search(
            r"# Worker engine:(.*?)\[string\]\$Engine", source, re.S
        )
        assert comment, "launch.ps1 has no -Engine help comment"
        help_text = comment.group(1)
        for engine in engines:
            assert engine in help_text, (
                f"-Engine help does not offer {engine!r}:\n{help_text}"
            )
        assert "codex" in help_text, (
            f"-Engine help does not document the unsupported Codex case:\n"
            f"{help_text}"
        )
        assert re.search(r"codex", help_text, re.I) and re.search(
            r"missing-engine|not\s+packaged|rejected", help_text, re.I
        ), (
            f"-Engine help mentions codex but not that it is refused:\n"
            f"{help_text}"
        )

    def test_bash_and_powershell_agree_on_the_claude_runner(self) -> None:
        """Both hosts route every engine they advertise to the Claude runner.

        The engine *sets* may differ per host (AC-1 says "on the current
        platform"); the runner they map to is the shared contract.
        """
        bash_source = _LAUNCH_SH.read_text(encoding="utf-8")
        ps_source = _LAUNCH_PS1.read_text(encoding="utf-8")
        for engine in _powershell_valid_engines(ps_source):
            body = re.search(
                r"^runner_script_for_engine\(\) \{.*?^\}",
                bash_source, re.M | re.S,
            )
            assert body, "runner_script_for_engine not found in launch.sh"
            assert engine in body.group(0), (
                f"PowerShell advertises {engine!r} but the bash launcher does "
                f"not route it at all — the two hosts have diverged"
            )
        assert _CLAUDE_RUNNER.is_file() and _CLAUDE_RUNNER_PS1.is_file()


# ── AC-3: runtime test on the host that has the runtime ──────────────────────

class TestPowerShellCliRefusal:
    """Runtime coverage for the PowerShell entry point.

    Skips when ``pwsh`` is absent.  A skip is a skip: nothing here reports a
    PowerShell result that did not execute.
    """

    @pytest.mark.skipif(shutil.which("pwsh") is None,
                        reason="PowerShell runtime unavailable on this host")
    def test_codex_refuses_before_running_metadata(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)
        env = _clean_env(home)

        probe = tmp_path / "probe.ps1"
        probe.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            f"& '{_LAUNCH_PS1}' -ProjectPath '{project}' -Engine codex\n"
            "if ($LASTEXITCODE -eq 0) { throw 'codex was accepted' }\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(probe)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace", env=env,
        )
        combined = result.stdout + result.stderr
        assert "not packaged" in combined, (
            f"PowerShell codex refusal is not a missing-engine error:\n{combined}"
        )
        assert _running_metadata(home) == [], (
            "PowerShell refusal wrote running metadata: "
            f"{_running_metadata(home)}"
        )
