"""A verdict carries validated base provenance (step 0).

Measured on gh-resolve run 20261011-061345: the recorder ran with
``--base-sha auto``, the MASTER frontmatter named ``84a572e3``, the record
stored ``5d79154c``, and a worker's pre-existing claim cited ``a1623fba`` —
a batch-verification marker that landed *after* the implementation.  The
resolver derives from the earliest matching trailers, so a difference from
frontmatter is not by itself proof of an incorrect base; and the previous
provenance string always printed the *first registry slug* regardless of
which slug actually matched.

These cases pin what step 0 delivers: a recorded base carries its selection
mode (explicit or derived), its resolved SHA, and its derivation evidence —
the actual matching trailer slug/commit.  A master/frontmatter versus derived
mismatch is carried as an actionable provenance conflict with both SHAs, not
silently reconciled and not used to excuse anything.

Every case builds a real temporary git history.  No full suite is run.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "commit", "-q", "--allow-empty", "-m", message)
    return _git(cwd, "rev-parse", "HEAD").strip()


def _make_repo(tmp_path: Path) -> Path:
    """init, then a ``beta`` trailer, then an interleaved foreign plan, then
    an ``alpha`` trailer.  The earliest registry-slug match is therefore
    ``beta`` even though the registry lists ``alpha`` first — the shape the
    old provenance string got wrong."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init", "-q")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    _commit(proj, "init")
    _commit(proj, "feat(y): beta step 0 [plan:beta#step-0]")
    _commit(proj, "feat(z): foreign plan [plan:foreign#step-0]")
    _commit(proj, "feat(x): alpha step 0 [plan:alpha#step-0]")
    return proj


def _shas(proj: Path) -> list[str]:
    return _git(proj, "log", "--reverse", "--format=%H").strip().splitlines()


def _make_master(tmp_path: Path, declared_base: str | None = None) -> Path:
    fm_lines = [
        "---",
        "master_plan: test-batch",
        "batch_date: 2026-09-29",
    ]
    if declared_base:
        fm_lines.append(f"base_sha: {declared_base}")
    fm_lines += [
        "status: active",
        "current_subplan: alpha",
        "---",
    ]
    body = textwrap.dedent("""\

        # MASTER plan: test batch

        ## Sub-plan registry

        | # | Slug | Items | Steps (est.) | Status |
        |---|---|---|---|---|
        | 1 | [2026-09-29-alpha.md](./2026-09-29-alpha.md) | alpha | 3 | pending |
        | 2 | [2026-09-29-beta.md](./2026-09-29-beta.md) | beta | 3 | pending |
    """)
    master = tmp_path / "MASTER-test.md"
    master.write_text("\n".join(fm_lines) + "\n" + body, encoding="utf-8")
    return master


RECORD_STUB = textwrap.dedent("""\
    # Batch verification record — test

    record_writer: verification_record
    batch: test
    verified_head: deadbeef
    verified_tree: cafebabe
    base_sha: 0000000
    suite_invocation: python3 -m pytest -q

    ## At-base rerun

    _(no failures)_
""")


# ── explicit vs auto: the selection mode is on the answer ───────────────────

