"""Red-first pins: a red goes back to its owner.

Part of sub-plan ``a-red-goes-back-to-its-owner``
(MASTER-2026-10-03i).

Tests that ``suite_ledger.return_reds`` reopens the in-batch owner of each
attributed red id so the loop re-dispatches the owner instead of a cold
verify worker.  Also tests the runner integration: ``ledger_return_reds``
inside ``attempt_gate_first_fast_path`` sets ``GATE_FIRST_NO_DISPATCH=1``
when the function exits 0.

AC-1: a record with attributed id T owned by ``ours-y`` (a shipped sub-plan
      in the fake plans dir, ``estimated_steps: 2``): ``return-reds`` exits 0;
      ``ours-y`` reads ``status: in-progress``, ``current_step: 1``;
      ``run_local_checks.extract_step_local_checks(body, 1)`` includes a
      command containing T; the ``#### Returned red`` block sits after the
      ``## Findings`` heading line and names T.  Red-first.
AC-2: an attributed id with owner ``—`` ⇒ exit 3 and every plan file is
      byte-identical.  Red-first.
AC-3: returning T to ``ours-y`` a second time ⇒ exit 4, plan files
      byte-identical.  Red-first.
AC-4: with ``ILK_WORKER_SESSION=1`` ⇒ exit 1, plan files byte-identical.
      Red-first.
AC-5 (runner): ``ledger_return_reds`` in the runner calls
      ``suite_ledger.py return-reds`` and returns its exit code.  With a
      stub exiting 0 the function succeeds; with the stub exiting 3 it
      fails.  Red-first.
AC-6 (runner, static): ``ledger_return_reds`` is called inside
      ``attempt_gate_first_fast_path`` between ``if ! gate_first_results_are_green``
      and that branch's ``return 1``.  Red-first.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import suite_ledger  # noqa: E402


# ── module-level fixture: pin HOME + ILK_DATA_HOME, clear worker session ──


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME and ILK_DATA_HOME to tmp_path; clear ILK_WORKER_SESSION."""
    data_home = tmp_path / "data"
    home = tmp_path / "home"
    original_home = os.environ.get("HOME", "")
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    try:
        suite_ledger._ORIGINAL_HOME = original_home
    except (AttributeError, TypeError):
        pass


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stripped stdout."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """Create a temp git repo with one passing test and .ilk-launch.json."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    launch = {
        "ship": {
            "suite": {
                "command": "python3 -m pytest",
                "flags": ["-q", "-p", "no:cacheprovider"],
            }
        }
    }
    (repo / ".ilk-launch.json").write_text(
        json.dumps(launch, indent=2) + "\n", encoding="utf-8"
    )

    (repo / "test_foo.py").write_text(
        "def test_foo(): assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base: green")
    return repo


def _write_verification_record(repo: Path, tree: str, *,
                                failing_nodes: list[str],
                                base_tree: str,
                                base_failing: list[str],
                                owners: dict[str, dict],
                                batch: str = "test-batch",
                                head_source: str = "ledger",
                                base_source: str = "ledger") -> None:
    """Write a minimal verification record at the expected external path."""
    from ilk_paths import external_logs_dir, resolve_project_key

    key = resolve_project_key(repo)
    verification_dir = external_logs_dir(key) / "verification"
    verification_dir.mkdir(parents=True, exist_ok=True)

    at_base_rows = []
    for node in failing_nodes:
        at_base_rows.append(f"| {node} | absent-at-base | no |")

    at_base_block = "\n".join(at_base_rows) if at_base_rows else ""

    owners_lines = []
    for node, info in owners.items():
        slug = info.get("slug", "—")
        sha = info.get("sha", "—")
        how = info.get("how", "unknown")
        sha_short = sha[:12] if sha and sha != "—" else "—"
        owners_lines.append(f"| {node} | {slug} | {sha_short} | {how} |")

    owners_block = ""
    if owners_lines:
        owners_block = (
            "\n## Owners\n\n"
            "| node id | owner | commit | how |\n"
            "|---|---|---|---|\n"
            + "\n".join(owners_lines) + "\n"
        )

    record_content = textwrap.dedent(f"""\
        # Verification record

        - batch: {batch}
        - head: {tree}
        - base: {base_tree}
        - head_source: {head_source}
        - base_source: {base_source}
        - record_writer: verify_attribution

        ## At-base rerun

        | node id | at base | in baseline_red |
        |---|---|---|
        {at_base_block}
    """) + owners_block

    record_path = verification_dir / f"{batch}-batch.md"
    record_path.write_text(record_content, encoding="utf-8")


