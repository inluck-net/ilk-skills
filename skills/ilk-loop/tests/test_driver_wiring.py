"""Structural wiring tests for run_ilk_loop_claude.sh.

`bash -n` (test_ship_gap.py::TestDriverParses) validates syntax only: it
cannot see an unresolved function name, and it cannot see a python3
invocation resolving state from the launcher's cwd instead of the
project's. The 2026-08-30 batch shipped both shapes past it. These tests
assert driver *structure* — by reading its lines and by executing the
exact snippets the driver executes — rather than by parsing tokens.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_DRIVER = _SCRIPTS / "run_ilk_loop_claude.sh"
_LOOP_STATUS = _SCRIPTS / "loop_status.py"

sys.path.insert(0, str(_SCRIPTS))
from importlib import import_module  # noqa: E402

ilk_paths = import_module("ilk_paths")  # noqa: E402

# Minimal external plans tree, written under a sandboxed ILK_DATA_HOME so
# the resolution under test never reads the real ~/.ilk-data.
_MASTER_TEXT = """\
---
master_plan: 2026-09-01-wiring-probe
batch_date: 2026-09-01
status: active
current_subplan: 2026-09-01-wiring-probe
---

# MASTER plan: wiring probe

## Sub-plan registry

| # | Order | Slug | Status |
|---|---|---|---|
| 1 | 1 | [2026-09-01-wiring-probe.md](./2026-09-01-wiring-probe.md) | pending |
"""

_SUBPLAN_TEXT = """\
---
plan: wiring-probe
status: pending
current_step: 0
estimated_steps: 1
last_updated: 2026-09-01
verification_tier: loop-verified
---

# Sub-plan: wiring probe
"""


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Hermetic loop project: a git repo whose plans live in a sandboxed
    ILK_DATA_HOME, keyed by the canonical project_key resolver."""
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-q"], cwd=project, check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    data_home = tmp_path / ".ilk-data"
    plans = data_home / "projects" / ilk_paths.project_key(project) / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-09-01-wiring-probe.md").write_text(
        _MASTER_TEXT, encoding="utf-8",
    )
    (plans / "2026-09-01-wiring-probe.md").write_text(
        _SUBPLAN_TEXT, encoding="utf-8",
    )
    return project, data_home


def _hermetic_env(data_home: Path) -> dict[str, str]:
    env = {**os.environ, "ILK_DATA_HOME": str(data_home)}
    # The back-compat alias must not override the primary var (AC-2 of the
    # data-home convention; precedence is ILK_DATA_HOME → ILK_DATA_DIR).
    env.pop("ILK_DATA_DIR", None)
    return env


def _run_status(cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LOOP_STATUS)],
        cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )


def _final_status_snippet() -> str:
    """The driver's own final-status lines — everything between
    `echo "Final loop_status:"` and the next blank line."""
    lines = _DRIVER.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if 'echo "Final loop_status:"' in line:
            block = []
            for follow in lines[i + 1:]:
                if not follow.strip():
                    break
                block.append(follow)
            assert block, "no invocation follows 'Final loop_status:' in the driver"
            return "\n".join(block)
    raise AssertionError(
        "driver no longer prints 'Final loop_status:' — update this test"
    )


def _ship_gap_block() -> str:
    """The driver's ship-gap accounting block, extracted verbatim: from the
    `local _SHIP_GAP_JSON=""` declaration right after the `# Ship-gap:`
    comment through the for-loop's closing `done` (driver :2210-2231)."""
    lines = _DRIVER.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if "# Ship-gap: committed-vs-changed path accounting" not in line:
            continue
        block: list[str] = []
        for follow in lines[i + 1:]:
            if not block and "_SHIP_GAP_JSON" not in follow:
                continue  # skip blank/comment lines before the declaration
            block.append(follow)
            if follow.strip() == "done":
                return "\n".join(block)
        raise AssertionError("ship-gap block has no closing done")
    raise AssertionError(
        "driver no longer carries the '# Ship-gap:' block — update this test"
    )


