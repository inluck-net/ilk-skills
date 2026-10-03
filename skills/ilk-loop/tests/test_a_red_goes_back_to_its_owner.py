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
AC-5 (runner): with a stub ``suite_ledger.py`` exiting 0 for ``return-reds``,
      a red gate-first verify step leaves ``GATE_FIRST_NO_DISPATCH=1``; with
      the stub exiting 3 it stays 0.  Red-first.
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
                                head_source: str = "ledger",
                                base_source: str = "ledger") -> None:
    """Write a minimal verification_record with an ``## Owners`` section."""
    import verification_record as vr

    owners_lines = []
    for node, info in owners.items():
        slug = info.get("slug", "—")
        sha = info.get("sha", "")
        how = info.get("how", "unknown")
        owners_lines.append(f"| {node} | {slug} | {sha} | {how} |")

    owners_block = ""
    if owners_lines:
        owners_block = (
            "\n## Owners\n\n"
            "| Id | Owner | Sha | How |\n"
            "|---|---|---|---|\n"
            + "\n".join(owners_lines) + "\n"
        )

    # Minimal record content: just enough for resolve_batch_record +
    # derive_attributed to work.
    record_content = textwrap.dedent(f"""\
        # Verification record

        - batch: test-batch
        - head: {tree}
        - base: {base_tree}
        - head_source: {head_source}
        - base_source: {base_source}

        ## At-base

        | Id | At base | Green count | Flaky |
        |---|---|---|---|
    """)
    for node in failing_nodes:
        # Nodes that are in the failing set at HEAD.
        record_content += f"| {node} | failed | — | — |\n"

    record_content += owners_block

    # Write to the expected location.
    from ilk_paths import external_logs_dir, resolve_project_key

    key = resolve_project_key(repo)
    vr_dir = external_logs_dir(key) / "verification"
    vr_dir.mkdir(parents=True, exist_ok=True)
    record_path = vr_dir / f"record-{tree[:12]}.md"
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

    # Minimal master.
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
            # Extract frontmatter between --- markers.
            parts = text.split("---", 2)
            if len(parts) >= 3:
                # Parse simple key: value pairs.
                fm = {}
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        fm[k.strip()] = v.strip()
                return fm
    return {}


# ── AC-1: return_reds exits 0, owner reopened ──────────────────────────────


@pytest.mark.xfail(strict=True, reason="return_reds not yet implemented")
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

        # Break the test to make it red at HEAD.
        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): break test")
        head_sha = _git(repo, "rev-parse", "HEAD")
        head_tree = _git(repo, "rev-parse", f"{head_sha}^{{tree}}")

        # Plans dir with ours-y shipped.
        plans_dir = _make_plans_dir(tmp_path, "ours-y",
                                     status="shipped",
                                     estimated_steps=2)

        # Write a verification record with T attributed, owned by ours-y.
        _write_verification_record(
            repo, head_tree,
            failing_nodes=["test_foo.py::test_foo"],
            base_tree=base_tree,
            base_failing=[],
            owners={"test_foo.py::test_foo": {
                "slug": "ours-y", "sha": head_sha, "how": "point"}},
        )

        # Call return_reds.
        rc = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc == 0

        # ours-y is now in-progress at last step.
        fm = _read_plan_status(plans_dir, "ours-y")
        assert fm.get("status") == "in-progress"
        assert fm.get("current_step") == "1"  # estimated_steps - 1

        # The plan body contains a Returned red block naming T.
        plan_path = list(plans_dir.glob("*ours-y*"))[0]
        body = plan_path.read_text(encoding="utf-8")
        assert "#### Returned red" in body
        assert "test_foo.py::test_foo" in body

        # local_checks for step 1 now includes a command with T.
        from run_local_checks import extract_step_local_checks
        checks = extract_step_local_checks(body, 1)
        assert any("test_foo.py::test_foo" in c.get("command", "")
                    for c in checks)