def _make_plans_dir(tmp_path: Path, slug: str, *,
                    status: str = "shipped",
                    estimated_steps: int = 2,
                    current_step: int | None = None) -> Path:
    """Create a fake plans dir with one sub-plan."""
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir(exist_ok=True)

    stem = f"2026-10-03-{slug}.md"
    cs = current_step if current_step is not None else estimated_steps
    plan_content = textwrap.dedent(f"""\
        ---
        plan: {slug}
        status: {status}
        current_step: {cs}
        estimated_steps: {estimated_steps}
        verification_tier: loop-verified
        local_checks: []
        ---

        # {slug}

        ## Steps

        ### Step 0 — first step

        ```yaml
        local_checks:
          - command: "true"
            timeout: 30
        ```

        ### Step 1 — second step

        ```yaml
        local_checks:
          - command: "true"
            timeout: 30
        ```

        ## Findings
    """)
    (plans_dir / stem).write_text(plan_content, encoding="utf-8")

    master_content = textwrap.dedent(f"""\
        ---
        master_plan: 2026-10-03-test-execution
        batch_date: 2026-10-03
        status: active
        ---

        # MASTER

        ## Sub-plan registry

        | # | Slug |
        |---|---|
        | 1 | [{stem}](./{stem}) |
    """)
    (plans_dir / f"MASTER-2026-10-03-test-execution-plan.md").write_text(
        master_content, encoding="utf-8"
    )
    return plans_dir


def _read_plan_status(plans_dir: Path, slug: str) -> dict:
    """Read the frontmatter of a sub-plan by slug."""
    for p in plans_dir.iterdir():
        if p.suffix == ".md" and slug in p.name:
            text = p.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm = {}
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        fm[k.strip()] = v.strip()
                return fm
    return {}


# ── AC-1: return_reds exits 0, owner reopened ──────────────────────────────


class TestAC1ReturnRedsExitsZero:
    """AC-1: a record with attributed id T owned by ``ours-y``: ``return-reds``
    exits 0; ``ours-y`` reads ``status: in-progress``, ``current_step: 1``;
    local_checks includes a command containing T; ``#### Returned red`` block
    sits after ``## Findings`` heading and names T."""

    def test_return_reds_opens_owner(self, tmp_path: Path) -> None:
        """Red-first: return-reds opens the owner and adds the check."""
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")
        base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): break test")
        head_sha = _git(repo, "rev-parse", "HEAD")
        head_tree = _git(repo, "rev-parse", f"{head_sha}^{{tree}}")

        plans_dir = _make_plans_dir(tmp_path, "ours-y",
                                     status="shipped",
                                     estimated_steps=2)

        _write_verification_record(
            repo, head_tree,
            failing_nodes=["test_foo.py::test_foo"],
            base_tree=base_tree,
            base_failing=[],
            owners={"test_foo.py::test_foo": {
                "slug": "ours-y", "sha": head_sha, "how": "point"}},
        )

        rc = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc == 0

        fm = _read_plan_status(plans_dir, "ours-y")
        assert fm.get("status") == "in-progress"
        assert fm.get("current_step") == "1"

        plan_path = list(plans_dir.glob("*ours-y*"))[0]
        body = plan_path.read_text(encoding="utf-8")
        assert "#### Returned red" in body
        assert "test_foo.py::test_foo" in body

        from run_local_checks import extract_step_local_checks
        checks = extract_step_local_checks(body, 1)
        assert any("test_foo.py::test_foo" in c.get("command", "")
                    for c in checks)


# ── AC-2: owner "—" ⇒ exit 3 ──────────────────────────────────────────────


class TestAC2OwnerDashExitsThree:
    """AC-2: an attributed id with owner ``—`` ⇒ exit 3 and every plan file
    is byte-identical."""

    def test_owner_dash_exits_three(self, tmp_path: Path) -> None:
        """Red-first: no owner ⇒ exit 3, plans unchanged."""
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")
        base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): break test")
        head_sha = _git(repo, "rev-parse", "HEAD")
        head_tree = _git(repo, "rev-parse", f"{head_sha}^{{tree}}")

        plans_dir = _make_plans_dir(tmp_path, "ours-y")

        _write_verification_record(
            repo, head_tree,
            failing_nodes=["test_foo.py::test_foo"],
            base_tree=base_tree,
            base_failing=[],
            owners={"test_foo.py::test_foo": {
                "slug": "—", "sha": "", "how": "unknown"}},
        )

        before = {p.name: p.read_bytes() for p in plans_dir.iterdir()
                  if p.suffix == ".md"}

        rc = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc == 3

        for name, content in before.items():
            assert (plans_dir / name).read_bytes() == content


# ── AC-3: returning same id twice ⇒ exit 4 ────────────────────────────────


