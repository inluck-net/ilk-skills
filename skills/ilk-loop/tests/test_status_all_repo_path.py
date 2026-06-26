"""Unit tests for status_all._resolve_repo_path.

The xbar/tray "Start now" action dispatches via ilk-run.*, which resolves a
project root from the SOURCE repo path — NOT the ~/.ilk-data data dir.
status_all must surface that repo path as `repo_path` so the renderers can
route the action correctly. Resolution order mirrors
scheduler_scan.resolve_repo_path:

  1. <data>/runtime/launcher/last-launch.json -> project_path
  2. <skill-root>/ilk-launcher/projects.json registry (key match)
  3. None when neither resolves.

These exercise branch (1) and (3) deterministically with a tmp data dir;
branch (2) depends on the real on-disk registry and is covered by the live
scheduler_scan tests.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Import the function under test directly (no subprocess) so a missing
# host skill root / empty projects dir can't make the test flaky.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
from status_all import _resolve_repo_path  # noqa: E402


def _write_last_launch(project_dir: Path, project_path: str) -> None:
    ll = project_dir / "runtime" / "launcher" / "last-launch.json"
    ll.parent.mkdir(parents=True, exist_ok=True)
    ll.write_text(json.dumps({"project_path": project_path}), encoding="utf-8")


def test_resolves_from_last_launch(tmp_path):
    """last-launch.json project_path is returned verbatim."""
    proj = tmp_path / "users-me-proj"
    proj.mkdir()
    repo = "/home/me/Projects/proj"
    _write_last_launch(proj, repo)
    assert _resolve_repo_path(proj, proj.name) == repo


def test_last_launch_takes_precedence_over_registry(tmp_path):
    """Even if a key could match the registry, last-launch.json wins."""
    proj = tmp_path / "anything"
    proj.mkdir()
    repo = "/home/me/Projects/explicit"
    _write_last_launch(proj, repo)
    assert _resolve_repo_path(proj, proj.name) == repo


def test_none_when_unresolved(tmp_path):
    """No last-launch.json and a key that can't match the registry -> None."""
    proj = tmp_path / "no-such-key-zzz-unresolvable"
    proj.mkdir()
    assert _resolve_repo_path(proj, proj.name) is None


def test_tolerates_malformed_last_launch(tmp_path):
    """Corrupt JSON doesn't raise — falls through to None (or registry)."""
    proj = tmp_path / "users-me-bad"
    proj.mkdir()
    ll = proj / "runtime" / "launcher" / "last-launch.json"
    ll.parent.mkdir(parents=True, exist_ok=True)
    ll.write_text("{not json", encoding="utf-8")
    # Must not raise; with an unmatchable key the result is None.
    assert _resolve_repo_path(proj, proj.name) is None


def test_repo_path_present_in_full_entry(tmp_path):
    """resolve_project_status surfaces repo_path in its dict (schema check)."""
    from status_all import resolve_project_status

    proj = tmp_path / "users-me-schema"
    (proj / "plans").mkdir(parents=True)
    _write_last_launch(proj, "/home/me/Projects/schema")
    entry = resolve_project_status(proj)
    assert "repo_path" in entry
    assert entry["repo_path"] == "/home/me/Projects/schema"
