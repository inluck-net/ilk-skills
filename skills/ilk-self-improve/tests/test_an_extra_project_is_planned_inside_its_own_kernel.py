"""Pin the track B seam: autoplan may plan for a project other than the toolkit,
but only inside that project's own kernel, and without writing its backlog.

Chad, 2026-10-10, option (b): move the tunable part of autoplan out of the
kernel (autoplan_projects.py: which extra projects, how their rows load) and
keep the rails in it (autoplan.py / autoplan_rails.py).  gh-resolve-a1 named
the three blockers: toolkit-only discovery, the hard-coded backlog dir, and a
fixed source list.

AC-1: no toolkit and no extra project -> not-enabled (unchanged).
AC-2: an extra project's candidate, with the project's declared source, is
      selected.
AC-3: an extra project whose own .ilk-launch.json names no kernel_file is
      skipped and audited, never planned (fail closed).
AC-4: a kernel_file outside the project's repo is refused the same way.
AC-5: a candidate naming the extra project's kernel path is escalated: blocked
      in autoplan's overlay, never selected, and the project's own backlog is
      byte-for-byte unchanged.
AC-6: two failed attempts recorded through the overlay make the candidate
      ineligible.
AC-7: the toolkit comes first when both have a candidate.
AC-8: an auto-planned master's priority sorts below a null-priority master.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SELF = Path(__file__).resolve().parents[1] / "scripts"
_LOOP = _SELF.parent.parent / "ilk-loop" / "scripts"
_WD = _SELF.parent.parent / "ilk-watchdog" / "scripts"
_FB = _SELF.parent.parent / "ilk-feedback" / "scripts"
for d in (_LOOP, _SELF, _WD, _FB):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import autoplan  # noqa: E402
import autoplan_projects  # noqa: E402
import autoplan_rails  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()


def _cand(cid: str, *, source: str = "gh-resolve-issue", gap: str = "x is slow") -> dict:
    return {"id": cid, "title": f"title {cid} is long enough", "gap": gap,
            "source": source, "status": "open", "seen_count": 1,
            "first_seen": NOW, "last_seen": NOW, "relations": {},
            "evidence": {}, "proposed_fix": "fix it", "kind": "bug"}


def _data_root(tmp_path: Path) -> Path:
    root = tmp_path / "ilk-data"
    for d in ("projects", "autoplan", "audit"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "autoplan" / "state.json").write_text(json.dumps({"idle_cycles": 3}))
    return root


def _extra(tmp_path: Path, rows: list[dict], *, kernel_file: str | None = "docs/loop/safety-kernel.json",
           kernel_paths: tuple[str, ...] = ("src/guard.py",)) -> dict:
    repo = tmp_path / "consumer"
    (repo / "docs" / "loop").mkdir(parents=True, exist_ok=True)
    auto = {"enabled": True}
    if kernel_file is not None:
        auto["kernel_file"] = kernel_file
    (repo / ".ilk-launch.json").write_text(json.dumps({"autoplan": auto}))
    (repo / "docs" / "loop" / "safety-kernel.json").write_text(json.dumps(
        {"rules": [], "kernel": [{"path": p} for p in kernel_paths], "kernel_basenames": []}))
    backlog = tmp_path / "consumer-backlog"
    backlog.mkdir(exist_ok=True)
    (backlog / "candidates.json").write_text(json.dumps(rows, indent=2) + "\n")
    # The kernel resolves the repo from the project's data dir, not from the
    # module's dict, so the data dir must exist (last-launch.json).
    pd = tmp_path / "ilk-data" / "projects" / "consumer-abc1234" / "runtime" / "launcher"
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "last-launch.json").write_text(json.dumps({"project_path": str(repo)}))
    return {"key": "consumer-abc1234", "repo": "/nowhere/ignored", "backlog_mode": "dir",
            "backlog_path": str(backlog), "sources": ["gh-resolve-issue"]}


def _tick(root: Path) -> dict:
    return autoplan.tick(data_root=root, manager_home="", dry_run=True)


def _audit_rows(root: Path, event: str) -> list[dict]:
    out = []
    for f in root.rglob("*.jsonl"):
        for line in f.read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("event") == event or d.get("kind") == event:
                out.append(d)
    return out


def test_nothing_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(autoplan_projects, "discover_extra", lambda root: [])
    assert _tick(_data_root(tmp_path))["decision"] == "not-enabled"


def test_an_extra_projects_candidate_is_selected(tmp_path, monkeypatch) -> None:
    proj = _extra(tmp_path, [_cand("gh-1")])
    monkeypatch.setattr(autoplan_projects, "discover_extra", lambda root: [proj])
    r = _tick(_data_root(tmp_path))
    assert r["decision"] == "started" and r["candidate"] == "gh-1", r


def test_no_kernel_file_is_skipped(tmp_path, monkeypatch) -> None:
    proj = _extra(tmp_path, [_cand("gh-1")], kernel_file=None)
    monkeypatch.setattr(autoplan_projects, "discover_extra", lambda root: [proj])
    root = _data_root(tmp_path)
    r = _tick(root)
    assert r["decision"] == "no-candidate", r
    assert any(a.get("reason") == "no-project-kernel" for a in _audit_rows(root, "autoplan-refused"))


def test_a_kernel_file_outside_the_repo_is_refused(tmp_path, monkeypatch) -> None:
    proj = _extra(tmp_path, [_cand("gh-1")], kernel_file="../elsewhere.json")
    (tmp_path / "elsewhere.json").write_text(json.dumps({"kernel": []}))
    monkeypatch.setattr(autoplan_projects, "discover_extra", lambda root: [proj])
    assert _tick(_data_root(tmp_path))["decision"] == "no-candidate"


def test_a_kernel_candidate_is_escalated_in_the_overlay(tmp_path, monkeypatch) -> None:
    proj = _extra(tmp_path, [_cand("gh-k", gap="src/guard.py is wrong"), _cand("gh-ok")])
    before = (Path(proj["backlog_path"]) / "candidates.json").read_bytes()
    monkeypatch.setattr(autoplan_projects, "discover_extra", lambda root: [proj])
    root = _data_root(tmp_path)
    r = autoplan.tick(data_root=root, manager_home="", dry_run=False,
                      popen_fn=lambda *a, **k: type("P", (), {"pid": 1})())
    assert r.get("candidate") == "gh-ok", r
    overlay = json.loads((root / "autoplan" / "projects" / proj["key"] / "overlay.json").read_text())
    assert overlay["gh-k"]["relations"]["autoplan_blocked"] is True
    assert (Path(proj["backlog_path"]) / "candidates.json").read_bytes() == before


def test_two_attempts_through_the_overlay_end_eligibility(tmp_path, monkeypatch) -> None:
    proj = _extra(tmp_path, [_cand("gh-1")])
    monkeypatch.setattr(autoplan_projects, "discover_extra", lambda root: [proj])
    root = _data_root(tmp_path)
    autoplan._increment_attempts(root, "gh-1", project_key=proj["key"])
    autoplan._increment_attempts(root, "gh-1", project_key=proj["key"])
    full = {**proj, "toolkit": False}
    rows = autoplan._load_project_entries(root, full)
    assert rows[0]["relations"]["autoplan_attempts"] == 2
    assert autoplan_rails.rank(rows, sources=proj["sources"]) == []


def test_the_toolkit_comes_first(tmp_path, monkeypatch) -> None:
    proj = _extra(tmp_path, [_cand("gh-1")])
    monkeypatch.setattr(autoplan_projects, "discover_extra", lambda root: [proj])
    root = _data_root(tmp_path)
    tk = tmp_path / "toolkit"
    (tk / "commands").mkdir(parents=True)
    (tk / "commands" / "ilk-plan.md").write_text("x")
    (tk / ".ilk-launch.json").write_text(json.dumps({"autoplan": {"enabled": True}}))
    pd = root / "projects" / "toolkit-0000000"
    (pd / "runtime" / "launcher").mkdir(parents=True)
    (pd / "runtime" / "launcher" / "last-launch.json").write_text(json.dumps({"project_path": str(tk)}))
    bl = root / "ilk-skills-improvements"
    bl.mkdir()
    (bl / "candidates.json").write_text(json.dumps([_cand("ilk-1", source="triage")]))
    r = _tick(root)
    assert r["decision"] == "started" and r["candidate"] == "ilk-1", r


def test_auto_planned_priority_sorts_below_null() -> None:
    import promote_next_master as pm
    assert pm._prio({"priority": str(autoplan_rails.AUTO_PLANNED_PRIORITY)}) < pm._prio({"priority": "null"})
