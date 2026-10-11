"""A verdict carries validated base provenance.

Measured on gh-resolve run 20261011-061345: the recorder ran with
``--base-sha auto``, the MASTER frontmatter named ``84a572e3``, the record
stored ``5d79154c``, and a worker's pre-existing claim cited ``a1623fba`` —
a batch-verification marker that landed *after* the implementation.  The
resolver derives from the earliest matching trailers, so a difference from
frontmatter is not by itself proof of an incorrect base; and the previous
provenance string always printed the *first registry slug* regardless of
which slug actually matched.

Step 0 pins the selection itself: a recorded base carries its mode (explicit
or derived), its resolved SHA, and its derivation evidence — the actual
matching trailer slug/commit.  A master/frontmatter versus derived mismatch
is carried as an actionable provenance conflict with both SHAs, not silently
reconciled and not used to excuse anything.

Step 1 pins the consumer side: verification and attribution share that
selection policy, and a stale re-measure validates the base **before** it
launches a suite.  A mismatch refuses with both SHAs and a recovery command;
legacy record-only use still works and is marked ``unvalidated``.  A
post-implementation marker cannot exonerate a regression measured under a
validated pre-batch base.

Every case builds a real temporary git history.  No full suite is run.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verification_record as vr  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin HOME / ILK_DATA_HOME / ILK_DATA_DIR to tmp_path; clear the worker flag.

    Same contract as the rest of the loop suite: nothing here may write the
    ambient ``~/.ilk-data`` (DATA-ROOT GUARD), and ``verification_record`` /
    ``verify_attribution`` both refuse inside a worker session.
    """
    real_home = Path(os.environ.get("HOME", Path.home())).resolve()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    data_home = tmp_path / "data"
    data_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ILK_DATA_HOME", str(data_home))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    monkeypatch.delenv("ILK_WORKER_SESSION", raising=False)
    user_base = real_home / "Library" / "Python" / "3.9"
    if user_base.is_dir():
        monkeypatch.setenv("PYTHONUSERBASE", str(user_base))


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


# ═══════════════════════════════════════════════════════════════════════════
# Step 1 — consumer enforcement: attribution and remeasurement
# ═══════════════════════════════════════════════════════════════════════════


def _write_launch(repo: Path) -> None:
    (repo / ".ilk-launch.json").write_text(
        textwrap.dedent("""\
            {
              "ship": {
                "suite": {
                  "command": "python3",
                  "flags": ["-m", "pytest", "-q", "-p", "no:cacheprovider"]
                }
              }
            }
        """),
        encoding="utf-8",
    )


def _write_touch_suite(repo: Path, marker: Path) -> None:
    """A tiny suite whose *import* writes *marker*.

    Collection or execution of the suite therefore leaves a trace.  A run
    that refuses before the suite is launched leaves none — which is the
    assertion, measured at the process boundary rather than inferred from a
    return code.
    """
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_touch.py").write_text(
        f"from pathlib import Path\n"
        f"Path(r'{marker}').write_text('ran')\n"
        f"def test_touch():\n    assert True\n",
        encoding="utf-8",
    )


def _stale_record(path: Path, *, base_sha: str, head: str,
                  failing: list[str] | None = None) -> Path:
    """A record that is stale (tree ≠ HEAD) but still parseable.

    Stale is what makes ``--remeasure-if-stale`` consider re-measuring at all;
    parseable is what lets attribution read the verdict afterwards.  Both at
    once is the real shape: the measurement is old, not garbage.

    Each failing node gets a 5-column row that ``derive_attributed`` reads as
    owned (``passed`` at base, ``3/3`` red at head, batch-touched) — so a
    green verdict here would mean the red was excused.
    """
    failing = failing or []
    if failing:
        rows = (
            "\n| node id | at base | in baseline_red | head reruns "
            "| batch touched file |\n"
            "|---|---|---|---|---|\n"
            + "".join(
                f"| {n} | passed | no | 3/3 | yes |\n" for n in failing
            )
        )
    else:
        rows = "\n_(no failures)_\n"
    path.write_text(
        f"# Batch verification record — t\n\n"
        f"record_writer: verification_record\n"
        f"batch: t\n"
        f"verified_head: {head}\n"
        f"verified_tree: {'c' * 40}\n"
        f"base_sha: {base_sha}\n"
        f"suite_invocation: python3 -m pytest -q\n"
        f"suite_failed: {len(failing)}\n"
        f"\n## At-base rerun\n{rows}",
        encoding="utf-8",
    )
    return path


