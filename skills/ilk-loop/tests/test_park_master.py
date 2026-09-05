"""park_master — the missing inverse of /ilk-resume.

`/ilk-resume` un-parks a blacklisted project; nothing performed the park, so
taking a batch out of the queue meant hand-editing frontmatter under the
external plans dir with no record of why.

The distinction the tool exists to enforce: the postmortem blacklist is a
60-MINUTE BACKOFF, not a park.  On 2026-09-05 a duplicate resolver run was
killed three times and returned each time, because each kill only bought an
hour.  Setting the master's status out of {queued, active} removes it from
scheduler_scan entirely, which is what survives the expiry.

AC-1  Park moves a runnable master to `blocked` and records why.
AC-2  A parked master is not a runnable status (scheduler_scan drops it).
AC-3  Unpark returns it to `queued` and REMOVES the stamp.
AC-4  A no-match names its search space rather than failing silently.
AC-5  --dry-run writes nothing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "park_master.py"
sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
from plan_status import is_master_runnable_status, parse_frontmatter  # noqa: E402


def _run(plans: Path, *extra: str, expect: int = 0) -> dict:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--plans-dir", str(plans), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == expect, f"exit {r.returncode}: {r.stderr or r.stdout}"
    return json.loads(r.stdout)


@pytest.fixture
def plans(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    (d / "2026-09-06-work.md").write_text(
        "---\nplan: 2026-09-06-work\nstatus: pending\n---\n\n### Step 0\n- work\n",
        encoding="utf-8")
    (d / "MASTER-2026-09-06-demo.md").write_text(
        "---\ntitle: demo\ncreated: 2026-09-06T00:00:00+08:00\n"
        "status: active\npriority: 0\n---\n\n# demo\n\n| # | file |\n|---|---|\n"
        "| 0 | [2026-09-06-work.md](2026-09-06-work.md) |\n",
        encoding="utf-8")
    return d


def _fm(plans: Path) -> dict:
    return parse_frontmatter(
        (plans / "MASTER-2026-09-06-demo.md").read_text(encoding="utf-8-sig"))


class TestPark:
    def test_park_sets_blocked(self, plans: Path) -> None:
        out = _run(plans, "--reason", "duplicate of PR #4622")
        assert out["from"] == "active" and out["to"] == "blocked"
        assert _fm(plans)["status"] == "blocked"

    def test_park_records_why(self, plans: Path) -> None:
        """A bare `status: blocked` is indistinguishable from a self-inflicted stall."""
        _run(plans, "--reason", "duplicate of PR #4622")
        fm = _fm(plans)
        assert fm["parked_reason"] == "duplicate of PR #4622"
        assert fm["parked_at"]

    def test_parked_master_is_not_runnable(self, plans: Path) -> None:
        """AC-2: this is the property the scheduler actually consumes."""
        _run(plans, "--reason", "x")
        assert not is_master_runnable_status(_fm(plans)["status"]), (
            "a parked master still counts as runnable — scheduler_scan would "
            "keep dispatching it"
        )

    def test_repark_does_not_accumulate_stamps(self, plans: Path) -> None:
        _run(plans, "--reason", "first")
        _run(plans, "--unpark")
        _run(plans, "--reason", "second")
        text = (plans / "MASTER-2026-09-06-demo.md").read_text(encoding="utf-8-sig")
        assert text.count("parked_at:") == 1
        assert "first" not in text


class TestUnpark:
    def test_unpark_returns_to_queued(self, plans: Path) -> None:
        _run(plans, "--reason", "x")
        out = _run(plans, "--unpark")
        assert out["from"] == "blocked" and out["to"] == "queued"
        assert is_master_runnable_status(_fm(plans)["status"])

    def test_unpark_removes_the_stamp(self, plans: Path) -> None:
        """A queued master carrying parked_at reads as still parked."""
        _run(plans, "--reason", "x")
        _run(plans, "--unpark")
        fm = _fm(plans)
        assert "parked_at" not in fm and "parked_reason" not in fm


class TestNegativesAndDryRun:
    def test_no_match_names_the_search_space(self, plans: Path) -> None:
        """AC-4: 'nothing to unpark' and 'wrong directory' must differ."""
        out = _run(plans, "--unpark", expect=1)
        assert out["error"] == "no matching master"
        assert out["wanted"] == "blocked"
        assert out["masters_searched"] == [
            {"master": "MASTER-2026-09-06-demo.md", "status": "active"}]

    def test_dry_run_writes_nothing(self, plans: Path) -> None:
        before = (plans / "MASTER-2026-09-06-demo.md").read_text(encoding="utf-8-sig")
        out = _run(plans, "--reason", "x", "--dry-run")
        assert out["dry_run"] is True and out["to"] == "blocked"
        assert (plans / "MASTER-2026-09-06-demo.md").read_text(encoding="utf-8-sig") == before

    def test_ambiguous_requires_master(self, plans: Path) -> None:
        (plans / "MASTER-2026-09-06-other.md").write_text(
            "---\ntitle: other\nstatus: queued\n---\n\n# other\n", encoding="utf-8")
        out = _run(plans, "--reason", "x", expect=1)
        assert "several masters match" in out["error"]
        assert len(out["candidates"]) == 2

    def test_master_flag_disambiguates(self, plans: Path) -> None:
        (plans / "MASTER-2026-09-06-other.md").write_text(
            "---\ntitle: other\nstatus: queued\n---\n\n# other\n", encoding="utf-8")
        out = _run(plans, "--reason", "x", "--master", "MASTER-2026-09-06-other.md")
        assert out["master"] == "MASTER-2026-09-06-other.md"
        assert _fm(plans)["status"] == "active", "the wrong master was parked"


class TestReasonSurvivesRoundTrip:
    """A reason containing `#` must not be silently truncated.

    parse_frontmatter strips an inline `# comment` from UNQUOTED scalars, so
    an unquoted `duplicate of PR #4622` reads back as `duplicate of PR`.
    `#` is exactly what a real reason carries — issue and PR numbers — so
    this is the common case, not an edge one.
    """

    @pytest.mark.parametrize("reason", [
        "duplicate of PR #4622",
        "superseded by #4622 and #4623",
        "blocked on upstream: needs 'quotes' and \"doubles\"",
        "line one\nline two",
        "trailing space and #hash ",
    ])
    def test_reason_round_trips(self, plans: Path, reason: str) -> None:
        _run(plans, "--reason", reason)
        got = _fm(plans)["parked_reason"]
        # Newlines are flattened (frontmatter is line-oriented) and inner
        # double quotes folded, but nothing is truncated.
        assert "#" not in reason or "#" in got, f"hash truncated: {got!r}"
        assert got.split()[-1] == " ".join(reason.split()).replace('"', "'").split()[-1], (
            f"reason truncated: {got!r} from {reason!r}"
        )

    def test_hash_reason_is_not_cut_at_the_hash(self, plans: Path) -> None:
        _run(plans, "--reason", "duplicate of PR #4622")
        assert _fm(plans)["parked_reason"] == "duplicate of PR #4622"

    def test_frontmatter_stays_parseable_after_stamping(self, plans: Path) -> None:
        _run(plans, "--reason", 'weird: "value" # with comment')
        fm = _fm(plans)
        assert fm["status"] == "blocked", "stamping corrupted the frontmatter"
        assert fm["title"] == "demo"