# ── AC-2: owner "—" ⇒ exit 3 ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="return_reds not yet implemented")
class TestAC2OwnerDashExitsThree:
    """AC-2: an attributed id with owner ``—`` ⇒ exit 3 and every plan file
    is byte-identical."""

    def test_owner_dash_exits_three(self, tmp_path: Path) -> None:
        """Red-first: no owner ⇒ exit 3, plans unchanged."""
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")
        base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

        # Break the test.
        (repo / "test_foo.py").write_text(
            "def test_foo(): assert False\n", encoding="utf-8"
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat(x): break test")
        head_sha = _git(repo, "rev-parse", "HEAD")
        head_tree = _git(repo, "rev-parse", f"{head_sha}^{{tree}}")

        plans_dir = _make_plans_dir(tmp_path, "ours-y")

        # Record with owner "—" (no owner).
        _write_verification_record(
            repo, head_tree,
            failing_nodes=["test_foo.py::test_foo"],
            base_tree=base_tree,
            base_failing=[],
            owners={"test_foo.py::test_foo": {
                "slug": "—", "sha": "", "how": "unknown"}},
        )

        # Snapshot plan files before.
        before = {p.name: p.read_bytes() for p in plans_dir.iterdir()
                  if p.suffix == ".md"}

        rc = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc == 3

        # Every plan file is byte-identical.
        for name, content in before.items():
            assert (plans_dir / name).read_bytes() == content


# ── AC-3: returning same id twice ⇒ exit 4 ────────────────────────────────


@pytest.mark.xfail(strict=True, reason="return_reds not yet implemented")
class TestAC3DuplicateReturnExitsFour:
    """AC-3: returning T to ``ours-y`` a second time ⇒ exit 4, plan files
    byte-identical."""

    def test_duplicate_return_exits_four(self, tmp_path: Path) -> None:
        """Red-first: second return of same id ⇒ exit 4."""
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")
        base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

        # Break the test.
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

        # First return succeeds.
        rc1 = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc1 == 0

        # Snapshot plan files after first return.
        before = {p.name: p.read_bytes() for p in plans_dir.iterdir()
                  if p.suffix == ".md"}

        # Second return ⇒ exit 4.
        rc2 = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc2 == 4

        # Plan files are byte-identical to after first return.
        for name, content in before.items():
            assert (plans_dir / name).read_bytes() == content


# ── AC-4: worker session ⇒ exit 1 ─────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="return_reds not yet implemented")
class TestAC4WorkerSessionExitsOne:
    """AC-4: with ``ILK_WORKER_SESSION=1`` ⇒ exit 1, plan files
    byte-identical."""

    def test_worker_session_exits_one(self, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """Red-first: worker session refuses ⇒ exit 1."""
        repo = _make_repo(tmp_path)
        base_sha = _git(repo, "rev-parse", "HEAD")
        base_tree = _git(repo, "rev-parse", f"{base_sha}^{{tree}}")

        # Break the test.
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

        # Snapshot plan files before.
        before = {p.name: p.read_bytes() for p in plans_dir.iterdir()
                  if p.suffix == ".md"}

        # Set worker session.
        monkeypatch.setenv("ILK_WORKER_SESSION", "1")

        rc = suite_ledger.return_reds(
            repo, batch="test-batch", plans_dir=plans_dir,
        )
        assert rc == 1

        # Plan files are byte-identical.
        for name, content in before.items():
            assert (plans_dir / name).read_bytes() == content


# ── AC-5: runner integration — stub exits 0/3 ──────────────────────────────


@pytest.mark.xfail(strict=True, reason="runner integration not yet implemented")
class TestAC5RunnerReturnRedsStub:
    """AC-5: with a stub ``suite_ledger.py`` exiting 0 for ``return-reds``,
    a red gate-first verify step leaves ``GATE_FIRST_NO_DISPATCH=1``; with
    the stub exiting 3 it stays 0."""

    def test_stub_exit_zero_sets_no_dispatch(self, tmp_path: Path) -> None:
        """Red-first: stub exits 0 ⇒ GATE_FIRST_NO_DISPATCH=1."""
        # This test exercises the runner's integration with return_reds.
        # It sources run_ilk_loop_claude.sh under ILK_DOTSOURCE_ONLY=1
        # with a stub suite_ledger.py that exits 0 for return-reds.
        #
        # The harness is the same as test_gate_first_step.py.
        driver = SCRIPTS_DIR.parent / "scripts" / "run_ilk_loop_claude.sh"
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

        # Build a stub suite_ledger.py that exits 0 for return-reds.
        stub_ledger = tmp_path / "suite_ledger_stub.py"
        stub_ledger.write_text(textwrap.dedent("""\
            import sys
            if len(sys.argv) > 1 and sys.argv[1] == "return-reds":
                sys.exit(0)
            sys.exit(1)
        """), encoding="utf-8")

        # Build a stub claude that does nothing.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub_claude = bin_dir / "claude"
        stub_claude.write_text("#!/usr/bin/env bash\nexit 0\n",
                                encoding="utf-8")
        stub_claude.chmod(0o755)

        # Source the driver and check that GATE_FIRST_NO_DISPATCH is set.
        script = f"""
export ILK_DOTSOURCE_ONLY=1
source {str(driver)} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
# Override suite_ledger to use our stub.
suite_ledger_py() {{
    python3 {str(stub_ledger)} "$@"
}}
export -f suite_ledger_py
# Call attempt_gate_first_fast_path with a red result.
# The function should call ledger_return_reds and set GATE_FIRST_NO_DISPATCH.
GATE_FIRST_NO_DISPATCH=0
attempt_gate_first_fast_path /dev/null
echo "NO_DISPATCH=$GATE_FIRST_NO_DISPATCH"
"""
        env = {
            **os.environ,
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(tmp_path / "data"),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30, env=env,
        )
        # With stub exiting 0, GATE_FIRST_NO_DISPATCH should be 1.
        assert "NO_DISPATCH=1" in result.stdout

    def test_stub_exit_three_keeps_dispatch(self, tmp_path: Path) -> None:
        """Red-first: stub exits 3 ⇒ GATE_FIRST_NO_DISPATCH=0."""
        driver = SCRIPTS_DIR.parent / "scripts" / "run_ilk_loop_claude.sh"
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

        stub_ledger = tmp_path / "suite_ledger_stub.py"
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
source {str(driver)} || exit 90
unset ILK_DOTSOURCE_ONLY
export ILK_RUN_LOCK_HELD=1
suite_ledger_py() {{
    python3 {str(stub_ledger)} "$@"
}}
export -f suite_ledger_py
GATE_FIRST_NO_DISPATCH=0
attempt_gate_first_fast_path /dev/null
echo "NO_DISPATCH=$GATE_FIRST_NO_DISPATCH"
"""
        env = {
            **os.environ,
            "HOME": str(tmp_path),
            "ILK_DATA_HOME": str(tmp_path / "data"),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30, env=env,
        )
        # With stub exiting 3, GATE_FIRST_NO_DISPATCH should stay 0.
        assert "NO_DISPATCH=0" in result.stdout


# ── AC-6: static check — ledger_return_reds placement ──────────────────────


@pytest.mark.xfail(strict=True, reason="runner integration not yet implemented")
class TestAC6RunnerStaticPlacement:
    """AC-6 (runner, static): ``ledger_return_reds`` is called inside
    ``attempt_gate_first_fast_path`` between ``if ! gate_first_results_are_green``
    and that branch's ``return 1``."""

    def test_ledger_return_reds_called_in_red_branch(self) -> None:
        """Red-first: the function is called in the right place."""
        driver = SCRIPTS_DIR.parent / "scripts" / "run_ilk_loop_claude.sh"
        text = driver.read_text(encoding="utf-8")

        # Find attempt_gate_first_fast_path.
        fn_match = re.search(
            r"^(attempt_gate_first_fast_path\(\))", text, re.MULTILINE,
        )
        assert fn_match is not None, (
            "attempt_gate_first_fast_path not found"
        )
        fn_start = fn_match.start()

        # Find the red branch: "if ! gate_first_results_are_green".
        red_match = re.search(
            r"if ! gate_first_results_are_green", text[fn_start:],
        )
        assert red_match is not None, (
            "red branch (if ! gate_first_results_are_green) not found"
        )
        red_start = fn_start + red_match.start()

        # Find the "return 1" after the red branch.
        return_match = re.search(r"return 1", text[red_start:])
        assert return_match is not None, (
            "return 1 after red branch not found"
        )
        red_end = red_start + return_match.end()

        # The red branch region.
        red_region = text[red_start:red_end]

        # ledger_return_reds must be called in this region.
        assert "ledger_return_reds" in red_region, (
            "ledger_return_reds not called between "
            "'if ! gate_first_results_are_green' and 'return 1'"
        )

        # It must come before the return 1.
        call_pos = red_region.index("ledger_return_reds")
        ret_pos = red_region.index("return 1")
        assert call_pos < ret_pos, (
            "ledger_return_reds must be called before 'return 1'"
        )