class TestSelectionMode:

    def test_explicit_choice_is_recorded_as_explicit(self, tmp_path: Path) -> None:
        """An operator-supplied SHA is a selection, and it says so."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        shas = _shas(proj)

        sel = vr.select_batch_base(proj, master, explicit_sha=shas[2])
        assert sel.selection_mode == vr.BASE_SELECTION_EXPLICIT
        assert sel.base_sha == shas[2]
        assert sel.derivation_text == "operator-supplied"
        assert "explicit" in sel.evidence
        assert sel.derivation_slug is None

    def test_auto_choice_is_recorded_as_derived(self, tmp_path: Path) -> None:
        """``--base-sha auto`` is a derivation, and it says so."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)

        sel = vr.select_batch_base(proj, master, explicit_sha="auto")
        assert sel.selection_mode == vr.BASE_SELECTION_DERIVED
        assert sel.derivation_slug is not None
        assert sel.derivation_commit is not None

    def test_resolve_batch_base_sha_keeps_its_tuple(self, tmp_path: Path) -> None:
        """The historical ``(base_sha, provenance)`` wrapper still works."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        shas = _shas(proj)

        base, provenance = vr.resolve_batch_base_sha(proj, master)
        assert base == shas[0]
        assert provenance.startswith(f"base_sha: {base}")

        explicit = shas[2]
        base2, provenance2 = vr.resolve_batch_base_sha(
            proj, master, explicit_sha=explicit)
        assert base2 == explicit
        assert "(explicit)" in provenance2


# ── derivation evidence names the slug that actually matched ───────────────

class TestDerivationEvidence:

    def test_derivation_names_the_actual_matching_slug(self, tmp_path: Path) -> None:
        """Registry order is alpha, beta; the first trailer commit is beta.

        The evidence must name beta.  Naming ``alpha`` because it is first in
        the registry is the measured defect: it is true of the registry, not
        of the history.
        """
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        shas = _shas(proj)

        sel = vr.select_batch_base(proj, master)
        assert sel.selection_mode == vr.BASE_SELECTION_DERIVED
        assert sel.derivation_slug == "beta", (
            f"expected the slug that matched, got {sel.derivation_slug!r}"
        )
        assert sel.derivation_commit == shas[1], (
            "the derivation commit is the beta trailer commit"
        )
        assert sel.base_sha == shas[0], "base is that commit's parent"
        assert "[plan:beta#…]" in sel.evidence
        assert "[plan:alpha#…]" not in sel.evidence

    def test_provenance_string_carries_the_matched_slug(self, tmp_path: Path) -> None:
        """The one-line provenance a human reads names the same slug."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)

        _, provenance = vr.resolve_batch_base_sha(proj, master)
        assert "[plan:beta#…]" in provenance
        assert "[plan:alpha#…]" not in provenance

    def test_interleaved_foreign_plan_does_not_move_the_base(
            self, tmp_path: Path) -> None:
        """A foreign plan's trailer between the batch's commits is not a match."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        shas = _shas(proj)

        sel = vr.select_batch_base(proj, master)
        assert sel.base_sha == shas[0]
        assert sel.derivation_slug == "beta"


# ── unknown history refuses ────────────────────────────────────────────────

class TestUnknownHistory:

    def _repo_without_registry_trailers(self, tmp_path: Path) -> Path:
        proj = tmp_path / "proj"
        proj.mkdir()
        _git(proj, "init", "-q")
        _git(proj, "config", "user.email", "t@example.com")
        _git(proj, "config", "user.name", "t")
        _commit(proj, "init")
        _commit(proj, "feat: unrelated [plan:gamma#step-0]")
        return proj

    def test_no_matching_trailer_refuses(self, tmp_path: Path) -> None:
        """History with no registry-slug trailer cannot derive a base."""
        proj = self._repo_without_registry_trailers(tmp_path)
        master = _make_master(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            vr.select_batch_base(proj, master)
        assert exc_info.value.code != 0

    def test_refusal_names_the_slugs_searched(
            self, tmp_path: Path, capsys) -> None:
        """A negative carries its denominator: the search space is named."""
        proj = self._repo_without_registry_trailers(tmp_path)
        master = _make_master(tmp_path)

        with pytest.raises(SystemExit):
            vr.select_batch_base(proj, master)
        err = capsys.readouterr().err
        assert "alpha" in err and "beta" in err, (
            f"refusal must name the slugs searched; got {err!r}"
        )

    def test_master_without_registry_slugs_refuses(self, tmp_path: Path) -> None:
        """A master with no sub-plan filenames has no derivation to make."""
        proj = _make_repo(tmp_path)
        empty = tmp_path / "MASTER-empty.md"
        empty.write_text("---\nmaster_plan: x\n---\n\n# no registry\n",
                         encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            vr.select_batch_base(proj, empty)
        assert exc_info.value.code != 0


# ── frontmatter disagreement is a carried conflict ─────────────────────────

class TestFrontmatterConflict:

    def test_derived_base_conflicting_with_frontmatter_is_carried(
            self, tmp_path: Path) -> None:
        """MASTER names a base the derivation does not produce.

        Both SHAs stay on the selection.  The mismatch is not proof the
        derived base is wrong, and it is not a licence to excuse the failure.
        """
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        declared = shas[3]  # a later commit, deliberately not the derived base
        master = _make_master(tmp_path, declared_base=declared)

        sel = vr.select_batch_base(proj, master)
        assert sel.selection_mode == vr.BASE_SELECTION_DERIVED
        assert sel.base_sha == shas[0]
        assert sel.frontmatter_sha == declared
        assert sel.conflict is True
        assert shas[0] in sel.evidence and declared in sel.evidence

    def test_matching_frontmatter_is_not_a_conflict(self, tmp_path: Path) -> None:
        """Negative control: agreement must stay green and silent."""
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        master = _make_master(tmp_path, declared_base=shas[0])

        sel = vr.select_batch_base(proj, master)
        assert sel.conflict is False
        assert sel.frontmatter_sha == shas[0]

    def test_prefix_match_is_agreement(self, tmp_path: Path) -> None:
        """A 12-char frontmatter SHA naming the same object is agreement."""
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        master = _make_master(tmp_path, declared_base=shas[0][:12])

        sel = vr.select_batch_base(proj, master)
        assert sel.conflict is False

    def test_explicit_choice_resolves_the_conflict_but_keeps_the_evidence(
            self, tmp_path: Path) -> None:
        """An explicit operator choice settles the question; the record still
        shows that a question was settled."""
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        declared = shas[0]
        master = _make_master(tmp_path, declared_base=declared)

        sel = vr.select_batch_base(proj, master, explicit_sha=shas[2])
        assert sel.base_sha == shas[2]
        assert sel.selection_mode == vr.BASE_SELECTION_EXPLICIT
        assert sel.conflict is True
        assert sel.frontmatter_sha == declared
        assert "explicit" in sel.evidence


# ── the record carries the evidence ────────────────────────────────────────

class TestRecordCarriesProvenance:

    def test_cli_persists_derived_selection_fields(self, tmp_path: Path) -> None:
        """Production entry: ``--base-sha auto --master`` writes the evidence
        onto the record, not only onto stdout."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        shas = _shas(proj)
        rec = tmp_path / "r.md"
        rec.write_text(RECORD_STUB, encoding="utf-8")

        rc = vr.main([
            "--project", str(proj),
            "--record", str(rec),
            "--base-sha", "auto",
            "--master", str(master),
        ])
        assert rc == 0
        text = rec.read_text(encoding="utf-8")
        assert "base_selection: derived" in text
        assert f"base_derivation: {shas[1]} [plan:beta#…]" in text
        assert "base_frontmatter:" not in text, "no declared base, no field"

    def test_cli_persists_explicit_selection_fields(self, tmp_path: Path) -> None:
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        shas = _shas(proj)
        rec = tmp_path / "r.md"
        rec.write_text(RECORD_STUB, encoding="utf-8")

        rc = vr.main([
            "--project", str(proj),
            "--record", str(rec),
            "--base-sha", shas[2],
            "--master", str(master),
        ])
        assert rc == 0
        text = rec.read_text(encoding="utf-8")
        assert "base_selection: explicit" in text
        assert "base_derivation: operator-supplied" in text

    def test_cli_persists_the_frontmatter_conflict(self, tmp_path: Path) -> None:
        """The mismatch reaches the record a successor reads, with both SHAs."""
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        declared = shas[3]
        master = _make_master(tmp_path, declared_base=declared)
        rec = tmp_path / "r.md"
        rec.write_text(RECORD_STUB, encoding="utf-8")

        rc = vr.main([
            "--project", str(proj),
            "--record", str(rec),
            "--base-sha", "auto",
            "--master", str(master),
        ])
        assert rc == 0
        text = rec.read_text(encoding="utf-8")
        assert f"base_frontmatter: {declared}" in text
        assert "base_provenance_conflict:" in text
        assert shas[0] in text and declared in text
        assert "base_selection: derived" in text

    def test_measured_record_header_carries_the_selection(self, tmp_path: Path) -> None:
        """The suite-running writer puts the same fields in the header."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        shas = _shas(proj)
        sel = vr.select_batch_base(proj, master)

        text = vr.render_record(
            batch="test", head=shas[-1], tree="t" * 40,
            base_sha=sel.base_sha,
            invocation="python3 -m pytest -q",
            scope={"mode": "full", "reason": "x", "count": 1},
            results={"counts": {"total": 1, "passed": 1, "failed": 0,
                                "errors": 0, "skipped": 0}},
            at_base={}, base_red=[], head_red=[],
            base_selection=sel,
        )
        assert "base_selection: derived" in text
        assert f"base_derivation: {shas[1]} [plan:beta#…]" in text

    def test_absent_selection_adds_no_fields(self) -> None:
        """Additive contract: a caller without a selection writes the record
        it always wrote.  Old records and old callers stay valid."""
        text = vr.render_record(
            batch="test", head="a" * 40, tree="b" * 40,
            base_sha="c" * 40,
            invocation="python3 -m pytest -q",
            scope={"mode": "full", "reason": "x", "count": 1},
            results={"counts": {"total": 0, "passed": 0, "failed": 0,
                                "errors": 0, "skipped": 0}},
            at_base={}, base_red=[], head_red=[],
        )
        assert "base_selection:" not in text
        assert "base_derivation:" not in text
        assert "base_provenance_conflict:" not in text
        assert f"base_sha: {'c' * 40}" in text

    def test_injection_is_idempotent_and_replaces(self, tmp_path: Path) -> None:
        """Re-running updates the fields rather than stacking a second copy."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        sel = vr.select_batch_base(proj, master)

        once = vr.inject_base_provenance(RECORD_STUB, sel)
        twice = vr.inject_base_provenance(once, sel)
        assert twice.count("base_selection:") == 1
        assert twice.count("base_derivation:") == 1
        assert once == twice

    def test_legacy_record_keeps_its_existing_fields(self, tmp_path: Path) -> None:
        """Injection is additive: nothing the record already carried is lost."""
        proj = _make_repo(tmp_path)
        master = _make_master(tmp_path)
        sel = vr.select_batch_base(proj, master)

        out = vr.inject_base_provenance(RECORD_STUB, sel)
        for kept in ("record_writer:", "verified_head:", "base_sha:",
                     "suite_invocation:", "_(no failures)_"):
            assert kept in out, f"lost {kept!r}"
