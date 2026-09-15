"""A declared gate that yields nothing runnable is malformed, not absent.

`parse_local_checks_block` returns `[]` for a block it cannot read, and
`run_local_checks.main` computes `passed = all(r.passed for r in results)`.
`all([])` is **True**, so an unreadable gate block reported a pass with no gate
having run.

Two shapes produced that, both measured 2026-09-15:

1. **`cmd:` instead of `command:`** — a resolver-generated sub-plan declared
   `- name: repo-verifier-1` / `cmd: bunx tsc ...`. It parsed to `[]`, the step
   passed unconditionally, and it shipped `2/2` reporting "0 fail" with nothing
   run. 66 of 67 batch-verification sub-plans on that host had the shape; 63
   shipped.
2. **A comment before the first item** — the parser's indent-establishing branch
   saw a line not starting with `-` and `break`, dropping the whole block. 7
   sub-plans on this host had their gate silently disabled by the comment that
   explained the gate.

(1) must refuse. (2) must parse.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_local_checks as rlc  # noqa: E402


# ── (2) a comment must not destroy the block ────────────────────────────────

class TestCommentBeforeFirstItem:
    def test_comment_before_first_item_still_parses(self) -> None:
        y = ("local_checks:\n"
             "  # frontmatter checks run at EVERY step, so this must reference\n"
             "  # paths that already exist.\n"
             "  - command: pytest -q\n"
             "    timeout: 180\n")
        assert len(rlc.parse_local_checks_block(y)) == 1, (
            "a comment before the first item dropped the entire block — the "
            "comment explaining the gate disabled the gate"
        )

    def test_comment_between_items_still_parses(self) -> None:
        y = "local_checks:\n  - command: a\n  # mid-list\n  - command: b\n"
        assert len(rlc.parse_local_checks_block(y)) == 2

    def test_non_list_after_key_still_stops(self) -> None:
        """The original guard must survive: a mapping is not a list."""
        assert rlc.parse_local_checks_block("local_checks:\n  oops: 1\n") == []


# ── the item counter: declared vs runnable ──────────────────────────────────

class TestItemCounter:
    def test_counts_unparseable_items(self) -> None:
        y = "local_checks:\n  - name: x\n    cmd: bunx tsc\n"
        assert rlc.count_local_checks_items(y) == 1
        assert rlc.parse_local_checks_block(y) == []

    def test_explicit_empty_is_zero_not_malformed(self) -> None:
        """`local_checks: []` is a legitimate 'no gates' declaration."""
        assert rlc.count_local_checks_items("local_checks: []\n") == 0

    def test_absent_key_is_zero(self) -> None:
        assert rlc.count_local_checks_items("other: 1\n") == 0

    def test_well_formed_items_match(self) -> None:
        y = "local_checks:\n  - command: a\n    timeout: 5\n  - command: b\n"
        assert rlc.count_local_checks_items(y) == 2
        assert len(rlc.parse_local_checks_block(y)) == 2


# ── end to end: main() refuses rather than reporting a pass ─────────────────

def _project(tmp_path: Path, frontmatter: str, slug: str) -> Path:
    """A project whose external plans dir holds one sub-plan and a MASTER."""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    sys.path.insert(0, str(SCRIPTS_DIR))
    from ilk_paths import external_plans_dir, resolve_project_key  # noqa: E402
    plans = Path(external_plans_dir(resolve_project_key(proj)))
    plans.mkdir(parents=True, exist_ok=True)
    (plans / f"2026-01-01-{slug}.md").write_text(
        f"---\nplan: {slug}\nstatus: pending\n{frontmatter}---\n# {slug}\n",
        encoding="utf-8")
    (plans / "MASTER-2026-01-01-execution-plan.md").write_text(
        f"---\nstatus: active\n---\n# m\n- [x](./2026-01-01-{slug}.md)\n",
        encoding="utf-8")
    return proj


class TestMainRefuses:
    def test_malformed_block_exits_2_and_is_not_a_pass(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """The load-bearing case: it must NOT report all_passed true."""
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = _project(tmp_path, "local_checks:\n  - name: v\n    cmd: true\n", "bad")
        rc = rlc.main(["--project", str(proj), "--slug", "bad"])
        out = capsys.readouterr().out
        assert rc == 2, f"expected refusal exit 2, got {rc}"
        assert '"all_passed": false' in out.lower().replace(" ", " "), out[:300]
        assert "malformed local_checks" in out

    def test_explicit_empty_still_passes(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Regression guard: declaring no gates must stay legal."""
        monkeypatch.setenv("ILK_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        proj = _project(tmp_path, "local_checks: []\n", "none")
        rc = rlc.main(["--project", str(proj), "--slug", "none"])
        assert rc == 0, capsys.readouterr().out[:300]