def _run_attribution(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "verify_attribution.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )


class TestResolveRemeasureBase:
    """The validation function itself, before the CLI around it."""

    def test_legacy_record_only_is_unvalidated(self, tmp_path: Path) -> None:
        proj = _make_repo(tmp_path)
        base, sel, refusal = vr.resolve_remeasure_base(
            supplied=None, stored_sha="abc1234")
        assert refusal is None
        assert base == "abc1234"
        assert sel is not None
        assert sel.selection_mode == vr.BASE_SELECTION_UNVALIDATED
        assert "unvalidated" in sel.evidence
        assert sel.derivation_text == "unvalidated (record-only)"
        assert proj is not None  # silence the unused-binding linter

    def test_no_base_at_all_reports_nothing(self) -> None:
        base, sel, refusal = vr.resolve_remeasure_base(
            supplied=None, stored_sha=None)
        assert (base, sel, refusal) == (None, None, None)

    def test_stored_mismatch_refuses_with_both_shas(self, tmp_path: Path) -> None:
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        master = _make_master(tmp_path)
        supplied = vr.select_batch_base(proj, master, explicit_sha=shas[2])

        base, sel, refusal = vr.resolve_remeasure_base(
            supplied=supplied, stored_sha=shas[0])
        assert base is None and sel is None
        assert refusal is not None
        assert shas[0] in refusal and shas[2] in refusal, (
            f"a negative carries both SHAs: {refusal!r}"
        )
        assert "--base-sha" in refusal, (
            f"the refusal must carry a recovery command: {refusal!r}"
        )

    def test_stored_agreement_returns_the_supplied_selection(
            self, tmp_path: Path) -> None:
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        master = _make_master(tmp_path)
        supplied = vr.select_batch_base(proj, master, explicit_sha=shas[2])

        base, sel, refusal = vr.resolve_remeasure_base(
            supplied=supplied, stored_sha=shas[2][:12])
        assert refusal is None
        assert base == shas[2]
        assert sel is supplied

    def test_derived_frontmatter_conflict_refuses_without_an_explicit_choice(
            self, tmp_path: Path) -> None:
        """AC-2: actionable until an explicit operator choice resolves it."""
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        master = _make_master(tmp_path, declared_base=shas[3])
        supplied = vr.select_batch_base(proj, master)  # derived
        assert supplied.conflict is True

        base, sel, refusal = vr.resolve_remeasure_base(
            supplied=supplied, stored_sha=None)
        assert base is None and sel is None
        assert refusal is not None
        assert shas[0] in refusal and shas[3] in refusal
        assert "--base-sha" in refusal

    def test_explicit_choice_resolves_the_derived_conflict(
            self, tmp_path: Path) -> None:
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        master = _make_master(tmp_path, declared_base=shas[3])
        supplied = vr.select_batch_base(proj, master, explicit_sha=shas[2])
        assert supplied.conflict is True  # carried, not dropped

        base, sel, refusal = vr.resolve_remeasure_base(
            supplied=supplied, stored_sha=shas[2])
        assert refusal is None
        assert base == shas[2]
        assert sel is not None and sel.selection_mode == vr.BASE_SELECTION_EXPLICIT


class TestRemeasureRefusesBeforeTheSuite:
    """AC-3: a mismatch refuses before any suite subprocess is launched."""

    def test_mismatch_never_starts_the_suite(self, tmp_path: Path) -> None:
        """Production entry.  The suite's import writes a marker; a refusal
        before the suite leaves the marker absent.  That is the measurement —
        not the exit code, which a post-hoc check could also produce."""
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        _write_launch(proj)
        marker = tmp_path / "suite-ran.marker"
        _write_touch_suite(proj, marker)
        _git(proj, "add", "-A")
        _git(proj, "config", "user.email", "t@example.com")
        _git(proj, "config", "user.name", "t")
        _commit(proj, "feat: suite [plan:alpha#step-0]")

        rec = _stale_record(tmp_path / "r.md",
                            base_sha=shas[0], head=shas[-1])
        result = _run_attribution(
            "--project", str(proj),
            str(rec),
            "--remeasure-if-stale",
            "--base-sha", shas[2],
        )
        assert result.returncode != 0, (
            f"a provenance mismatch must refuse: {result.stdout}\n"
            f"{result.stderr}"
        )
        err = result.stderr
        assert shas[0] in err and shas[2] in err, (
            f"the refusal names both SHAs: {err!r}"
        )
        assert "--base-sha" in err, (
            f"the refusal carries a recovery command: {err!r}"
        )
        assert not marker.exists(), (
            "the suite subprocess ran despite the provenance mismatch"
        )

    def test_mismatch_refuses_before_verification_record_is_invoked(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The same refusal at the in-process boundary verify_attribution
        actually calls, so 'before the suite' is pinned at the call site."""
        import verify_attribution as va  # noqa: PLC0415

        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        rec = _stale_record(tmp_path / "r.md",
                            base_sha=shas[0], head=shas[-1])

        launched: list[list[str]] = []

        def _spy(args):
            launched.append(list(args))
            return 0

        monkeypatch.setattr(vr, "main", _spy)
        rc = va.main([
            "--project", str(proj),
            str(rec),
            "--remeasure-if-stale",
            "--base-sha", shas[2],
        ])
        assert rc != 0
        assert launched == [], (
            f"verification_record was invoked despite a mismatch: {launched}"
        )

    def test_derived_conflict_refuses_before_the_suite(
            self, tmp_path: Path) -> None:
        """AC-2 on the consumer: a derived/frontmatter mismatch is actionable
        and blocks the re-measure until an explicit choice is made."""
        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        _write_launch(proj)
        marker = tmp_path / "suite-ran.marker"
        _write_touch_suite(proj, marker)
        _git(proj, "config", "user.email", "t@example.com")
        _git(proj, "config", "user.name", "t")
        _git(proj, "add", "-A")
        _commit(proj, "feat: suite [plan:alpha#step-0]")

        master = _make_master(tmp_path, declared_base=shas[3])
        rec = _stale_record(tmp_path / "r.md",
                            base_sha=shas[0], head=shas[-1])
        result = _run_attribution(
            "--project", str(proj),
            str(rec),
            "--remeasure-if-stale",
            "--base-sha", "auto",
            "--master", str(master),
        )
        assert result.returncode != 0
        err = result.stderr
        assert shas[0] in err and shas[3] in err, (
            f"both SHAs of the conflict are named: {err!r}"
        )
        assert "--base-sha" in err
        assert not marker.exists(), (
            "the suite ran under a provenance conflict that had not been "
            "resolved by an explicit operator choice"
        )


class TestMatchingProvenancePreservesTheVerdict:
    """Negative control: agreement must not become a refusal."""

    def test_matching_base_launches_the_remeasure_and_keeps_the_red(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import verify_attribution as va  # noqa: PLC0415

        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        rec = _stale_record(
            tmp_path / "r.md", base_sha=shas[0], head=shas[-1],
            failing=["tests/test_x.py::test_x"],
        )

        launched: list[list[str]] = []

        def _spy(args):
            launched.append(list(args))
            return 0

        monkeypatch.setattr(vr, "main", _spy)
        rc = va.main([
            "--project", str(proj),
            str(rec),
            "--remeasure-if-stale",
            "--base-sha", shas[0],
        ])
        assert launched, "matching provenance must reach the re-measure"
        assert any("--run-suite" in a for a in launched)
        # The red is preserved: attribution still reports the attributed
        # regression rather than excusing it.
        assert rc != 0, (
            "a matching base must not turn an attributed red into a pass"
        )

    def test_matching_base_keeps_a_green_green(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import verify_attribution as va  # noqa: PLC0415

        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        rec = _stale_record(tmp_path / "r.md",
                            base_sha=shas[0], head=shas[-1])

        monkeypatch.setattr(vr, "main", lambda args: 0)
        rc = va.main([
            "--project", str(proj),
            str(rec),
            "--remeasure-if-stale",
            "--base-sha", shas[0],
        ])
        assert rc == 0, "a green record under a matching base stays green"


class TestLegacyRecordOnly:
    """AC-3: record-only use stays supported, and says it was not validated."""

    def test_record_only_remeasure_is_marked_unvalidated(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import verify_attribution as va  # noqa: PLC0415

        proj = _make_repo(tmp_path)
        shas = _shas(proj)
        rec = _stale_record(tmp_path / "r.md",
                            base_sha=shas[0], head=shas[-1])

        def _fake_remeasure(args):
            # What the writer would have done: mark the bare SHA explicit.
            text = rec.read_text(encoding="utf-8")
            rec.write_text(vr.inject_base_provenance(
                text, vr.select_batch_base(proj, None, explicit_sha=args[
                    args.index("--base-sha") + 1])),
                encoding="utf-8")
            return 0

        monkeypatch.setattr(vr, "main", _fake_remeasure)
        rc = va.main([
            "--project", str(proj),
            str(rec),
            "--remeasure-if-stale",
        ])
        assert rc == 0, "legacy record-only use must remain supported"
        text = rec.read_text(encoding="utf-8")
        assert "base_selection: unvalidated" in text, (
            f"a record-only base must not be promoted to an operator choice:\n"
            f"{text}"
        )
        assert "base_derivation: unvalidated (record-only)" in text
        assert "base_selection: explicit" not in text


class TestPostImplementationMarker:
    """AC-4: a marker that landed after the implementation cannot exonerate it.

    Hermetic CLI scenario, tiny temporary suite.  One failure, two bases, two
    results — and under a validated pre-batch base the regression stands.
    """

    NODE = "tests/test_x.py::test_x"

    def _scenario(self, tmp_path: Path) -> tuple[Path, str, str]:
        """c0 green → c1 pre-batch base → c2 breaks it → c3 post-impl marker.

        Returns ``(repo, pre_batch_base, post_impl_marker)``.
        """
        proj = tmp_path / "proj"
        proj.mkdir()
        _git(proj, "init", "-q")
        _git(proj, "config", "user.email", "t@example.com")
        _git(proj, "config", "user.name", "t")
        (proj / "tests").mkdir()
        (proj / "tests" / "test_x.py").write_text(
            "def test_x():\n    assert True\n", encoding="utf-8")
        _write_launch(proj)
        _git(proj, "add", "-A")
        _commit(proj, "chore: init")

        pre_batch = _commit(proj, "docs: pre-batch checkpoint")

        (proj / "tests" / "test_x.py").write_text(
            "def test_x():\n    assert False\n", encoding="utf-8")
        _git(proj, "add", "-A")
        _commit(proj, "feat: implementation [plan:alpha#step-0]")

        post_impl = _commit(
            proj, "chore: batch-verify marker [plan:batch-verify#step-0]")
        # One commit past the marker: measuring *at* the marker is then a
        # real comparison, not HEAD against itself (which the writer refuses).
        _commit(proj, "docs: later")
        return proj, pre_batch, post_impl

    def _measure(self, proj: Path, base_sha: str, rec: Path) -> str:
        """Production entry: ``verification_record.py --run-suite``.

        Tiny temporary suite — one test, one failure.  No full suite is run.
        """
        result = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "verification_record.py"),
             "--project", str(proj), "--record", str(rec),
             "--run-suite", "--base-sha", base_sha, "--scope", "full",
             "--ledger", "off"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180,
        )
        assert result.returncode == 0, (
            f"measure at {base_sha[:8]} failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        return rec.read_text(encoding="utf-8")

    @staticmethod
    def _at_base_cell(text: str, node: str) -> str:
        for line in text.splitlines():
            if line.startswith(f"| {node} |"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                return cells[1]
        raise AssertionError(f"no at-base row for {node} in:\n{text}")

    def test_a_post_implementation_marker_cannot_exonerate_the_regression(
            self, tmp_path: Path) -> None:
        proj, pre_batch, post_impl = self._scenario(tmp_path)
        master = _make_master(tmp_path, declared_base=pre_batch)

        # ── the AC: a validated pre-batch base ──────────────────────────
        validated = self._measure(proj, pre_batch, tmp_path / "validated.md")
        assert self._at_base_cell(validated, self.NODE) == "passed", (
            "under a validated pre-batch base the failure is a regression: "
            "it passed at base, so nothing here excuses it"
        )
        assert "suite_failed: 1" in validated
        assert "base_selection:" in validated and "unvalidated" not in validated, (
            f"the base must be validated provenance:\n{validated}"
        )

        # The record written under the validated base refuses to pass
        # attribution: the regression is measured, not excused.
        import verify_attribution as va  # noqa: PLC0415
        rec_path = tmp_path / "validated.md"
        with pytest.raises(va.VerificationError):
            va.verify_detailed(rec_path, project=proj)

        # ── negative control: the post-implementation marker ────────────
        # The same failure measured against the marker that landed *after*
        # the implementation looks pre-existing.  That is exactly the
        # exonerating reading the validated pre-batch base does not allow.
        marked = self._measure(proj, post_impl, tmp_path / "marked.md")
        assert self._at_base_cell(marked, self.NODE) == "failed", (
            "the post-implementation marker is the exonerating reading"
        )
        # …and the two results differ, so the base is what decided it.
        assert (self._at_base_cell(validated, self.NODE)
                != self._at_base_cell(marked, self.NODE))

        # The marker's exoneration cannot be adopted silently: re-measuring
        # under the validated base, against a record that carries the marker,
        # is a provenance conflict and refuses before any suite runs.
        marker_record = tmp_path / "marker-record.md"
        marker_record.write_text(
            f"# Batch verification record — t\n\n"
            f"record_writer: verification_record\n"
            f"batch: t\n"
            f"verified_head: {'d' * 40}\n"
            f"verified_tree: {'c' * 40}\n"
            f"base_sha: {post_impl}\n"
            f"suite_invocation: python3 -m pytest -q\n"
            f"suite_failed: 1\n\n"
            f"## At-base rerun\n\n"
            f"| node id | at base | in baseline_red | head reruns "
            f"| batch touched file |\n"
            f"|---|---|---|---|---|\n"
            f"| {self.NODE} | failed | no | 3/3 | yes |\n",
            encoding="utf-8",
        )
        result = _run_attribution(
            "--project", str(proj),
            str(marker_record),
            "--remeasure-if-stale",
            "--base-sha", pre_batch,
        )
        assert result.returncode != 0
        assert post_impl in result.stderr and pre_batch in result.stderr
        assert "--base-sha" in result.stderr


