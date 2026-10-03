"""Pin: every invariant test is the catcher of at least one catalog mutation.

Static check — no subprocess, no runner.  Validates ``mutations.json``
against the working tree.

AC-8: ``xfail(strict=True)`` until step 1 adds the catalog rows.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CATALOG = Path(__file__).resolve().parent / "mutations.json"
_SELF = Path(__file__).resolve().name
# Golden harness pins are not invariant catchers.
_EXCLUDE = {_SELF, "conftest.py"}


def _collect_test_files() -> set[str]:
    """Return the set of test_*.py filenames in this directory."""
    return {
        f.name for f in Path(__file__).resolve().parent.iterdir()
        if f.name.startswith("test_") and f.name.endswith(".py")
        and f.name not in _EXCLUDE
    }


def _load_catalog() -> dict:
    return json.loads(_CATALOG.read_text(encoding="utf-8"))


def _git_ls_files() -> set[str]:
    """All tracked files in the repo."""
    r = subprocess.run(
        ["git", "ls-files"], cwd=str(_REPO),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return set(r.stdout.splitlines())


@pytest.mark.xfail(
    strict=True,
    reason="catalog has only sub-plan 1's seed row; step 1 adds the rest",
)
def test_every_invariant_is_a_catcher() -> None:
    """Every test_*.py in this dir (except self) must be a catcher in the catalog."""
    catalog = _load_catalog()
    test_files = _collect_test_files()

    # Collect catcher test files from catalog.
    catcher_files: set[str] = set()
    for m in catalog["mutations"]:
        argv = m["catcher"]["argv"]
        # Find the pytest target — an arg that looks like a path or ::node.
        for arg in argv:
            if arg.startswith("tests/invariants/"):
                fname = arg.split("::")[0].rsplit("/", 1)[-1]
                catcher_files.add(fname)
            elif "/" not in arg and arg.startswith("test_"):
                catcher_files.add(arg)

    missing = test_files - catcher_files
    assert not missing, (
        f"these invariant tests are not catchers of any catalog mutation: "
        f"{sorted(missing)}.  Each must guard a rail."
    )


def test_every_mutation_is_not_stale() -> None:
    """Every mutation's pattern must match at least min_matches in the tree."""
    catalog = _load_catalog()
    tracked = _git_ls_files()

    for m in catalog["mutations"]:
        target = m["file"]
        assert target in tracked, (
            f"mutation {m['id']!r}: file {target!r} not tracked"
        )
        content = (_REPO / target).read_text(encoding="utf-8")
        pattern = m["pattern"]
        min_matches = m.get("min_matches", 1)
        n = len(re.findall(pattern, content, flags=re.MULTILINE))
        assert n >= min_matches, (
            f"mutation {m['id']!r}: pattern matches {n} times in {target}, "
            f"need >= {min_matches}"
        )


def test_mutation_ids_are_unique() -> None:
    """No two mutations share the same id."""
    catalog = _load_catalog()
    ids = [m["id"] for m in catalog["mutations"]]
    dupes = [i for i in ids if ids.count(i) > 1]
    assert not dupes, f"duplicate mutation ids: {sorted(set(dupes))}"