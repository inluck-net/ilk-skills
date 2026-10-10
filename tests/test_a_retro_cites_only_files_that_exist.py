r"""A retro cites only files that exist.

Sub-plan: a-retro-cites-only-files-that-exist (step 0 of 2).

A retro's "How it is held" section is the record of what enforces a rule.  A
citation of a test file that is not in the tree is an unbacked claim: it reads
as though a guard is in place, and nothing checks that.

Scope of the check (binding design):

* every ``docs/retros/*.md``; split on ``##``/``###`` headings; only sections
  whose heading matches ``(?i)how it is held|held by``;
* in those sections, backticked tokens containing ``/`` and ending in a file
  extension or ``/`` — after stripping a trailing ``:<line>``/``::<test>``;
* a token carrying ``planned:`` (inside the backticks, or immediately before
  them) is skipped: the claim is declared unbuilt, not claimed held;
* every other such token must be in ``git ls-files``, or be a directory prefix
  of a tracked path.

AC-1 (red control, step 0): at base the 10-07 retro named
``tests/invariants/test_a_batch_runs_one_suite.py``, which is not built.  That
param was xfail-pinned (strict) so the gate stayed green while the claim was
unbacked — and non-vacuous, so the gate would go red the moment that citation
stopped being false.
AC-2: after step 1 every retro is green, including the 10-10 one — the 10-07
citation is now ``planned:`` and the pin is gone.
AC-3: the checker over a tmp markdown string — an existing path passes, a
missing path is reported, a ``planned:`` path is skipped.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_RETROS_DIR = _REPO / "docs" / "retros"

_HEADING_RE = re.compile(r"^#{2,3}[ \t]+(.*)$", re.M)
_HELD_HEADING_RE = re.compile(r"(?i)how it is held|held by")
_TOKEN_RE = re.compile(r"`([^`]+)`")
# ``foo.py:4961`` (a line anchor) or ``foo.py::test_bar`` (a node id).
_ANCHOR_SUFFIX_RE = re.compile(r"(?:::\w+|:\d+)$")
_PATHISH_RE = re.compile(r"\.\w+$")


def _is_pathish(token: str) -> bool:
    """A citation of a file or a directory, not an inline name or a flag."""
    return "/" in token and (bool(_PATHISH_RE.search(token)) or token.endswith("/"))


def held_citations(text: str) -> list[tuple[str, str, str]]:
    """``(section, path, raw)`` for every path citation in a held-by section."""
    heads = list(_HEADING_RE.finditer(text))
    out: list[tuple[str, str, str]] = []
    for i, head in enumerate(heads):
        if not _HELD_HEADING_RE.search(head.group(1)):
            continue
        start = head.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[start:end]
        for match in _TOKEN_RE.finditer(body):
            raw = match.group(1).strip()
            path = _ANCHOR_SUFFIX_RE.sub("", raw).strip()
            if not path or not _is_pathish(path):
                continue
            # ``planned:`` inside the backticks, or the word immediately before.
            if path.startswith("planned:"):
                continue
            if body[: match.start()].rstrip().endswith("planned:"):
                continue
            out.append((head.group(1).strip(), path, raw))
    return out


def missing_citations(
    text: str, known: "frozenset[str] | set[str]"
) -> list[tuple[str, str]]:
    """Held-by citations whose path is neither tracked nor a tracked prefix."""
    missing: list[tuple[str, str]] = []
    for section, path, _raw in held_citations(text):
        probe = path.rstrip("/")
        if any(k == probe or k.startswith(probe + "/") for k in known):
            continue
        missing.append((section, path))
    return missing


def _report(retro_name: str, missing: list[tuple[str, str]]) -> str:
    """Failure text naming the retro, the section, and each missing path."""
    return (
        f"{retro_name}: a retro cites files that do not exist.\n"
        + "\n".join(f"  [{section}] {path}" for section, path in missing)
    )


def _tracked() -> frozenset[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO,
        check=True,
        capture_output=True,
    )
    return frozenset(
        chunk.decode("utf-8", "replace")
        for chunk in proc.stdout.split(b"\0")
        if chunk
    )


@pytest.fixture(scope="session")
def tracked() -> frozenset[str]:
    return _tracked()


def _retro_params():
    for path in sorted(_RETROS_DIR.glob("*.md")):
        yield pytest.param(path.name, id=path.stem)


@pytest.mark.parametrize("retro_name", _retro_params())
def test_a_retro_cites_only_files_that_exist(retro_name: str, tracked) -> None:
    text = (_RETROS_DIR / retro_name).read_text(encoding="utf-8")
    missing = missing_citations(text, tracked)
    assert not missing, _report(retro_name, missing)


# ── AC-3: the checker over a tmp markdown string ────────────────────────────


_HEADING = "## How it is held, not just stated"


def _md(*items: str) -> str:
    return _HEADING + "\n\n" + "\n\n".join(items) + "\n"


def test_an_existing_path_passes() -> None:
    text = _md("Held by `tests/conftest.py`.")
    assert missing_citations(text, frozenset({"tests/conftest.py"})) == []


def test_a_missing_path_is_reported() -> None:
    text = _md("Held by `tests/invariants/test_a_batch_runs_one_suite.py`.")
    missing = missing_citations(text, frozenset())
    assert missing == [
        (
            "How it is held, not just stated",
            "tests/invariants/test_a_batch_runs_one_suite.py",
        ),
    ]
    report = _report("retro-x.md", missing)
    assert "retro-x.md" in report
    assert "How it is held" in report
    assert "tests/invariants/test_a_batch_runs_one_suite.py" in report


def test_a_planned_path_passes() -> None:
    text = _md(
        "Held by `planned:tests/invariants/test_a_batch_runs_one_suite.py`.",
        "Also planned: `tests/invariants/test_a_batch_runs_one_suite.py`.",
    )
    assert missing_citations(text, frozenset()) == []


def test_a_directory_citation_is_satisfied_by_a_tracked_prefix() -> None:
    text = _md("Held by `tests/invariants/`.")
    known = frozenset({"tests/invariants/test_a_red_gate_blocks_a_ship.py"})
    assert missing_citations(text, known) == []


def test_a_line_anchor_is_stripped_before_the_lookup() -> None:
    text = _md(
        "I8a fails on `tests/conftest.py:4961`; I8b on `tests/conftest.py::test_x`."
    )
    assert missing_citations(text, frozenset({"tests/conftest.py"})) == []


def test_a_citation_outside_a_held_section_is_ignored() -> None:
    text = "## What kept happening\n\nSee `docs/plans/nope/missing.py`.\n"
    assert missing_citations(text, frozenset()) == []