class TestAC3DuplicateReturnExitsFour:
    """AC-3: returning T to ``ours-y`` a second time ⇒ exit 4, plan files
    byte-identical."""

    def test_duplicate_return_exits_four(self, tmp_path: Path) -> None:
        """Red-first: second return of same id ⇒ exit 4."""
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")
        base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): break test")
        head_sha = _git(repo, "rev-parse", "HEAD")
        head_tree = _git(repo, "rev-parse", f"{head_sha}^{{tree}}")

        plans_dir = _make_plans_dir(tmp_path, "ours-y",
                                     status="shipped",
                                     estimated_steps=2)

        _write_verification_record(
            repo, head_tree,
            failing_nodes=["test_foo.py::test_foo"],
            base_tree=base_tree,
            base_failing=[],
            owners={"test_foo.py::test_foo": {
                "slug": "ours-y", "sha": head_sha, "how": "point"}},
        )

        rc1 = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc1 == 0

        before = {p.name: p.read_bytes() for p in plans_dir.iterdir()
                  if p.suffix == ".md"}

        rc2 = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc2 == 4

        for name, content in before.items():
            assert (plans_dir / name).read_bytes() == content


# ── AC-4: worker session ⇒ exit 1 ─────────────────────────────────────────


class TestAC4WorkerSessionExitsOne:
    """AC-4: with ``ILK_WORKER_SESSION=1`` ⇒ exit 1, plan files
    byte-identical."""

    def test_worker_session_exits_one(self, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """Red-first: worker session refuses ⇒ exit 1."""
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")
        base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): break test")
        head_sha = _git(repo, "rev-parse", "HEAD")
        head_tree = _git(repo, "rev-parse", f"{head_sha}^{{tree}}")

        plans_dir = _make_plans_dir(tmp_path, "ours-y",
                                     status="shipped",
                                     estimated_steps=2)

        _write_verification_record(
            repo, head_tree,
            failing_nodes=["test_foo.py::test_foo"],
            base_tree=base_tree,
            base_failing=[],
            owners={"test_foo.py::test_foo": {
                "slug": "ours-y", "sha": head_sha, "how": "point"}},
        )

        before = {p.name: p.read_bytes() for p in plans_dir.iterdir()
                  if p.suffix == ".md"}

        monkeypatch.setenv("ILK_WORKER_SESSION", "1")

        rc = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc == 1

        for name, content in before.items():
            assert (plans_dir / name).read_bytes() == content


# ── AC-5: runner integration — ledger_return_reds calls suite_ledger ──────


