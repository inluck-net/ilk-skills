"""status_all builds the host-wide blocks once per run, not once per project.

``roles`` and ``providers`` describe the host (the role registry and the
CCSwitch store), not a project.  ``_providers_block`` spawns
``ccswitch_import.py list``, ~35ms a call; built inside
``resolve_project_status`` it ran once per project dir.  Measured 2026-09-28
on chad-mbp: 118 project dirs, 118 spawns, 3.69s of a 4.18s run, refreshed
every 10s by the xbar plugin — ~40% of a core, 24/7.  Caching per run took
the same run to 0.33s with identical JSON.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))

import status_all  # noqa: E402


def _run_main(monkeypatch, tmp_path: Path, capsys, n_projects: int):
    data = tmp_path / "data"
    for i in range(n_projects):
        (data / "projects" / f"proj-{i}" / "plans").mkdir(parents=True)
    monkeypatch.setenv("ILK_DATA_HOME", str(data))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)

    spawns = []
    real_run = subprocess.run

    def counting_run(argv, *a, **kw):
        if any("ccswitch_import" in str(x) for x in argv):
            spawns.append(argv)
            return subprocess.CompletedProcess(
                argv, 0,
                stdout=json.dumps([{"id": "p1", "name": "P1", "model": "m",
                                    "base_url": "https://api.example.com/x"}]),
                stderr="")
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", counting_run)
    monkeypatch.setattr(sys, "argv", ["status_all.py", "--json"])
    assert status_all.main() == 0
    return spawns, json.loads(capsys.readouterr().out)


def test_ccswitch_spawned_once_per_run_not_per_project(monkeypatch, tmp_path, capsys):
    spawns, entries = _run_main(monkeypatch, tmp_path, capsys, n_projects=5)
    assert len(entries) == 5
    assert len(spawns) == 1, (
        f"ccswitch_import spawned {len(spawns)}x for 5 projects; the providers "
        "block is host-wide and must be built once per status_all run"
    )


def test_every_entry_still_carries_the_shared_blocks(monkeypatch, tmp_path, capsys):
    # render_xbar reads providers from the first entry that has them
    # (render_xbar.py:143); the schema stays per-entry either way.
    _, entries = _run_main(monkeypatch, tmp_path, capsys, n_projects=3)
    for e in entries:
        assert e["providers"] == [{"id": "p1", "name": "P1", "model": "m",
                                   "base_url_host": "api.example.com"}]
        assert "roles" in e
