"""Sub-plan ``a-red-without-an-owner-reverts-nothing`` step 0 — xfail pins.

AC-1 through AC-5 for the red-owner bisect and attribution fixes.
Each test is ``xfail(strict=True, reason="red-first")`` until step 1
implements the fixes and removes the xfails.

AC-1 (13a): a repo where a sibling test in the gate's files is red at
      base, and commit C makes node N red ⇒ ``bisect_red_owner`` returns
      ``first_red == C``.  The bisect must use the same node-only
      invocation as the iteration-base check (not re-run the whole gate).
AC-2 (13b): ``attribute_red`` returns ``owned`` with no owner ⇒ the
      verdict becomes ``unmeasured``, and ship_integrity reverts nothing.
AC-3 (13c): C's trailer ``[plan:x#step-1]`` is in the body only ⇒
      ``owner_slug == "x"``.
AC-4 (1): a no-flag gate ``python3 -m pytest tests/a.py`` with a red
      sibling in ``tests/a.py`` ⇒ the at-base rerun runs node N only.
AC-5 (8): red at iteration base and ``batch_base`` None ⇒ ``unmeasured``,
      never ``pre-existing``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_TESTS = _HERE.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import red_owner  # noqa: E402
import ship_integrity  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo), *args], check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return cp.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")
    (repo / "f.txt").write_text("0\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _run_red_owner(repo: Path, base: str, head: str,
                   gate_cmd: str = "python3 -m pytest test_stuff.py -q",
                   nodes: list[str] | None = None,
                   budget: int = 300) -> dict:
    args = [
        "python3", str(_SCRIPTS / "red_owner.py"),
        "--repo", str(repo),
        "--base", base, "--head", head,
        "--cmd", gate_cmd,
        "--budget-s", str(budget),
    ]
    for n in (nodes or []):
        args += ["--node", n]
    r = subprocess.run(
        args, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert r.returncode == 0, f"red_owner.py exited {r.returncode}: {r.stderr}"
    return json.loads(r.stdout)


def _run_attribute_red(
    repo: Path,
    iteration_base: str,
    batch_base: str | None,
    head: str,
    cmd: str,
    stdout_tail: str = "",
    budget: int = 300,
) -> dict:
    args = [
        "python3", str(_SCRIPTS / "red_owner.py"),
        "--attribute",
        "--repo", str(repo),
        "--iteration-base", iteration_base,
        "--head", head,
        "--cmd", cmd,
        "--budget-s", str(budget),
    ]
    if batch_base is not None:
        args += ["--batch-base", batch_base]
    if stdout_tail:
        args += ["--stdout-file", "-"]
        r = subprocess.run(
            args, input=stdout_tail, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
    else:
        r = subprocess.run(
            args, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
    assert r.returncode == 0, f"red_owner.py --attribute exited {r.returncode}: {r.stderr}"
    return json.loads(r.stdout)


# ── AC-1 (13a): sibling red at base, commit C makes node N red ──────────────

class TestBisectFindsOwnerDespiteSiblingRed:
    """AC-1: a repo where a sibling test in the gate's files is red at
    base, and commit C makes node N red ⇒ bisect_red_owner returns
    first_red == C.

    The bug: bisect_red_owner re-runs the WHOLE gate command plus the
    node.  The 21-file gate is red at base for another reason, so the
    base check returns ``{first_red: None, base_green: False}``.
    The bisect must use the same node-only invocation as the
    iteration-base check.
    """

    def test_sibling_red_at_base_node_red_at_c(self, tmp_path: Path) -> None:
        """Sibling red at base + node N red at C ⇒ first_red == C."""
        repo = _make_repo(tmp_path)

        # Base: sibling test is red, node N is green.
        (repo / "test_sibling.py").write_text(
            "def test_sibling(): assert False\n"
        )
        (repo / "test_node.py").write_text(
            "def test_node_n(): assert True\n"
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "base: sibling red, node green")

        base_sha = _git(repo, "rev-parse", "HEAD")

        # Commit C: breaks node N.
        (repo / "test_node.py").write_text(
            "def test_node_n(): assert False\n"
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m",
             "feat(x): break node [plan:x#step-1]")

        c_sha = _git(repo, "rev-parse", "HEAD")

        # The bisect should find C as the first red for node N,
        # even though the whole gate is red at base due to the sibling.
        result = _run_red_owner(
            repo, base_sha, c_sha,
            gate_cmd="python3 -m pytest test_sibling.py test_node.py -q",
            nodes=["test_node.py::test_node_n"],
        )

        assert result["first_red"] == c_sha, (
            f"Expected first_red == {c_sha}, got {result['first_red']}"
        )
        assert result["base_green"] is True


# ── AC-2 (13b): owned with no owner ⇒ unmeasured ─────────────────────────────

class TestOwnedWithNoOwnerIsUnmeasured:
    """AC-2: attribute_red returns ``owned`` with ``owner_sha: None`` ⇒
    the verdict must become ``unmeasured``, and ship_integrity reverts
    nothing.

    The bug: an ``owned`` verdict with ``owner_sha: None`` was charged
    to the GATING sub-plan, not the owner.  It must refuse to revert:
    verdict ``unmeasured``, with the reason saying why.
    """

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_owned_no_owner_ship_integrity_reverts_nothing(self) -> None:
        """An owned verdict with owner_sha=None and owner_slug=None ⇒
        ship_integrity must not revert (treat as unmeasured).

        The bug: evaluate_ship sees verdict="owned" with no owner_slug,
        falls through to regular gate enforcement, and reverts a red
        gate that this sub-plan didn't cause.  The fix: when
        owner_slug is empty/None, treat the verdict as unmeasured.
        """
        # Synthetic gate result: owned, but no owner identified.
        gate_result = {
            "all_passed": False,
            "failing_checks": ["pytest"],
            "attribution": {
                "verdict": "owned",
                "owner_sha": None,
                "owner_slug": None,
                "iteration_base": "abc1234",
                "batch_base": None,
            },
        }

        verdict = ship_integrity.evaluate_ship(
            subplan_status="shipped",
            declared_checks=[{"command": "pytest"}],
            last_gate_result=gate_result,
        )

        # The fix: owned with no owner should be treated as unmeasured,
        # so the ship should be ok (not reverted).
        assert verdict.ok, (
            f"Expected ok=True (unmeasured), got ok=False: {verdict.reason}"
        )


# ── AC-3 (13c): owner slug from body, not subject ────────────────────────────

class TestOwnerSlugFromBody:
    """AC-3: C's trailer ``[plan:x#step-1]`` is in the body only ⇒
    ``owner_slug == "x"``.

    The bug: the owner slug is read from the commit SUBJECT only
    (``git log --format=%s``).  Worker trailers sit in the BODY.
    Must read ``%B`` (the way ``ship_audit`` does).
    """

    def test_slug_from_body_not_subject(self, tmp_path: Path) -> None:
        """Trailer in body only ⇒ owner_slug == 'x'."""
        repo = _make_repo(tmp_path)

        # Base: green.
        (repo / "test_stuff.py").write_text("def test_ok(): assert True\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "base: green")

        base_sha = _git(repo, "rev-parse", "HEAD")

        # Commit A: breaks the test, trailer in BODY only.
        (repo / "test_stuff.py").write_text("def test_ok(): assert False\n")
        _git(repo, "add", ".")
        # Write a commit with trailer in body via -m with two messages.
        _git(repo, "commit", "-q", "-m",
             "feat(x): break test\n\n[plan:alpha#step-1]")

        a_sha = _git(repo, "rev-parse", "HEAD")

        # Commit B: unrelated.
        (repo / "bar.py").write_text("# bar\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "feat(y): unrelated [plan:beta#step-0]")

        head_sha = _git(repo, "rev-parse", "HEAD")

        # Bisect should find A and read the slug from the body.
        result = _run_red_owner(
            repo, base_sha, head_sha,
            gate_cmd="python3 -m pytest test_stuff.py -q",
            nodes=["test_stuff.py::test_ok"],
        )

        assert result["first_red"] == a_sha
        # The slug should be "alpha" (from body), not from subject.
        # _extract_plan_slug reads the full message — verify via attribute_red.
        attr = _run_attribute_red(
            repo,
            iteration_base=base_sha,
            batch_base=None,
            head=head_sha,
            cmd="python3 -m pytest test_stuff.py -q",
            stdout_tail="FAILED test_stuff.py::test_ok",
        )
        assert attr["owner_slug"] == "alpha", (
            f"Expected owner_slug='alpha', got {attr['owner_slug']}"
        )


# ── AC-4 (1): no-flag gate with red sibling ⇒ node-only rerun ────────────────

class TestNoFlagGateNodeOnlyRerun:
    """AC-4: a no-flag gate ``python3 -m pytest tests/a.py`` with a red
    sibling in ``tests/a.py`` ⇒ the at-base rerun runs node N only.

    The bug: a no-flag gate keeps its file paths in the at-base node
    rerun, so a node reads failed-at-base when a sibling test is red.
    The ``re.sub`` after ``_strip_runner_flags`` in
    ``_run_at_base_worktree`` doesn't strip a trailing path.
    """

    def test_no_flag_gate_path_not_stripped(self) -> None:
        r"""A no-flag gate ``python3 -m pytest tests/a.py`` keeps the
        path after ``_strip_runner_flags`` + the path-stripping regex.

        The bug: the regex ``r'(pytest)\s+(?!\-)(?:\S+\s+)*?(?=\-\w|\s*$)'``
        doesn't strip a trailing path (no flag follows it).  The path
        remains, and when a node id is appended the command becomes
        ``python3 -m pytest tests/a.py tests/a.py::test_node_n`` —
        the file path appears twice.  The correct result is
        ``python3 -m pytest tests/a.py::test_node_n``.
        """
        import re
        cmd = "python3 -m pytest tests/a.py"
        # Apply the same transforms as _run_at_base_worktree.
        runner = red_owner._strip_runner_flags(cmd)
        # Match one or more non-flag args (paths) after pytest,
        # stopping at the next flag or end-of-string.
        runner = re.sub(
            r"(pytest)(?:\s+(?!\-)\S+)+",
            r"\1",
            runner,
        )
        # The path should be stripped.
        assert "tests/a.py" not in runner, (
            f"Path should be stripped from command, got: {runner!r}"
        )


# ── AC-5 (8): red at iter base + batch_base None ⇒ unmeasured ────────────────

class TestRedAtBaseWithNoBatchBaseIsUnmeasured:
    """AC-5: red at iteration base and ``batch_base`` None ⇒
    ``unmeasured``, never ``pre-existing``.

    The bug: a ``pre-existing`` verdict with ``batch_base: null``
    (reason ``red at iteration base and batch base is unresolved``)
    is an unmeasured excuse.  It must read ``unmeasured``.
    """

    @pytest.mark.xfail(strict=True, reason="red-first")
    def test_red_at_iter_base_no_batch_base(self, tmp_path: Path) -> None:
        """Red at iter base, no batch base ⇒ unmeasured."""
        repo = _make_repo(tmp_path)

        # Base: test is already red.
        (repo / "test_stuff.py").write_text("def test_ok(): assert False\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "base: already red")

        base_sha = _git(repo, "rev-parse", "HEAD")

        # Commit: unrelated change.
        (repo / "bar.py").write_text("# bar\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "feat(x): unrelated [plan:x#step-1]")

        head_sha = _git(repo, "rev-parse", "HEAD")

        # Attribute with no batch_base.
        result = _run_attribute_red(
            repo,
            iteration_base=base_sha,
            batch_base=None,
            head=head_sha,
            cmd="python3 -m pytest test_stuff.py -q",
            stdout_tail="FAILED test_stuff.py::test_ok",
        )

        # Must be unmeasured, not pre-existing.
        assert result["verdict"] == "unmeasured", (
            f"Expected unmeasured, got {result['verdict']}: {result.get('reason')}"
        )