"""Red-first tests for trailer slug integrity (D2).

Pins three behaviours before the fix:

1. ``check_step_commits`` finds 0 commits for the correct slug when every
   trailer carries a typo (the refusal — must stay).
2. ``audit_ship``'s report **names** the near-miss slug actually present in
   the commit range (not yet implemented — this is the red pin).
3. A trailer naming a slug that matches no sub-plan file is detectable from
   the plans dir alone, with no fuzzy matching involved.

Fixture: a synthetic git repo under ``tmp_path`` reproducing the real shape
from gh-resolve — commits whose trailers carry ``<slug>`` with one character
removed, and a sub-plan file whose ``plan:`` is the correct spelling.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _init_repo(tmp: Path) -> Path:
    """Create a minimal git repo with one initial commit."""
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=repo, check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True,
    )
    return repo


def _commit_with_trailer(repo: Path, filename: str, content: str, trailer: str) -> str:
    """Create a commit with a plan trailer. Returns short SHA."""
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    msg = f"feat({filename}): change\n\n{trailer}"
    subprocess.run(
        ["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def _commit_with_subject_trailer(repo: Path, filename: str, content: str,
                                 trailer: str) -> str:
    """Create a commit whose trailer is in the SUBJECT, not the body.

    This is the dominant real-world placement: 225 of the last 300 commits
    in this repo carry ``[plan:…]`` in the subject, only 18 in a body line.
    ``_commit_with_trailer`` above uses the body form, which is why the
    near-miss diagnostic could regress without any test noticing.
    """
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    msg = f"feat({filename}): change {trailer}"
    subprocess.run(
        ["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    return result.stdout.strip()


def _make_subplan(plans_dir: Path, slug: str, steps: list[int]) -> Path:
    """Create a sub-plan file with the given slug and step headings."""
    step_headings = "\n".join(f"### Step {s}" for s in steps)
    subplan = plans_dir / f"2026-09-08-{slug}.md"
    subplan.write_text(
        f"---\n"
        f"plan: {slug}\n"
        f"status: shipped\n"
        f"current_step: {len(steps)}\n"
        f"---\n"
        f"{step_headings}\n"
    )
    return subplan


# ── test fixtures ────────────────────────────────────────────────────────────

CORRECT_SLUG = "a-collaborator-is-not-an-intruder"
# The real typo: one character removed ('in' → 'an')
MANGLED_SLUG = "a-collaborator-is-not-antruder"


@pytest.fixture
def typo_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with commits carrying the mangled slug, plus a sub-plan
    whose ``plan:`` field has the correct spelling."""
    repo = _init_repo(tmp_path)
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True)

    # Sub-plan with the correct slug
    _make_subplan(plans_dir, CORRECT_SLUG, [0, 1, 2, 3])

    # Commits with the MANGLED slug (the real-world typo)
    _commit_with_trailer(repo, "a.txt", "a\n",
                         f"[plan:{MANGLED_SLUG}#step-0]")
    _commit_with_trailer(repo, "b.txt", "b\n",
                         f"[plan:{MANGLED_SLUG}#step-1]")
    _commit_with_trailer(repo, "c.txt", "c\n",
                         f"[plan:{MANGLED_SLUG}#step-2]")
    _commit_with_trailer(repo, "d.txt", "d\n",
                         f"[plan:{MANGLED_SLUG}#step-3]")

    return repo, plans_dir


@pytest.fixture
def typo_repo_subject_trailers(tmp_path: Path) -> tuple[Path, Path]:
    """Same as ``typo_repo`` but the trailers live in the commit SUBJECT."""
    repo = _init_repo(tmp_path)
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True)

    _make_subplan(plans_dir, CORRECT_SLUG, [0, 1, 2, 3])

    for name, step in (("a.txt", 0), ("b.txt", 1), ("c.txt", 2), ("d.txt", 3)):
        _commit_with_subject_trailer(
            repo, name, f"{name[0]}\n", f"[plan:{MANGLED_SLUG}#step-{step}]",
        )

    return repo, plans_dir


