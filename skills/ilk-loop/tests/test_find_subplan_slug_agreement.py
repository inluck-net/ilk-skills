"""Every sub-plan must resolve by the slug the rest of the toolkit reports.

Four components carry a sub-plan identity.  Three derive it from the FILENAME
(``loop_status.py`` strips the date prefix and ``.md``; ``ship-proof.jsonl``
records that form; ``quarantine_subplan.py`` globs ``*-<slug>.md``) and
``run_local_checks.find_subplan`` used to be the lone outlier, matching only
frontmatter ``plan:``.

Measured 2026-09-08 on a consumer host: a generator wrote the run id into the
filename and omitted it from frontmatter, so
``2026-09-08-issue-4796-work-52d08c9c.md`` carried ``plan: issue-4796-work``.

    --slug issue-4796-work-52d08c9c  -> {"error": "sub-plan not found ..."}
    --slug issue-4796-work           -> resolves, starts `bun run test`

``error`` is in ``_BLOCKING_OUTCOMES``, and a name mismatch is deterministic,
so the confirm-before-block re-run reproduced it and the loop read a harness
failure as a real gate failure.  Two seconds elapsed between worker-done and
loop-stop against a gate declaring ``bun run test, timeout: 300`` — no test
ever ran, on three consecutive issues.

The invariant these pin is the cross-component one, not the single bug: for
every sub-plan on disk, the slug ``loop_status`` reports must resolve through
``find_subplan``.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_local_checks as rlc  # noqa: E402
from plan_slug import strip_date_prefix  # noqa: E402


_SUBPLAN = """\
---
plan: {plan}
status: in-progress
current_step: 0
---

# Sub-plan

### Step 0

```yaml
local_checks:
  - command: "true"
    timeout: 60
```
"""


def _plans(tmp_path: Path) -> Path:
    """A resolvable in-tree plans dir.

    ``ilk_paths.find_plans_dir`` needs BOTH a project root (a git root) and a
    MASTER file present -- without either it returns (None, "") and
    ``find_subplan`` bails before looking at any slug, which makes every
    assertion here fail for a reason that has nothing to do with slugs.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   capture_output=True, encoding="utf-8", errors="replace")
    d = tmp_path / "docs" / "plans"
    d.mkdir(parents=True)
    (d / "MASTER-2026-09-08-fixture-execution-plan.md").write_text(
        "---\nstatus: in-progress\n---\n\n# MASTER\n", encoding="utf-8")
    return d


def _loop_status_slug(fname: str) -> str:
    """Reproduce loop_status.py:377-384 — the form the loop reports."""
    slug = fname[:-3] if fname.endswith(".md") else fname
    return strip_date_prefix(slug)


def _mk(plans: Path, fname: str, plan_field: str) -> None:
    (plans / fname).write_text(_SUBPLAN.format(plan=plan_field), encoding="utf-8")


def test_resolves_by_the_slug_loop_status_reports(tmp_path: Path) -> None:
    """The real shape: run id in the filename, absent from frontmatter."""
    plans = _plans(tmp_path)
    fname = "2026-09-08-issue-4796-work-52d08c9c.md"
    _mk(plans, fname, "issue-4796-work")

    slug = _loop_status_slug(fname)
    assert slug == "issue-4796-work-52d08c9c"

    got = rlc.find_subplan(tmp_path, slug)
    assert got is not None, (
        f"find_subplan could not resolve {slug!r}, the slug loop_status reports "
        f"and ship-proof.jsonl records — a not-found is a BLOCKING outcome, so "
        f"this reads as a real gate failure"
    )
    assert got.name == fname


def test_frontmatter_form_still_resolves(tmp_path: Path) -> None:
    """No regression: frontmatter is tried first and still wins."""
    plans = _plans(tmp_path)
    _mk(plans, "2026-09-08-issue-4796-work-52d08c9c.md", "issue-4796-work")
    got = rlc.find_subplan(tmp_path, "issue-4796-work")
    assert got is not None and got.name == "2026-09-08-issue-4796-work-52d08c9c.md"


def test_the_invariant_across_every_subplan_in_a_dir(tmp_path: Path) -> None:
    """The general rule, not one example: every sub-plan resolves by its
    loop_status slug, whatever its frontmatter says."""
    plans = _plans(tmp_path)
    cases = {
        # filename                                    frontmatter plan:
        "2026-09-08-issue-4796-work-52d08c9c.md":     "issue-4796-work",
        "2026-09-08-issue-4798-work-a1b2c3d4.md":     "issue-4798-work",
        "2026-09-08b-a-status-read-does-not-mutate.md": "a-status-read-does-not-mutate",
        "2026-09-08-plain.md":                         "plain",
    }
    for fname, plan in cases.items():
        _mk(plans, fname, plan)

    unresolved = []
    for fname in cases:
        slug = _loop_status_slug(fname)
        if rlc.find_subplan(tmp_path, slug) is None:
            unresolved.append((fname, slug))

    assert not unresolved, (
        f"{len(unresolved)} of {len(cases)} sub-plans do not resolve by the slug "
        f"loop_status reports: {unresolved}"
    )


def test_the_filename_fallback_never_returns_a_master(tmp_path: Path) -> None:
    """The filename fallback must not hand back a MASTER.

    quarantine_subplan excludes them for the same reason: a master is not a
    gateable sub-plan, and gating one would run the whole batch's checks.

    The fixture uses ``slug:`` rather than ``plan:`` because that is what real
    masters carry (see MASTER-2026-09-08b) -- so the ONLY way this file could
    be returned is through the filename glob, which is what this pins.  The
    frontmatter branch is deliberately not asserted on here: a master carrying
    a ``plan:`` field would match it, but no master in this toolkit does.
    """
    plans = _plans(tmp_path)
    (plans / "MASTER-2026-09-08-state-ownership.md").write_text(
        "---\nslug: 2026-09-08-state-ownership\nstatus: draft\n---\n\n# MASTER\n",
        encoding="utf-8")
    assert rlc.find_subplan(tmp_path, "state-ownership") is None


def test_an_unknown_slug_still_returns_none(tmp_path: Path) -> None:
    """The fallback must not become a wildcard — a genuinely absent slug is
    still absent, or a typo would silently gate the wrong sub-plan."""
    plans = _plans(tmp_path)
    _mk(plans, "2026-09-08-issue-4796-work-52d08c9c.md", "issue-4796-work")
    assert rlc.find_subplan(tmp_path, "issue-9999-does-not-exist") is None