def _make_two_commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A git repo with two commits and a clean tree; returns (repo, sha1, sha2)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True,
                       capture_output=True, text=True, encoding="utf-8")

    git("init", "-q")
    git("config", "user.email", "test@test")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("init\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "init")
    sha_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    git("commit", "-q", "-am", "change a")
    sha_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()
    return repo, sha_before, sha_after


def _write_heads_file(path: Path, repo: Path, sha: str) -> None:
    """`get_repo_heads`' exact `repo=sha` format, one line per repo."""
    path.write_text(f"{repo}={sha}\n", encoding="utf-8")


def _dotsource_env() -> dict[str, str]:
    """Env for dot-sourcing the driver: ILK_DOTSOURCE_ONLY defines the
    functions without running main(); ILK_SKILL_HOME pins skill-root
    resolution to THIS repo's skills dir, not an installed copy."""
    return {
        **os.environ,
        "ILK_DOTSOURCE_ONLY": "1",
        "ILK_SKILL_HOME": str(_SCRIPTS.parent.parent),
    }


def _dotsource(setup_lines: list[str], body_lines: list[str]) -> subprocess.CompletedProcess:
    """Source the driver, run `setup_lines`, then execute `body_lines` inside
    a probe function (the block under test uses `local`, which is
    function-scoped). `set +e` mirrors the driver's own bookkeeping context
    (:2191) — the ship-gap block only ever runs under it."""
    script = "\n".join(
        [
            f'source "{_DRIVER}"',
            "set +e",
            *setup_lines,
            "probe() {",
            *body_lines,
            "}",
            "probe",
        ]
    )
    return subprocess.run(
        ["bash", "-c", script],
        env=_dotsource_env(), capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )


class TestLoopStatusCwd:
    """AC-4 + AC-5: every loop_status invocation resolves the project, not
    the launcher's cwd."""

    def test_every_loop_status_call_is_cwd_scoped(self) -> None:
        """AC-4: each `python3 "$LOOP_STATUS_SCRIPT"` execution sits inside a
        `cd "$PROJECT_PATH"` construct.

        `[[ -f "$LOOP_STATUS_SCRIPT" ]]` existence guards reference the
        variable without executing it, so they are skipped.
        """
        if not _DRIVER.exists():
            pytest.skip("driver script not found")
        text = _DRIVER.read_text(encoding="utf-8")
        referencing = [
            line for line in text.splitlines()
            if 'python3 "$LOOP_STATUS_SCRIPT"' in line
        ]
        executions = [
            line for line in referencing
            if not ("[[" in line and " -f " in line)
        ]
        assert executions, (
            'no `python3 "$LOOP_STATUS_SCRIPT"` executions found — the parser '
            "matches nothing; check the driver's invocation style"
        )
        unwrapped = [
            line for line in executions if 'cd "$PROJECT_PATH"' not in line
        ]
        assert not unwrapped, (
            f'{len(unwrapped)} of {len(executions)} `python3 '
            f'"$LOOP_STATUS_SCRIPT"` invocations resolve from the launcher\'s '
            f"cwd instead of $PROJECT_PATH: {unwrapped}"
        )

    def test_final_status_resolves_project_not_cwd(self, tmp_path: Path) -> None:
        """AC-5: the driver's final-status code path, run from a launcher cwd
        outside the project, resolves the *project's* plans dir.

        Pins the mechanism first: loop_status.py walks up from cwd, so the
        bare invocation from outside the project reports `no plans dir
        found` while the same invocation from the project root does not.
        Then executes the driver's own `Final loop_status:` lines verbatim
        from the outside cwd — that is the assertion the driver must satisfy.
        """
        project, data_home = _make_project(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        env = _hermetic_env(data_home)

        # The resolver is cwd-driven: bare from outside → exit 2, no plans.
        bare = _run_status(outside, env)
        assert bare.returncode == 2
        assert "no plans dir found" in bare.stdout + bare.stderr

        # The identical invocation from the project root finds the plans.
        wrapped = _run_status(project, env)
        assert wrapped.returncode != 2
        assert "no plans dir found" not in wrapped.stdout + wrapped.stderr

        # The driver's own final-status lines, executed verbatim from the
        # launcher's cwd. The driver declares PROJECT_PATH itself; the env
        # stands in for it exactly as the loop would have set it.
        proc = subprocess.run(
            ["bash", "-c", _final_status_snippet()],
            cwd=outside,
            env={
                **env,
                "LOOP_STATUS_SCRIPT": str(_LOOP_STATUS),
                "PROJECT_PATH": str(project),
            },
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert "no plans dir found" not in proc.stdout + proc.stderr, (
            "the driver's final-status line resolved from the launcher's cwd "
            "and could not see the project's plans dir (defect C)"
        )


class TestShipGapWiring:
    """AC-1 + AC-2 + AC-3: the ship-gap block's helpers resolve, read the
    heads files, and actually reach ship_gap.py."""

    def test_head_sha_helpers_resolve(self) -> None:
        """AC-1: after `ILK_DOTSOURCE_ONLY=1 source <driver>`, both helpers
        are declared functions. This is the assertion `bash -n` structurally
        cannot make — a syntax check passes on an unresolved name."""
        if not _DRIVER.exists():
            pytest.skip("driver script not found")
        proc = subprocess.run(
            ["bash", "-c",
             f'source "{_DRIVER}"\ndeclare -F head_before_sha head_after_sha'],
            env=_dotsource_env(), capture_output=True, text=True,
            encoding="utf-8", timeout=60,
        )
        assert proc.returncode == 0, (
            f"declare -F failed rc={proc.returncode}; stderr: {proc.stderr.strip()}"
        )
        declared = proc.stdout.split()
        assert "head_before_sha" in declared and "head_after_sha" in declared, (
            f"expected both helpers declared; declared matching: {declared}"
        )

    def test_head_sha_helpers_read_the_heads_file(self, tmp_path: Path) -> None:
        """AC-2 + AC-3: both helpers return the recorded sha in
        `get_repo_heads`' exact `repo=sha` format, and a repo recorded as
        `(unknown)` yields empty so the existing `[[ -n ... ]]` guard skips
        it — exactly as `get_new_commit_count` treats `(unknown)` as 0."""
        repo, sha_before, sha_after = _make_two_commit_repo(tmp_path)
        ghost = tmp_path / "ghost"
        before_file = tmp_path / "heads-before"
        after_file = tmp_path / "heads-after"
        _write_heads_file(before_file, repo, sha_before)
        _write_heads_file(after_file, repo, sha_after)
        # A second repo line, as get_repo_heads writes one line per repo.
        with before_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{ghost}=(unknown)\n")
        with after_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{ghost}=(unknown)\n")

        proc = _dotsource(
            setup_lines=[
                f'BEFORE="{before_file}"',
                f'AFTER="{after_file}"',
            ],
            body_lines=[
                f'printf "B=%s\\n" "$(head_before_sha "{repo}" "$BEFORE")"',
                f'printf "A=%s\\n" "$(head_after_sha "{repo}" "$AFTER")"',
                f'printf "UB=%s\\n" "$(head_before_sha "{ghost}" "$BEFORE")"',
                f'printf "UA=%s\\n" "$(head_after_sha "{ghost}" "$AFTER")"',
            ],
        )
        assert proc.returncode == 0, (
            f"probe rc={proc.returncode}; stderr: {proc.stderr.strip()}"
        )
        values = dict(
            line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
        )
        assert values.get("B") == sha_before, f"B={values.get('B')!r} in {proc.stdout!r}"
        assert values.get("A") == sha_after, f"A={values.get('A')!r} in {proc.stdout!r}"
        assert values.get("UB") == "", f"UB={values.get('UB')!r} (want empty)"
        assert values.get("UA") == "", f"UA={values.get('UA')!r} (want empty)"

    def test_ship_gap_block_reaches_ship_gap_py(self, tmp_path: Path) -> None:
        """AC-2, end-to-end: the consumer-entry-point assertion — the block
        that actually matters. Extract the driver's own ship-gap block,
        point REPOS at a hermetic two-commit repo, write the two heads
        files, execute the block verbatim, and require `_SHIP_GAP_JSON` to
        be non-empty and parse. Helper resolution (the tests above) is
        necessary; ship_gap.py actually being reached is the thing."""
        repo, sha_before, sha_after = _make_two_commit_repo(tmp_path)
        before_file = tmp_path / "heads-before"
        after_file = tmp_path / "heads-after"
        _write_heads_file(before_file, repo, sha_before)
        _write_heads_file(after_file, repo, sha_after)

        proc = _dotsource(
            setup_lines=[
                f'REPOS=("{repo}")',
                f'heads_before_file="{before_file}"',
                f'heads_after_file="{after_file}"',
            ],
            body_lines=[
                _ship_gap_block(),
                'printf "__SHIP_GAP_JSON=%s\\n" "$_SHIP_GAP_JSON"',
            ],
        )
        lines = proc.stdout.splitlines()
        payload_start = next(
            (i for i, line in enumerate(lines)
             if line.startswith("__SHIP_GAP_JSON=")),
            None,
        )
        assert payload_start is not None, (
            "the ship-gap block never reached ship_gap.py — _SHIP_GAP_JSON is "
            f"empty. probe rc={proc.returncode}; stderr: {proc.stderr.strip()!r}"
        )
        # ship_gap.py --json pretty-prints (indent=2), so the payload spans
        # lines. The printf is the probe's last statement, so everything from
        # the marker line onward is the JSON.
        raw = "\n".join(
            [lines[payload_start][len("__SHIP_GAP_JSON="):]]
            + lines[payload_start + 1:]
        )
        assert raw.strip(), f"_SHIP_GAP_JSON is empty; stderr: {proc.stderr.strip()!r}"
        parsed = json.loads(raw)
        assert isinstance(parsed, dict) and "unexplained" in parsed