class TestAC5RunnerLedgerReturnReds:
    """AC-5: ``ledger_return_reds`` in the runner calls
    ``suite_ledger.py return-reds`` and returns its exit code."""

    def test_ledger_return_reds_exit_zero(self, tmp_path: Path) -> None:
        """Red-first: stub exits 0 ⇒ ledger_return_reds returns 0."""
        driver = SCRIPTS_DIR / "run_ilk_loop_claude.sh"
        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(
            ["git", "init", "-q"],
            cwd=project, check=True, capture_output=True, text=True,
        )
        (project / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A"],
            cwd=project, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "init"],
            cwd=project, check=True, capture_output=True, text=True,
        )

        # Stub at <_SKILL_ROOT>/ilk-loop/scripts/suite_ledger.py.
        stub_ledger = tmp_path / "ilk-loop" / "scripts" / "suite_ledger.py"
        stub_ledger.parent.mkdir(parents=True)
        stub_ledger.write_text(textwrap.dedent("""\
            import sys
            if len(sys.argv) > 1 and sys.argv[1] == "return-reds":
                sys.exit(0)
            sys.exit(1)
        """), encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub_claude = bin_dir / "claude"
        stub_claude.write_text("#!/usr/bin/env bash\nexit 0\n",
                                encoding="utf-8")
        stub_claude.chmod(0o755)

        # Create a minimal plans dir.
        data_home = tmp_path / "data"
        from ilk_paths import resolve_project_key
        with _scoped_ilk_data_home(data_home):
            key = resolve_project_key(project)
        plans_dir = data_home / "projects" / key / "plans"
        plans_dir.mkdir(parents=True)
        stem = "2026-10-03-test-slug.md"
        (plans_dir / stem).write_text(textwrap.dedent(f"""\
            ---
            plan: test-slug
            status: in-progress
            current_step: 0
            estimated_steps: 2
            ---
            # test-slug
            ## Steps
            ### Step 0 — first step
            ```yaml
            local_checks:
              - command: "true"
                timeout: 30
            ```
        """), encoding="utf-8")
        (plans_dir / "MASTER-test.md").write_text(
            "---\nmaster_plan: test\nstatus: active\n---\n\n# M\n\n"
            "## Sub-plan registry\n\n| # | Slug |\n|---|---|\n"
            f"| 1 | [{stem}](./{stem}) |\n",
            encoding="utf-8",
        )

        script = f"""
export ILK_DOTSOURCE_ONLY=1
source {str(driver)!r} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
export PROJECT_PATH={str(project)!r}
# Point _SKILL_ROOT to our stub directory.
_SKILL_ROOT={str(tmp_path)!r}
_gate_first_plans_dir() {{
    printf '%s\\n' {str(plans_dir)!r}
}}
_verify_gate_batch_name() {{
    echo "test-batch"
}}
selfmod_effective_repo() {{
    echo {str(project)!r}
}}
GATE_FIRST_NO_DISPATCH=0
ledger_return_reds "test-slug" 0
RC=$?
echo "RC=$RC"
"""
        env = {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "ILK_DATA_HOME": str(data_home),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30, env=env,
            cwd=str(project),
        )
        # The function calls suite_ledger.py which lives at _SKILL_ROOT.
        # With the stub at tmp_path, it should exit 0.
        assert result.returncode == 0, (
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

    def test_ledger_return_reds_exit_three(self, tmp_path: Path) -> None:
        """Red-first: stub exits 3 ⇒ ledger_return_reds returns 3."""
        driver = SCRIPTS_DIR / "run_ilk_loop_claude.sh"
        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(
            ["git", "init", "-q"],
            cwd=project, check=True, capture_output=True, text=True,
        )
        (project / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A"],
            cwd=project, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "init"],
            cwd=project, check=True, capture_output=True, text=True,
        )

        # Stub that exits 3 for return-reds.
        # Must be at <_SKILL_ROOT>/ilk-loop/scripts/suite_ledger.py.
        stub_ledger = tmp_path / "ilk-loop" / "scripts" / "suite_ledger.py"
        stub_ledger.parent.mkdir(parents=True)
        stub_ledger.write_text(textwrap.dedent("""\
            import sys
            if len(sys.argv) > 1 and sys.argv[1] == "return-reds":
                sys.exit(3)
            sys.exit(1)
        """), encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub_claude = bin_dir / "claude"
        stub_claude.write_text("#!/usr/bin/env bash\nexit 0\n",
                                encoding="utf-8")
        stub_claude.chmod(0o755)

        script = f"""
export ILK_DOTSOURCE_ONLY=1
source {str(driver)!r} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
export PROJECT_PATH={str(project)!r}
# Point _SKILL_ROOT to our stub directory.
_SKILL_ROOT={str(tmp_path)!r}
_gate_first_plans_dir() {{
    echo "/nonexistent"
}}
_verify_gate_batch_name() {{
    echo "test-batch"
}}
selfmod_effective_repo() {{
    echo {str(project)!r}
}}
RC=0
ledger_return_reds "test-slug" 0 || RC=$?
echo "RC=$RC"
"""
        env = {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "ILK_DATA_HOME": str(tmp_path / "data"),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30, env=env,
            cwd=str(project),
        )
        assert "RC=3" in result.stdout, (
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )


class _scoped_ilk_data_home:
    """Pin ILK_DATA_HOME for a block."""
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


# ── AC-6: static check — ledger_return_reds placement ──────────────────────


class TestAC6RunnerStaticPlacement:
    """AC-6 (runner, static): ``ledger_return_reds`` is called inside
    ``attempt_gate_first_fast_path`` between ``if ! gate_first_results_are_green``
    and that branch's ``return 1``."""

    def test_ledger_return_reds_called_in_red_branch(self) -> None:
        """Red-first: the function is called in the right place."""
        driver = SCRIPTS_DIR / "run_ilk_loop_claude.sh"
        text = driver.read_text(encoding="utf-8")

        fn_match = re.search(
            r"^(attempt_gate_first_fast_path\(\))", text, re.MULTILINE,
        )
        assert fn_match is not None, (
            "attempt_gate_first_fast_path not found"
        )
        fn_start = fn_match.start()

        red_match = re.search(
            r"if ! gate_first_results_are_green", text[fn_start:],
        )
        assert red_match is not None, (
            "red branch (if ! gate_first_results_are_green) not found"
        )
        red_start = fn_start + red_match.start()

        return_match = re.search(r"return 1", text[red_start:])
        assert return_match is not None, (
            "return 1 after red branch not found"
        )
        red_end = red_start + return_match.end()

        red_region = text[red_start:red_end]

        assert "ledger_return_reds" in red_region, (
            "ledger_return_reds not called between "
            "'if ! gate_first_results_are_green' and 'return 1'"
        )

        call_pos = red_region.index("ledger_return_reds")
        ret_pos = red_region.index("return 1")
        assert call_pos < ret_pos, (
            "ledger_return_reds must be called before 'return 1'"
        )