"""Scoping degrades per module, never globally.

MEASURED 2026-09-16 on ilk-skills: compute_suite_scope returned
`mode=full, count=0, reason="no test file found for changed module
skills/ilk-loop/scripts/verification_record.py"` and the verification step ran
3102 tests in ~305s. That module's tests are test_verification_record_emission.py
and test_record_is_measured.py — neither is test_verification_record.py. ONE
naming mismatch discarded the selection already computed for every other changed
file.

Cost of the bug, measured the same day: 70 whole-suite invocations in one day on
one project, ~6h of test execution, because every pass of the fix loop paid for
a broad run that scoping should have avoided.

After: mode=scoped, 14 files, 172 tests, 18.6s.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verification_record as vr  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]):
    import subprocess
    repo = tmp_path / "proj"
    repo.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "h"),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True, env=env)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    for rel, body in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True, env=env)
    return repo, base


class TestPerModuleDegrade:
    def test_one_unmapped_module_does_not_discard_the_selection(self, tmp_path: Path) -> None:
        """The measured defect: a mapped module plus an unmapped one."""
        repo, base = _repo(tmp_path, {
            "src/alpha.py": "A = 1\n",
            "tests/test_alpha.py": "def test_a(): pass\n",
            "src/orphan.py": "B = 2\n",          # no test of any name
        })
        s = vr.compute_suite_scope(repo, base)
        assert s["mode"] == "scoped", s
        assert s["count"] >= 1, s
        assert "orphan" in s["reason"], (
            "an uncovered module must be NAMED, not silently omitted: " + s["reason"]
        )

    def test_prefixed_test_name_is_found(self, tmp_path: Path) -> None:
        """foo.py -> test_foo_emission.py, the exact shape that was missed."""
        repo, base = _repo(tmp_path, {
            "src/foo.py": "A = 1\n",
            "tests/test_foo_emission.py": "def test_a(): pass\n",
        })
        s = vr.compute_suite_scope(repo, base)
        assert s["mode"] == "scoped" and s["count"] == 1, s

    def test_an_importer_counts_as_coverage(self, tmp_path: Path) -> None:
        """A test that imports the module covers it, whatever it is called."""
        repo, base = _repo(tmp_path, {
            "src/widget.py": "A = 1\n",
            "tests/test_totally_different_name.py": "import widget\ndef test_a(): pass\n",
        })
        s = vr.compute_suite_scope(repo, base)
        assert s["mode"] == "scoped" and s["count"] == 1, s

    def test_nothing_mapped_at_all_still_widens(self, tmp_path: Path) -> None:
        """The legitimate full-suite case survives — this is not a weakening."""
        repo, base = _repo(tmp_path, {"src/orphan.py": "B = 2\n"})
        s = vr.compute_suite_scope(repo, base)
        assert s["mode"] == "full", s

    def test_global_change_still_widens(self, tmp_path: Path) -> None:
        """conftest/build config still forces full, unchanged."""
        repo, base = _repo(tmp_path, {
            "conftest.py": "x = 1\n",
            "src/alpha.py": "A = 1\n",
            "tests/test_alpha.py": "def test_a(): pass\n",
        })
        assert vr.compute_suite_scope(repo, base)["mode"] == "full"
