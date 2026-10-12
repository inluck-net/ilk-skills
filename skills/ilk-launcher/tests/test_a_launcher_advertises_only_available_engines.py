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
routing keeps its per-engine home through the real launcher boundary.
AC-2 (step 1 half): a detached stub-backed smoke through the real ``launch.sh``
spawn reaches the authoritative local gate (``run_local_checks.py`` on the
sub-plan's declared ``local_checks``) and a deliberately red gate blocks
advancement while a green one does not.  Default-engine and worker-home
routing survive the real launch.

AC-3: Bash and PowerShell declarations/help stay consistent with their actual
runner paths.  Runtime tests run where the runtime exists (bash here);
static parity assertions cover the other host and never claim a hosted test
that was not performed.  The two hosts must also agree on which engine is
the no-flag default — they did not (``launch.ps1`` said ``claude``,
``launch.sh`` said ``claude-worker``).

Everything runs against a temporary git project with ``HOME``,
``ILK_DATA_HOME`` and ``ILK_DATA_DIR`` pinned in every spawned process, so
the ambient release-layout guard and the real ``~/.ilk-data`` stay untouched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
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
# ``last-exit.json`` is the runner's terminal sentinel and ``running.pid`` the
# PID file: a refusal that leaves either behind is a half-launch.
_RUNNING_METADATA = (
    "last-launch.json", "running.pid", "mcp-worker.json",
    "last-exit.json", "phase.json",
)


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

    def test_codex_refusal_leaves_no_pid_or_sentinel(self, tmp_path: Path) -> None:
        """A refused engine leaves neither a PID file nor a terminal sentinel.

        ``_running_metadata`` covers both; this pins them by name so the
        claim "no PID/sentinel artifacts" has a denominator of its own
        rather than riding on a shared list that could be narrowed later.
        """
        home = tmp_path / "home"
        home.mkdir()
        project = _make_project(tmp_path)

        result = _run_launch(home, project, "--engine", "codex")
        assert result.returncode != 0

        for name in ("running.pid", "last-exit.json"):
            hits = sorted(
                str(p.relative_to(home))
                for p in home.rglob(name)
            )
            assert hits == [], (
                f"--engine codex left {name} behind: {hits}"
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

    def test_both_hosts_share_one_default_engine(self) -> None:
        """AC-3: the no-flag default is one engine on both hosts.

        Measured 2026-10-12: ``launch.sh`` set ``DEFAULT_ENGINE="claude-worker"``
        while ``launch.ps1`` set ``$DefaultEngine = 'claude'``, and
        ``test_worker_engine_routing.{sh,ps1}`'s AC-3 pinned the planner
        reading.  A default that differs per host is not a platform detail —
        it is the same forgot-the-flag spawn landing on two different accounts.
        """
        bash_source = _LAUNCH_SH.read_text(encoding="utf-8")
        ps_source = _LAUNCH_PS1.read_text(encoding="utf-8")

        bash_default = re.search(
            r'^DEFAULT_ENGINE="([^"]+)"', bash_source, re.M
        )
        assert bash_default, "launch.sh has no DEFAULT_ENGINE declaration"
        ps_default = re.search(
            r"^\$DefaultEngine = '([^']+)'", ps_source, re.M
        )
        assert ps_default, "launch.ps1 has no $DefaultEngine declaration"

        assert bash_default.group(1) == ps_default.group(1), (
            f"the hosts disagree on the default engine: "
            f"launch.sh DEFAULT_ENGINE={bash_default.group(1)!r} vs "
            f"launch.ps1 $DefaultEngine={ps_default.group(1)!r}"
        )
        # The floor itself is pinned functionally by test_default_engine_floor.
        # Here we only require the advertised default to be one of the
        # packaged engines, so a host cannot "agree" by both being wrong.
        bash_engines = _advertised_engines(
            bash_source, r'^VALID_ENGINES="([^"]+)"'
        )
        assert bash_default.group(1) in bash_engines, (
            f"launch.sh's default {bash_default.group(1)!r} is not in its own "
            f"VALID_ENGINES {bash_engines}"
        )
        ps_engines = _powershell_valid_engines(ps_source)
        assert ps_default.group(1) in ps_engines, (
            f"launch.ps1's default {ps_default.group(1)!r} is not in its own "
            f"$ValidEngines {ps_engines}"
        )


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


# ── AC-2 (step 1): a detached stub-backed run reaches the real local gate ────
#
# The smoke drives the REAL ``launch.sh`` CLI (no --engine, so the default
# floor is what spawns), which detaches ``run_ilk_loop_claude.sh`` with a stub
# ``claude`` on PATH and a bootstrapped stub worker home.  The gate the run
# reaches is the production one: ``run_local_checks.py`` executing the
# ``local_checks`` the sub-plan declares.  Nothing here stubs the gate.
#
# Two runs, one per verdict.  The stub agent always lands the same work (a
# commit carrying the step trailer and a ``current_step`` 0 -> 1 bump); what
# differs is only whether the declared gate accepts it.  Green must leave the
# bump standing; red must roll it back.  That contrast is "red blocks
# advancement" — the observable is the plan's own pointer, not a log word.

_NEEDS_GTIMEOUT = pytest.mark.skipif(
    shutil.which("gtimeout") is None,
    reason="the bash runner refuses to start without gtimeout",
)

_SENTINEL_TERMINAL = frozenset({
    "completed", "all-shipped", "shipped-unproven", "local_checks_failed",
    "local_checks_failed_no_commits", "ship_integrity_violation",
    "dispatch_identity_invalid", "iteration_context_invalid",
    "budget-exhausted", "interrupted", "max-iterations", "stopped",
    "gates-off", "run_inputs_changed", "selfmod_merge_failed", "error",
})


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _build_detached_world(root: Path, slug: str, gate_command: str) -> dict:
    """A hermetic project + data home + stub worker + stub ``claude`` CLI.

    ``gate_command`` is written verbatim into the sub-plan's ``local_checks``,
    so what the run executes is whatever the plan declares — the production
    contract, not a test double.
    """
    home = root / "home"
    home.mkdir(parents=True)
    data_home = home / ".ilk-data"

    project = root / "project"
    project.mkdir()
    _git(project, "init", "-q")

    sys.path.insert(0, str(_SKILLS / "ilk-loop" / "scripts"))
    import ilk_paths  # noqa: PLC0415  (needs the env pin below)
    with _pinned_data_home(data_home):
        key = ilk_paths.project_key(project)
    plans = data_home / "projects" / key / "plans"
    plans.mkdir(parents=True)

    stem = f"2026-10-12-{slug}"
    # Registry link MUST carry the .md suffix: extract_subplan_files() keys on
    # the literal registry filenames, and a bare stem yields 0 matches, which
    # the driver reports as dispatch_identity_invalid before any gate runs.
    (plans / "MASTER-2026-10-12-execution-plan.md").write_text(
        "---\n"
        "master_plan: 2026-10-12-execution\n"
        "batch_date: 2026-10-12\n"
        "status: active\n"
        "supervised_only: false\n"
        "---\n\n# MASTER\n\n## Sub-plan registry\n\n"
        "| # | Slug |\n|---|---|\n"
        f"| 1 | [{slug}](./{stem}.md) |\n",
        encoding="utf-8",
    )
    (plans / f"{stem}.md").write_text(
        "---\n"
        f"plan: {slug}\n"
        "status: in-progress\n"
        "current_step: 0\n"
        # 2, not 1: with 1 the driver ships on this step and the run ends
        # `shipped-unproven`, which is a different question than gate verdicts.
        "estimated_steps: 2\n"
        "---\n\n"
        f"# {slug}\n\n"
        "### Step 0 — do the thing\n\n"
        "```yaml\n"
        "local_checks:\n"
        f"  - command: {json.dumps(gate_command)}\n"
        "    timeout: 60\n"
        "```\n\nBody.\n",
        encoding="utf-8",
    )

    # Bootstrapped worker home: without it launch.sh fails closed rather than
    # spawning, so the smoke would never reach a gate at all.
    worker_home = home / ".claude-worker"
    worker_home.mkdir(parents=True)
    (worker_home / "settings.json").write_text(
        json.dumps({
            "env": {
                "ANTHROPIC_BASE_URL": "https://fake-gateway.example.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": "fake-token-for-testing",
            }
        }),
        encoding="utf-8",
    )

    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub_log = root / "stub-worker.log"
    subplan = plans / f"{stem}.md"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'echo "CLAUDE_CONFIG_DIR=${{CLAUDE_CONFIG_DIR:-}}" >> "{stub_log}"\n'
        f'echo "ILK_SKILL_HOME=${{ILK_SKILL_HOME:-}}" >> "{stub_log}"\n'
        f'python3 - "{subplan}" >> "{stub_log}" <<\'EOP\'\n'
        "import re, sys\n"
        "from pathlib import Path\n"
        "p = Path(sys.argv[1]); b = p.read_text()\n"
        "b = re.sub(r'^current_step: 0', 'current_step: 1', b, count=1, flags=re.M)\n"
        "p.write_text(b)\n"
        "print('bumped current_step 0 -> 1')\n"
        "EOP\n"
        "touch RED\n"
        "git add RED\n"
        "git -c user.email=t@example.com -c user.name=t commit -q "
        f"-m 'feat: the work [plan:{slug}#step-0]'\n"
        "echo 'stub agent done'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = _clean_env(home)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return {
        "root": root, "home": home, "data_home": data_home, "key": key,
        "project": project, "plans": plans, "subplan": subplan, "stem": stem,
        "slug": slug, "env": env, "stub_log": stub_log, "gate_command": gate_command,
    }


class _pinned_data_home:
    """Pin ILK_DATA_HOME for a block (ilk_paths.project_key reads it)."""

    def __init__(self, data_home: Path) -> None:
        self._data_home = data_home
        self._prev: str | None = None

    def __enter__(self) -> Path:
        self._prev = os.environ.get("ILK_DATA_HOME")
        os.environ["ILK_DATA_HOME"] = str(self._data_home)
        return self._data_home

    def __exit__(self, *exc: object) -> None:
        if self._prev is None:
            os.environ.pop("ILK_DATA_HOME", None)
        else:
            os.environ["ILK_DATA_HOME"] = self._prev


def _launch_detached(world: dict) -> subprocess.CompletedProcess[str]:
    """Real non-dry launch through the launcher CLI.  No --engine: the
    DEFAULT_ENGINE floor is what this has to exercise."""
    return subprocess.run(
        ["bash", str(_LAUNCH_SH), "--project-path", str(world["project"]),
         "--max-iterations", "1", "--run-local-checks"],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace", env=world["env"],
        cwd=str(world["root"]),
    )


def _wait_for_terminal_sentinel(world: dict, timeout: int = 120) -> dict:
    """Poll the canonical sentinel until the run leaves `running`."""
    runtime = world["data_home"] / "projects" / world["key"] / "runtime"
    candidates = [runtime / "launcher" / "last-exit.json",
                  runtime / "last-exit.json"]
    deadline = time.time() + timeout
    payload: dict = {}
    while time.time() < deadline:
        for cand in candidates:
            if not cand.is_file():
                continue
            try:
                payload = json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("state") and payload["state"] != "running":
                payload["_path"] = str(cand)
                return payload
        time.sleep(0.5)
    raise AssertionError(
        f"no terminal sentinel within {timeout}s; last read: {payload}"
    )


def _local_check_records(world: dict) -> list[dict]:
    """Every local_checks record this run wrote for our slug, from the JSONL."""
    log = world["data_home"] / "projects" / world["key"] / "logs" / ".ilk-loop.log"
    if not log.is_file():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for lc in rec.get("local_checks") or []:
            if lc.get("slug") == world["slug"]:
                out.append(lc)
    return out


def _frontmatter_int(world: dict, field: str) -> str:
    body = world["subplan"].read_text(encoding="utf-8")
    m = re.search(rf"^{field}:\s*(\S+)", body, re.M)
    return m.group(1) if m else "<absent>"


def _last_launch(world: dict) -> dict:
    runtime = world["data_home"] / "projects" / world["key"] / "runtime" / "launcher"
    hits = sorted(runtime.rglob("last-launch.json"))
    assert hits, f"launch wrote no last-launch.json under {runtime}"
    return json.loads(hits[0].read_text(encoding="utf-8"))


def _run_log_tail(world: dict, lines: int = 40) -> str:
    meta = _last_launch(world)
    log_file = meta.get("log_file", "")
    if not log_file or not Path(log_file).is_file():
        return ""
    return "\n".join(Path(log_file).read_text(encoding="utf-8").splitlines()[-lines:])


@_NEEDS_GTIMEOUT
class TestDetachedLaunchReachesItsLocalGate:
    """AC-2 step-1 half: detached stub-backed smoke, green and deliberately red."""

    @pytest.fixture(scope="class")
    def green(self, tmp_path_factory: pytest.TempPathFactory) -> dict:
        world = _build_detached_world(
            tmp_path_factory.mktemp("detached-green"), "detached-green", "true"
        )
        world["proc"] = _launch_detached(world)
        world["sentinel"] = _wait_for_terminal_sentinel(world)
        return world

    @pytest.fixture(scope="class")
    def red(self, tmp_path_factory: pytest.TempPathFactory) -> dict:
        # The red is introduced by the iteration's own work: the gate passes
        # until the stub lands RED.  An always-red gate is attribution-unmeasured
        # and this pair is about verdicts, not about the red-owner path.
        gate = (
            "python3 -c 'import pathlib,sys; raise SystemExit("
            '1 if pathlib.Path("RED").exists() else 0)\''
        )
        world = _build_detached_world(
            tmp_path_factory.mktemp("detached-red"), "detached-red", gate
        )
        world["proc"] = _launch_detached(world)
        world["sentinel"] = _wait_for_terminal_sentinel(world)
        return world

    # -- routing survives the real launch ---------------------------------

    def test_default_engine_is_preserved_through_the_spawn(self, green, red) -> None:
        """No --engine was passed: the floor, not a flag, chose the engine."""
        for world in (green, red):
            meta = _last_launch(world)
            assert meta.get("worker_engine") == "claude-worker", (
                f"a no-flag spawn used {meta.get('worker_engine')!r}; the "
                f"DEFAULT_ENGINE floor is claude-worker\n{meta}"
            )
            assert meta.get("run_local_checks") is True, meta

    def test_worker_home_isolation_survives_the_spawn(self, green) -> None:
        """The stub worker ran in a worker home, never the planner's."""
        assert green["stub_log"].is_file(), (
            f"the stub claude never ran.\n{green['proc'].stdout}\n"
            f"{green['proc'].stderr}"
        )
        log = green["stub_log"].read_text(encoding="utf-8")
        m = re.search(r"^CLAUDE_CONFIG_DIR=(.*)$", log, re.M)
        assert m and m.group(1).strip(), f"stub recorded no config dir:\n{log}"
        cfg = m.group(1).strip()
        assert cfg.endswith("worker-home") or cfg.endswith(".claude-worker"), (
            f"the stub worker was not routed to a worker home: {cfg}\n{log}"
        )
        assert str(green["home"]) in cfg, (
            f"the worker home escaped the pinned HOME: {cfg}\n{log}"
        )

    # -- the gate is the production one, and its verdict is recorded -------

    def test_green_gate_runs_the_declared_check_and_passes(self, green) -> None:
        records = _local_check_records(green)
        assert records, (
            f"the run never reached a local gate.\n"
            f"sentinel={green['sentinel']}\n{_run_log_tail(green)}"
        )
        assert any(r.get("outcome") == "pass" for r in records), records
        assert all(r.get("command") == green["gate_command"] for r in records), (
            f"the gate did not run the sub-plan's declared command:\n{records}"
        )

    def test_green_gate_leaves_the_step_advanced(self, green) -> None:
        """Green is only proven by the advance it permits."""
        assert _frontmatter_int(green, "current_step") == "1", (
            f"green gate did not leave the stub's 0 -> 1 bump standing.\n"
            f"sentinel={green['sentinel']}\n{_run_log_tail(green)}"
        )
        state = green["sentinel"].get("state", "")
        assert "local_checks_failed" not in state, (
            f"a passing gate ended the run as {state!r}:\n{_run_log_tail(green)}"
        )

    def test_red_gate_runs_the_declared_check_and_fails(self, red) -> None:
        records = _local_check_records(red)
        assert records, (
            f"the run never reached a local gate.\n"
            f"sentinel={red['sentinel']}\n{_run_log_tail(red)}"
        )
        assert any(r.get("outcome") == "fail" for r in records), records
        assert all(r.get("command") == red["gate_command"] for r in records), (
            f"the gate did not run the sub-plan's declared command:\n{records}"
        )

    def test_red_gate_blocks_advancement(self, red) -> None:
        """The stub bumped the pointer to 1; the red gate must take it back.

        Measured contrast with the green run: identical stub work, one gate
        verdict different, and the plan pointer is the thing that moves.
        """
        assert _frontmatter_int(red, "current_step") == "0", (
            f"a red gate let the step advance anyway.\n"
            f"sentinel={red['sentinel']}\n{_run_log_tail(red)}"
        )
        assert _frontmatter_int(red, "status") == "in-progress", (
            f"a red gate left the sub-plan past in-progress:\n{_run_log_tail(red)}"
        )
        tail = _run_log_tail(red)
        assert "current_step" in tail and "-> 0" in tail, (
            "the red gate produced no pointer rollback line; the pointer only "
            f"reads 0 because the bump never landed.\n{tail}"
        )

    def test_red_gate_names_its_failure_in_the_sentinel(self, red) -> None:
        state = red["sentinel"].get("state", "")
        assert "local_checks_failed" in state, (
            f"a red gate ended the run as {state!r}, which does not name the "
            f"gate as the cause.\nsentinel={red['sentinel']}"
        )
