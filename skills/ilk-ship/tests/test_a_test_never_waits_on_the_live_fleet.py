"""Pin that a test never waits on the live fleet probe.

Sub-plan: a-test-never-waits-on-the-live-fleet, step 0 (pins) + step 1 (impl).

Under pytest, if ``ILK_TEST_NO_LIVE_FLEET=1`` is set (autouse conftest
fixture) and no ``quiet_probe`` was injected, the code must raise
``RuntimeError`` immediately instead of calling the real fleet probe
(``_live_loops_local`` / ``_live_loops_remote``) and waiting up to 90 min.

The four acceptance criteria:
  AC-1  _ssh_deploy with no quiet_probe raises RuntimeError (< 1 s).
  AC-2  _ssh_deploy with quiet_probe=lambda: [] does NOT raise.
  AC-3  With env var deleted and a probe stub patched in, the default path
        runs (production behaviour unchanged).
  AC-4  The env var is set inside an ordinary test (proves conftest autouse).
"""
from __future__ import annotations

import os
import time
import sys
from pathlib import Path

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_SHIP_SCRIPTS = _HERE.parent / "scripts"
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"


class TestNoLiveFleetProbe:
    """Under pytest the live fleet probe must never run."""

    @pytest.mark.xfail(
        strict=True,
        reason="a test can reach the live fleet probe and wait",
    )
    def test_ssh_deploy_without_probe_raises_immediately(self, tmp_path: Path) -> None:
        """AC-1: _ssh_deploy with no quiet_probe raises RuntimeError in < 1 s."""
        sys.path.insert(0, str(_SHIP_SCRIPTS))
        sys.path.insert(0, str(_LOOP_SCRIPTS))
        import release_train

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        def _fake_ssh_runner(host: str, cmd: list, timeout: int = 120) -> dict:
            return {"rc": 255, "stdout": "", "stderr": "connection refused"}

        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="a test reached the live fleet probe"):
            release_train._ssh_deploy(
                project, "v0.0.1", data_dir,
                host="rezmac",
                ssh_runner=_fake_ssh_runner,
                # No quiet_probe — the default path must NOT reach the live fleet
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"Took {elapsed:.1f}s — must fail fast, not wait"

    def test_ssh_deploy_with_probe_does_not_raise(self, tmp_path: Path) -> None:
        """AC-2: _ssh_deploy with quiet_probe=lambda: [] does NOT raise RuntimeError."""
        sys.path.insert(0, str(_SHIP_SCRIPTS))
        sys.path.insert(0, str(_LOOP_SCRIPTS))
        import release_train

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        def _fake_ssh_runner(host: str, cmd: list, timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            if "ilk_release.py" in cmd_str:
                return {"rc": 0, "stdout": "extracted", "stderr": ""}
            if "bounce_daemons" in cmd_str:
                return {"rc": 1, "stdout": "", "stderr": ""}
            if "host_deploy_status" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            return {"rc": 0, "stdout": "", "stderr": ""}

        # Must NOT raise — injected probe bypasses the live fleet guard
        result = release_train._ssh_deploy(
            project, "v0.0.1", data_dir,
            host="rezmac",
            ssh_runner=_fake_ssh_runner,
            quiet_probe=lambda: [],  # fleet is quiet immediately
            settle_deadline_sec=0.1,
            settle_poll_interval_sec=0.05,
        )
        assert result["tag"] == "v0.0.1"

    def test_default_path_runs_with_env_deleted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-3: With env var deleted and a probe stub, the default path runs."""
        sys.path.insert(0, str(_SHIP_SCRIPTS))
        sys.path.insert(0, str(_LOOP_SCRIPTS))
        import release_train

        monkeypatch.delenv("ILK_TEST_NO_LIVE_FLEET", raising=False)

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        def _fake_ssh_runner(host: str, cmd: list, timeout: int = 120) -> dict:
            cmd_str = " ".join(str(c) for c in cmd)
            if "pgrep" in cmd_str:
                return {"rc": 1, "stdout": "", "stderr": ""}  # no loops
            if "ilk_release.py" in cmd_str:
                return {"rc": 0, "stdout": "extracted", "stderr": ""}
            if "bounce_daemons" in cmd_str:
                return {"rc": 1, "stdout": "", "stderr": ""}
            if "host_deploy_status" in cmd_str:
                return {"rc": 0, "stdout": "ok", "stderr": ""}
            return {"rc": 0, "stdout": "", "stderr": ""}

        # Patch the default probe so it returns [] (no real pgrep)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(release_train, "_live_loops_remote", lambda run, host: [])
            result = release_train._ssh_deploy(
                project, "v0.0.1", data_dir,
                host="rezmac",
                ssh_runner=_fake_ssh_runner,
                settle_deadline_sec=0.1,
                settle_poll_interval_sec=0.05,
            )
        assert result["tag"] == "v0.0.1"

    def test_env_var_is_set_by_conftest(self) -> None:
        """AC-4: ILK_TEST_NO_LIVE_FLEET is set inside a test (conftest autouse)."""
        assert os.environ.get("ILK_TEST_NO_LIVE_FLEET") == "1", (
            "conftest autouse fixture must set ILK_TEST_NO_LIVE_FLEET=1"
        )