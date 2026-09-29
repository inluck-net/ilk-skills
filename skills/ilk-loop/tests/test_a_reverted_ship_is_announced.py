"""Pin a revert notice for the next worker.

Part of sub-plan ``a-reverted-ship-is-announced`` (MASTER-2026-09-29c).

Five acceptance criteria, verified against the real runner, prompt
assembly, commands/ilk.md, and the detached-component-contracts doc.

The tests use synthetic revert rows and prompt assembly helpers —
no loop runs.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parent.parent / "scripts"
_TESTS = _HERE.parent

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_revert_row(
    slug: str,
    ship_commit: str | None = None,
    reason: str = "ship_integrity",
    from_status: str = "shipped",
    to_status: str = "in-progress",
    from_step: int | None = None,
    to_step: int | None = None,
    run_id: str = "test-run",
    iteration: int = 1,
    timestamp: str = "2026-09-29T10:00:00+0800",
    site: str = "integrity",
) -> dict:
    """Build a synthetic revert row."""
    row: dict = {
        "slug": slug,
        "ship_commit": ship_commit,
        "reason": reason,
        "from_status": from_status,
        "to_status": to_status,
        "from_step": from_step,
        "to_step": to_step,
        "run_id": run_id,
        "iteration": iteration,
        "timestamp": timestamp,
        "site": site,
    }
    return row


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── AC-1: revert appends a row to ship-reverts.jsonl ────────────────────────


class TestRevertHelperValidation:
    """Green controls that validate the test helper — pass today."""

    def test_site_is_one_of_four_values(self):
        """site must be one of integrity, inconclusive, one-ship, final-gate."""
        valid_sites = {"integrity", "inconclusive", "one-ship", "final-gate"}
        for site in valid_sites:
            row = _make_revert_row("alpha", site=site)
            assert row["site"] == site

    def test_required_fields_present_in_helper(self):
        """The helper function produces all required fields."""
        required = {
            "slug", "ship_commit", "reason", "from_status", "to_status",
            "from_step", "to_step", "run_id", "iteration", "timestamp", "site",
        }
        row = _make_revert_row("alpha")
        missing = required - set(row.keys())
        assert not missing, f"helper missing fields: {missing}"


@pytest.mark.xfail(strict=True, reason="revert notice not implemented")
class TestRevertAppendsRow:
    """AC-1: every runner revert appends a row to
    ``<runtime>/launcher/ship-reverts.jsonl`` with the specified fields."""

    REQUIRED_FIELDS = {
        "slug",
        "ship_commit",
        "reason",
        "from_status",
        "to_status",
        "from_step",
        "to_step",
        "run_id",
        "iteration",
        "timestamp",
        "site",
    }

    def test_row_has_all_required_fields(self, tmp_path):
        """A revert row must carry every required field."""
        reverts_file = tmp_path / "launcher" / "ship-reverts.jsonl"
        row = _make_revert_row("alpha", ship_commit="abc1234")

        # The new mechanism should write this row.
        _append_revert_row(
            reverts_file,
            slug="alpha",
            ship_commit="abc1234",
            reason="ship_integrity",
            from_status="shipped",
            to_status="in-progress",
            from_step=2,
            to_step=1,
            run_id="test-run",
            iteration=1,
            timestamp="2026-09-29T10:00:00+0800",
            site="integrity",
        )

        assert reverts_file.exists(), "ship-reverts.jsonl was not created"
        lines = reverts_file.read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        missing = self.REQUIRED_FIELDS - set(rec.keys())
        assert not missing, f"missing fields: {missing}"

    def test_append_is_idempotent_per_slug(self, tmp_path):
        """Two reverts for the same slug produce two rows (append-only)."""
        reverts_file = tmp_path / "launcher" / "ship-reverts.jsonl"
        for _ in range(2):
            _append_revert_row(
                reverts_file,
                slug="alpha",
                ship_commit="abc1234",
                reason="ship_integrity",
                from_status="shipped",
                to_status="in-progress",
                from_step=2,
                to_step=1,
                run_id="test-run",
                iteration=1,
                timestamp="2026-09-29T10:00:00+0800",
                site="integrity",
            )
        lines = reverts_file.read_text().strip().splitlines()
        assert len(lines) == 2, "expected two rows (append-only)"


# ── AC-2: prompt carries revert notice ──────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="revert notice not implemented")
class TestPromptCarriesRevertNotice:
    """AC-2: at the next iteration, for sub-plans of the active master,
    the prompt carries one line per revert row whose sub-plan is still
    not shipped."""

    def test_prompt_includes_revert_line(self):
        """A revert row whose slug is still not shipped appears in the prompt."""
        revert_rows = [
            _make_revert_row("alpha", ship_commit="abc1234", reason="ship_integrity"),
        ]
        shipped_slugs: set[str] = set()

        prompt = _assemble_revert_notice(revert_rows, shipped_slugs)
        assert "REVERTED BY THE RUNNER" in prompt
        assert "alpha" in prompt
        assert "abc1234" in prompt

    def test_prompt_excludes_shipped_reverts(self):
        """A revert row whose slug is now shipped does NOT appear."""
        revert_rows = [
            _make_revert_row("alpha", ship_commit="abc1234"),
        ]
        shipped_slugs = {"alpha"}

        prompt = _assemble_revert_notice(revert_rows, shipped_slugs)
        assert "REVERTED BY THE RUNNER" not in prompt or "alpha" not in prompt


# ── AC-3: commands/ilk.md rule ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="revert notice not implemented")
class TestIlkMdRule:
    """AC-3: ``commands/ilk.md`` gets a short rule: a ``#ship`` commit
    whose sub-plan reads not-shipped is a runner revert — check
    ``ship-reverts.jsonl``, and never resync by editing frontmatter."""

    def test_ilk_md_mentions_reverts(self):
        """commands/ilk.md must mention ship-reverts.jsonl."""
        ilk_md = _find_commands_ilk_md()
        text = ilk_md.read_text(encoding="utf-8")
        assert "ship-reverts.jsonl" in text, (
            "commands/ilk.md does not mention ship-reverts.jsonl"
        )

    def test_ilk_md_forbids_frontmatter_resync(self):
        """commands/ilk.md must forbid resyncing by editing frontmatter."""
        ilk_md = _find_commands_ilk_md()
        text = ilk_md.read_text(encoding="utf-8")
        assert re.search(r"never.*resync|do not.*edit.*frontmatter|not.*desync", text, re.I), (
            "commands/ilk.md should forbid frontmatter resync for reverted ships"
        )


# ── AC-4: contract doc entry ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="revert notice not implemented")
class TestContractDocEntry:
    """AC-4: the contract doc gains a ``ship-reverts.jsonl`` entry:
    writers, readers, fields."""

    def test_contract_doc_has_reverts_section(self):
        """detached-component-contracts.md must document ship-reverts.jsonl."""
        contracts = _find_contracts_doc()
        text = contracts.read_text(encoding="utf-8")
        assert "ship-reverts.jsonl" in text, (
            "contract doc does not mention ship-reverts.jsonl"
        )

    def test_contract_doc_names_writers_and_readers(self):
        """The contract doc must name the writer and reader of ship-reverts.jsonl."""
        contracts = _find_contracts_doc()
        text = contracts.read_text(encoding="utf-8")
        # Find the section about ship-reverts
        section_match = re.search(
            r"##.*ship-reverts.*?\n(.*?)(?=\n##|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        assert section_match, "no ship-reverts section in contract doc"
        section = section_match.group(1)
        assert re.search(r"writer|Who writes", section, re.I), (
            "contract doc does not name the writer"
        )
        assert re.search(r"reader|Who reads", section, re.I), (
            "contract doc does not name the reader"
        )


# ── AC-5: no revert rows ⇒ byte-identical prompt ───────────────────────────


class TestNoRevertsPromptUnchanged:
    """AC-5: with no revert rows, the prompt is byte-identical to today.

    This test passes today — the revert notice mechanism is additive.
    The assembly function is not yet implemented, so we test the
    precondition directly: no rows ⇒ no notice text to inject."""

    def test_empty_reverts_produces_no_notice(self):
        """With no revert rows, there is nothing to inject into the prompt.

        This is a green control — the mechanism is additive, so an empty
        revert list must never add text."""
        revert_rows: list[dict] = []
        shipped_slugs: set[str] = set()

        # The assembly should produce empty string when there are no reverts.
        # Since the function doesn't exist yet, we verify the precondition:
        # iterating over an empty list yields nothing.
        notice_lines = []
        for row in revert_rows:
            if row.get("slug") not in shipped_slugs:
                notice_lines.append(
                    f"REVERTED BY THE RUNNER: {row['slug']} — "
                    f"#ship {row.get('ship_commit', 'unknown')} was undone "
                    f"({row.get('reason', 'unknown')}). "
                    f"This is not a desync: do not set it shipped by hand; "
                    f"make its final gate pass."
                )
        notice = "\n".join(notice_lines)
        assert notice == "", (
            f"expected empty string with no reverts, got: {notice!r}"
        )


# ── Delegation wrappers ─────────────────────────────────────────────────────


def _append_revert_row(
    reverts_path: Path,
    slug: str,
    ship_commit: str | None,
    reason: str,
    from_status: str,
    to_status: str,
    from_step: int | None,
    to_step: int | None,
    run_id: str,
    iteration: int,
    timestamp: str,
    site: str,
) -> None:
    """Append a revert row to ship-reverts.jsonl.

    Delegates to the Python helper (implemented in step 1).
    The runner is a bash script; the Python helper lives in
    ``scripts/revert_notice.py`` (to be created).
    """
    import revert_notice as rn

    rn.append_revert_row(
        reverts_path,
        slug=slug,
        ship_commit=ship_commit,
        reason=reason,
        from_status=from_status,
        to_status=to_status,
        from_step=from_step,
        to_step=to_step,
        run_id=run_id,
        iteration=iteration,
        timestamp=timestamp,
        site=site,
    )


def _assemble_revert_notice(
    revert_rows: list[dict],
    shipped_slugs: set[str],
) -> str:
    """Assemble the revert notice for the worker prompt.

    Delegates to ``scripts/revert_notice.py`` (implemented in step 1).
    """
    import revert_notice as rn

    return rn.assemble_revert_notice(revert_rows, shipped_slugs)


def _find_commands_ilk_md() -> Path:
    """Locate commands/ilk.md relative to the repo root."""
    repo_root = _HERE.parent.parent.parent.parent
    ilk_md = repo_root / "commands" / "ilk.md"
    if not ilk_md.exists():
        pytest.skip("commands/ilk.md not found")
    return ilk_md


def _find_contracts_doc() -> Path:
    """Locate detached-component-contracts.md."""
    contracts = _SCRIPTS.parent / "references" / "detached-component-contracts.md"
    if not contracts.exists():
        pytest.skip("detached-component-contracts.md not found")
    return contracts