@pytest.fixture
def clean_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with correct trailers (no typo). Used as a control."""
    repo = _init_repo(tmp_path)
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True)

    _make_subplan(plans_dir, CORRECT_SLUG, [0, 1, 2, 3])

    _commit_with_trailer(repo, "a.txt", "a\n",
                         f"[plan:{CORRECT_SLUG}#step-0]")
    _commit_with_trailer(repo, "b.txt", "b\n",
                         f"[plan:{CORRECT_SLUG}#step-1]")
    _commit_with_trailer(repo, "c.txt", "c\n",
                         f"[plan:{CORRECT_SLUG}#step-2]")
    _commit_with_trailer(repo, "d.txt", "d\n",
                         f"[plan:{CORRECT_SLUG}#step-3]")

    return repo, plans_dir


# ── Pin 1: the audit finds 0 commits for the correct slug ───────────────────

class TestPin1Refusal:
    """The correct slug must NOT match mangled trailers.

    This is today's behaviour (exact match) and must stay — it is the
    refusal that prevents forged work from auditing as proven.
    """

    def test_check_step_commits_finds_zero_for_correct_slug(
        self, typo_repo: tuple[Path, Path],
    ) -> None:
        repo, _ = typo_repo
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ship_audit import check_step_commits

        present, missing = check_step_commits(
            CORRECT_SLUG, [0, 1, 2, 3], cwd=repo,
        )
        assert present == [], (
            "check_step_commits must not find commits for the correct slug "
            "when every trailer carries the typo"
        )
        assert missing == [0, 1, 2, 3]

    def test_audit_ship_reports_unproven_for_typed_slug(
        self, typo_repo: tuple[Path, Path],
    ) -> None:
        """The full audit must refuse — proven=False."""
        repo, plans_dir = typo_repo
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ship_audit import audit_ship

        result = audit_ship(
            status="shipped",
            body="### Step 0\n### Step 1\n### Step 2\n### Step 3\n",
            declared_checks=[],
            gate_passed="unknown",
            slug=CORRECT_SLUG,
            cwd=repo,
        )
        assert result["proven"] is False, (
            "audit_ship must refuse when no trailers match the correct slug"
        )
        assert result["missing_steps"] == [0, 1, 2, 3]

    def test_correct_trailers_pass(self, clean_repo: tuple[Path, Path]) -> None:
        """Control: correct trailers must still pass (sanity check)."""
        repo, _ = clean_repo
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ship_audit import check_step_commits

        present, missing = check_step_commits(
            CORRECT_SLUG, [0, 1, 2, 3], cwd=repo,
        )
        assert present == [0, 1, 2, 3]
        assert missing == []


# ── Pin 2b: near-miss detection must see SUBJECT-line trailers ──────────────
#
# Regression added 2026-09-08.  ``_find_near_miss_slugs`` matched a 40-char
# SHA at the start of a line and then ``continue``d, discarding the rest of
# that line.  Under ``--format=%H %s%n%b`` the SUBJECT shares the SHA line,
# so every subject-placed trailer was invisible to the diagnostic — 225 of
# the last 300 commits in this repo.  The pre-existing fixtures all use the
# body form, so the whole suite stayed green over the blind spot.

class TestPin2bSubjectLineTrailers:
    def _helper(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ship_audit import _find_near_miss_slugs
        return _find_near_miss_slugs

    def test_subject_line_trailer_is_found(self) -> None:
        """A trailer on the SHA line must yield the near-miss slug and SHA."""
        find = self._helper()
        sha = "a" * 40
        git_output = f"{sha} feat: change [plan:{MANGLED_SLUG}#step-0]\n"
        assert find(CORRECT_SLUG, git_output) == [(MANGLED_SLUG, ["aaaaaaa"])]

    def test_body_line_trailer_still_found(self) -> None:
        """Control: the body placement must keep working."""
        find = self._helper()
        sha = "a" * 40
        git_output = f"{sha} feat: change\n\n[plan:{MANGLED_SLUG}#step-0]\n"
        assert find(CORRECT_SLUG, git_output) == [(MANGLED_SLUG, ["aaaaaaa"])]

    def test_target_slug_on_subject_line_is_not_a_near_miss(self) -> None:
        """The correct slug must never report itself as a near-miss."""
        find = self._helper()
        sha = "a" * 40
        git_output = f"{sha} feat: change [plan:{CORRECT_SLUG}#step-0]\n"
        assert find(CORRECT_SLUG, git_output) == []

    def test_audit_reasons_name_near_miss_from_subject_trailers(
        self, typo_repo_subject_trailers: tuple[Path, Path],
    ) -> None:
        """End-to-end: a repo whose typo trailers are all in subjects must
        still produce the actionable near-miss reason."""
        repo, _ = typo_repo_subject_trailers
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ship_audit import audit_ship

        result = audit_ship(
            status="shipped",
            body="### Step 0\n### Step 1\n### Step 2\n### Step 3\n",
            declared_checks=[],
            gate_passed="unknown",
            slug=CORRECT_SLUG,
            cwd=repo,
        )
        assert result["proven"] is False
        all_reasons = " ".join(result["reasons"])
        assert MANGLED_SLUG in all_reasons, (
            f"Subject-placed trailers must still surface the near-miss slug; "
            f"got: {result['reasons']}"
        )


# ── Pin 2: the report names near-miss slugs ─────────────────────────────────

class TestPin2NearMissReport:
    """When zero commits match, the report must name near-miss slugs.

    This is NOT yet implemented — these tests are the red pin.  The fix
    will add near-miss detection to ``audit_ship`` or a helper it calls.
    """

    def test_audit_reasons_mention_near_miss_slug(
        self, typo_repo: tuple[Path, Path],
    ) -> None:
        """The ``reasons`` list must include the mangled slug found in
        the commit range, so the operator knows what to fix."""
        repo, _ = typo_repo
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ship_audit import audit_ship

        result = audit_ship(
            status="shipped",
            body="### Step 0\n### Step 1\n### Step 2\n### Step 3\n",
            declared_checks=[],
            gate_passed="unknown",
            slug=CORRECT_SLUG,
            cwd=repo,
        )
        # The reasons should mention the mangled slug somewhere
        all_reasons = " ".join(result["reasons"])
        assert MANGLED_SLUG in all_reasons, (
            f"Expected near-miss slug '{MANGLED_SLUG}' in audit reasons, "
            f"got: {result['reasons']}"
        )

    def test_audit_reasons_include_commit_shas(
        self, typo_repo: tuple[Path, Path],
    ) -> None:
        """The near-miss report should include the SHAs of the commits
        carrying the mangled slug, so the operator can inspect them."""
        repo, _ = typo_repo
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from ship_audit import audit_ship

        result = audit_ship(
            status="shipped",
            body="### Step 0\n### Step 1\n### Step 2\n### Step 3\n",
            declared_checks=[],
            gate_passed="unknown",
            slug=CORRECT_SLUG,
            cwd=repo,
        )
        # At least one reason should contain a short SHA (7 hex chars)
        import re
        sha_pattern = re.compile(r"[0-9a-f]{7,}")
        all_reasons = " ".join(result["reasons"])
        assert sha_pattern.search(all_reasons), (
            f"Expected commit SHAs in audit reasons, got: {result['reasons']}"
        )


# ── Pin 3: unknown slug detection from plans dir ────────────────────────────

class TestPin3UnknownSlugDetection:
    """A trailer naming a slug with no corresponding sub-plan file is
    detectable from the plans dir alone.

    This is the write-time check — not yet implemented.  The driver
    already extracts trailer slugs at ``run_ilk_loop_claude.sh:829-831``;
    the fix will compare each extracted slug against the plans dir.
    """

    def test_unknown_slug_detectable_from_plans_dir(
        self, typo_repo: tuple[Path, Path],
    ) -> None:
        """Given the plans dir and a set of trailer slugs, an unknown slug
        (one with no matching sub-plan file) must be detectable without
        fuzzy matching."""
        repo, plans_dir = typo_repo
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

        # Extract slugs from plans dir (mimic what the driver does)
        plan_slugs: set[str] = set()
        for f in plans_dir.glob("*.md"):
            text = f.read_text()
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("plan:"):
                    plan_slugs.add(s[len("plan:"):].strip().strip("'\""))

        # Extract trailer slugs from git log
        result = subprocess.run(
            ["git", "log", "--format=%s%n%b", "--all"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        import re
        trailer_re = re.compile(r"\[plan:([^#]+)#")
        trailer_slugs: set[str] = set()
        for line in result.stdout.splitlines():
            for m in trailer_re.finditer(line):
                trailer_slugs.add(m.group(1))

        # The mangled slug is in trailers but NOT in plans
        assert MANGLED_SLUG in trailer_slugs
        assert MANGLED_SLUG not in plan_slugs

        # The correct slug is in plans but NOT in trailers
        assert CORRECT_SLUG in plan_slugs
        assert CORRECT_SLUG not in trailer_slugs

        # Unknown slugs = trailer slugs - plan slugs
        unknown = trailer_slugs - plan_slugs
        assert MANGLED_SLUG in unknown, (
            f"Expected '{MANGLED_SLUG}' in unknown slugs, got: {unknown}"
        )


# ── Pin 3b: bash driver function ─────────────────────────────────────────────

class TestPin3BashDriver:
    """Test the bash ``check_trailer_slugs_against_plans`` function
    in the driver script."""

    RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "run_ilk_loop_claude.sh"

    def _source_and_call(self, func_call: str, cwd: Path) -> subprocess.CompletedProcess:
        """Dot-source the driver and execute func_call in the same shell."""
        script = (
            f"export ILK_DOTSOURCE_ONLY=1; "
            f"source '{self.RUNNER}' 2>/dev/null; "
            f"set +e; "
            f"{func_call}"
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=30, cwd=str(cwd),
        )

    def test_unknown_slug_detected(self, typo_repo: tuple[Path, Path]) -> None:
        """The bash function must detect the mangled slug and return non-zero."""
        repo, plans_dir = typo_repo
        # Get before/after SHAs (init commit vs HEAD)
        before = subprocess.run(
            ["git", "rev-parse", "HEAD~4"], cwd=str(repo), capture_output=True,
            text=True, check=True,
        ).stdout.strip()
        after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True,
            text=True, check=True,
        ).stdout.strip()

        result = self._source_and_call(
            f"check_trailer_slugs_against_plans '{repo}' '{before}' '{after}' '{plans_dir}'",
            cwd=repo,
        )
        assert result.returncode != 0, (
            "check_trailer_slugs_against_plans must return non-zero for unknown slugs"
            f"\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert MANGLED_SLUG in result.stderr, (
            f"Expected '{MANGLED_SLUG}' in stderr, got: {result.stderr}"
        )

    def test_correct_slugs_pass(self, clean_repo: tuple[Path, Path]) -> None:
        """Correct slugs must pass (return 0, no stderr)."""
        repo, plans_dir = clean_repo
        before = subprocess.run(
            ["git", "rev-parse", "HEAD~4"], cwd=str(repo), capture_output=True,
            text=True, check=True,
        ).stdout.strip()
        after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True,
            text=True, check=True,
        ).stdout.strip()

        result = self._source_and_call(
            f"check_trailer_slugs_against_plans '{repo}' '{before}' '{after}' '{plans_dir}'",
            cwd=repo,
        )
        assert result.returncode == 0, (
            f"check_trailer_slugs_against_plans must return 0 for correct slugs, "
            f"got stderr: {result.stderr}"
        )
        assert result.stderr == ""
