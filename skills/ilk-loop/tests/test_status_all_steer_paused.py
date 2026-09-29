"""status_all --json must say when a runner is held by ``runtime/steer/pause.flag``.

Why this exists: the runner idles on the flag with its pid alive, so the panel
row carried the running ``*`` icon — and, because a paused run never writes its
first ``iter-NN.log``, no heartbeat either.  Observed 2026-09-29: a verify
sub-plan's runner sat 30 minutes on a stale flag left by the previous night's
design-review pause, and the row read ``* +4 ilk-skills state-ownership 9/9
state-ownership-verify 0/2`` — indistinguishable from a run just starting.
The only reader of the flag was the runner itself.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))

import status_all  # noqa: E402


def _make_project(root: Path, key: str = "proj") -> Path:
    proj = root / "projects" / key
    (proj / "plans").mkdir(parents=True, exist_ok=True)
    launcher = proj / "runtime" / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    (launcher / "last-exit.json").write_text(
        json.dumps({"pid": os.getpid(), "state": "running"}), encoding="utf-8"
    )
    return proj


def _resolve(root: Path, proj: Path, monkeypatch) -> dict:
    monkeypatch.setenv("ILK_DATA_HOME", str(root))
    monkeypatch.delenv("ILK_DATA_DIR", raising=False)
    monkeypatch.setattr(status_all, "ilk_pid_alive", lambda pid: True)
    return status_all.resolve_project_status(proj)


def test_pause_flag_is_reported_with_its_reason(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    steer = proj / "runtime" / "steer"
    steer.mkdir(parents=True)
    (steer / "pause.flag").write_text(
        "set by operator: design review before more patches\nsecond line\n",
        encoding="utf-8",
    )
    entry = _resolve(tmp_path, proj, monkeypatch)
    assert entry["steer_paused"] is True
    assert entry["steer_paused_reason"] == (
        "set by operator: design review before more patches"
    )


def test_empty_pause_flag_still_pauses(tmp_path, monkeypatch):
    """The runner tests presence only — an empty flag pauses, so it must show."""
    proj = _make_project(tmp_path)
    steer = proj / "runtime" / "steer"
    steer.mkdir(parents=True)
    (steer / "pause.flag").write_text("", encoding="utf-8")
    entry = _resolve(tmp_path, proj, monkeypatch)
    assert entry["steer_paused"] is True
    assert entry["steer_paused_reason"] == ""


def test_no_pause_flag(tmp_path, monkeypatch):
    proj = _make_project(tmp_path)
    entry = _resolve(tmp_path, proj, monkeypatch)
    assert entry["steer_paused"] is False
    assert entry["steer_paused_reason"] == ""
