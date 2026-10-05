"""Notification isolation for scheduler-driven autoplan."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


_SELF_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    if str(_SELF_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SELF_SCRIPTS))
    import autoplan

    return autoplan


def _world(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_root = tmp_path / "ilk-data"
    (data_root / "projects").mkdir(parents=True)
    (data_root / "autoplan").mkdir()
    (data_root / "audit").mkdir()

    toolkit = tmp_path / "toolkit"
    (toolkit / "commands").mkdir(parents=True)
    (toolkit / "commands" / "ilk-plan.md").write_text(
        "# ilk-plan\n", encoding="utf-8"
    )
    (toolkit / ".ilk-launch.json").write_text(
        json.dumps({"autoplan": {"enabled": True}}) + "\n",
        encoding="utf-8",
    )
    (data_root / "plans").mkdir()

    project_dir = data_root / "projects" / "test-project"
    launcher = project_dir / "runtime" / "launcher"
    launcher.mkdir(parents=True)
    (launcher / "last-launch.json").write_text(
        json.dumps({"project_path": str(toolkit)}) + "\n",
        encoding="utf-8",
    )
    (data_root / "autoplan" / "state.json").write_text(
        json.dumps({"idle_cycles": 6}) + "\n", encoding="utf-8"
    )

    manager_home = tmp_path / ".claude-worker-xxx"
    manager_home.mkdir()
    return data_root, toolkit, manager_home


def test_fixture_refusal_does_not_call_native_notifier(tmp_path: Path) -> None:
    mod = _load_module()
    data_root, _toolkit, manager_home = _world(tmp_path)

    def fail_notifier(event: str, detail: str) -> None:
        raise AssertionError(
            "fixture refusal reached notifier "
            f"(event={event!r}, detail={detail!r})"
        )

    result = mod.tick(
        data_root=data_root,
        manager_home=str(manager_home),
        notifier_fn=fail_notifier,
    )
    assert result["decision"] == "bad-home"


def test_production_refusal_builds_native_notification_identity(tmp_path: Path) -> None:
    mod = _load_module()
    data_root, _toolkit, manager_home = _world(tmp_path)
    calls: list[list[str]] = []
    original_run = subprocess.run

    def intercept_notify(cmd, *args, **kwargs):
        if isinstance(cmd, list) and any("ilk_notify.py" in str(x) for x in cmd):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return original_run(cmd, *args, **kwargs)

    with patch("subprocess.run", side_effect=intercept_notify):
        result = mod.tick(data_root=data_root, manager_home=str(manager_home))
    assert result["decision"] == "bad-home"

    assert len(calls) == 1
    notify_cmd = calls[0]
    assert notify_cmd[notify_cmd.index("--project") + 1] == "ilk-skills"
    assert notify_cmd[notify_cmd.index("--event") + 1] == "blocked"
    assert "bad-home" in notify_cmd[notify_cmd.index("--detail") + 1]
