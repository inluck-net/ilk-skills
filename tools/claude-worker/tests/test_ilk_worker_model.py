"""Tests for ilk-worker-model — the one-command worker model switch.

Hermetic, CLI-level fixtures per the sub-plan's AC1-AC3: tmp HOME standing
in for the worker main home + one slot, a fixture role registry whose
manager role holds the target env block, a fake ``claude`` on PATH that
reports a canned model (the probe's assert logic is what is under test),
and fake live watchdog/runner processes that write a marker on TERM so
the stop ORDER (watchdog before runner — the retro's invariant) is
assertable from marker mtimes.

Red-first: ``tools/claude-worker/ilk-worker-model`` does not exist yet;
every test here fails until step 1 implements it.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="bash CLI fixture; a .ps1 twin would cover Windows"
)

TOOLS_DIR = Path(__file__).resolve().parent.parent  # tools/claude-worker
TOOL = TOOLS_DIR / "ilk-worker-model"

MODEL_BEFORE_MAIN = "model-A"
MODEL_BEFORE_SLOT = "model-A2"
MODEL_TARGET = "glm-5.3"
BASE_URL_BEFORE = "https://a.example/api"
BASE_URL_TARGET = "https://glm.example/api"
TOKEN_TARGET = "tok-glm"


def _write_settings(home: Path, model: str, base_url: str,
                    token: str = "tok-before") -> None:
    """A worker home's settings.json: an env block plus unrelated keys that
    a rewrite must preserve."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls:*)"]},
        "env": {
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_AUTH_TOKEN": token,
        },
        "includeCoAuthoredBy": False,
    }, indent=2), encoding="utf-8")


def _read_env(home: Path) -> dict:
    return json.loads(
        (home / "settings.json").read_text(encoding="utf-8")
    ).get("env", {})


def _read_settings(home: Path) -> dict:
    return json.loads((home / "settings.json").read_text(encoding="utf-8"))


def _make_registry(path: Path) -> None:
    path.write_text(json.dumps({
        "version": 1,
        "roles": {
            "manager": {"tier": "manager", "home": "~/.claude-manager",
                        "provider": "Zhipu GLM", "model": MODEL_TARGET},
            "coder": {"tier": "worker", "home": "~/.claude-worker",
                      "provider": "Xiaomi MiMo V2.5 - Pro",
                      "model": "mimo-v2.5-pro"},
        },
    }, indent=2), encoding="utf-8")


class Env:
    """A hermetic switch environment: homes + registry + data home + PATH."""

    def __init__(self, tmp_path: Path, fake_claude_model: str) -> None:
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.main = self.home / ".claude-worker"
        self.slot2 = self.home / ".claude-worker-2"
        self.manager = self.home / ".claude-manager"
        self.registry = tmp_path / "role-registry.json"
        self.data_home = tmp_path / "ilk-data"

        _write_settings(self.main, MODEL_BEFORE_MAIN, BASE_URL_BEFORE)
        _write_settings(self.slot2, MODEL_BEFORE_SLOT, BASE_URL_BEFORE)
        _write_settings(self.manager, MODEL_TARGET, BASE_URL_TARGET,
                        token=TOKEN_TARGET)
        _make_registry(self.registry)

        # Fake claude: reports the canned model, and logs each probe's
        # CLAUDE_CONFIG_DIR so tests can assert the probe ran per home.
        self.probe_log = tmp_path / "probe.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "claude").write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"{self.probe_log}\" \"$CLAUDE_CONFIG_DIR\" >> \"{self.probe_log}\"\n"
            f"echo \"{fake_claude_model}\"\n",
            encoding="utf-8",
        )
        os.chmod(bin_dir / "claude", 0o755)
        self.bin_dir = bin_dir

    def run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["ILK_ROLE_REGISTRY"] = str(self.registry)
        env["ILK_DATA_HOME"] = str(self.data_home)
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["bash", str(TOOL), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=60,
        )

    def probe_homes(self) -> list:
        """CLAUDE_CONFIG_DIR values the probe was invoked with, in order."""
        if not self.probe_log.exists():
            return []
        rows = self.probe_log.read_text(encoding="utf-8").splitlines()
        return [rows[i] for i in range(1, len(rows), 2)]


@pytest.fixture
def env_ok_probe(tmp_path: Path) -> Env:
    """Probe reports the target — the happy path."""
    return Env(tmp_path, MODEL_TARGET)


@pytest.fixture
def env_bad_probe(tmp_path: Path) -> Env:
    """Probe reports the wrong model — AC3's rollback path."""
    return Env(tmp_path, "totally-wrong-model")


def _spawn_marker_process(script_path: Path, marker: Path) -> subprocess.Popen:
    """A fake watchdog/runner: touches `marker` on TERM, then exits."""
    script_path.write_text(
        "#!/usr/bin/env bash\n"
        f"trap 'touch \"{marker}\"; exit 0' TERM\n"
        "while :; do sleep 0.2; done\n",
        encoding="utf-8",
    )
    os.chmod(script_path, 0o755)
    proc = subprocess.Popen(
        ["bash", str(script_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc


class TestShow:
    def test_reports_every_home_and_the_registry_mismatch(self, env_ok_probe: Env):
        """AC1: both homes listed, live model reported, and the registry
        disagreement (registry says mimo, home says model-A) is LOUD."""
        result = env_ok_probe.run("show")
        assert result.returncode == 0, result.stdout + result.stderr
        out = result.stdout
        assert ".claude-worker" in out
        assert ".claude-worker-2" in out
        assert MODEL_BEFORE_MAIN in out
        assert MODEL_BEFORE_SLOT in out
        assert "mismatch" in out.lower()
        assert "mimo-v2.5-pro" in out  # what the registry claims
        # It is a report, not a gate: exit 0 even with a mismatch present.

    def test_exit_zero_with_no_worker_homes(self, tmp_path: Path):
        env = Env(tmp_path, MODEL_TARGET)
        for home in (env.main, env.slot2):
            (home / "settings.json").unlink()
        result = env.run("show")
        assert result.returncode == 0, result.stdout + result.stderr


class TestUse:
    def test_rewrites_every_home_backs_up_probes_reports_success(
        self, env_ok_probe: Env
    ):
        """AC1 happy path: use <role> sweeps main + slot, preserves keys,
        writes backups, probes each home, then reports success."""
        result = env_ok_probe.run("use", "manager")
        assert result.returncode == 0, result.stdout + result.stderr

        for home, before in ((env_ok_probe.main, MODEL_BEFORE_MAIN),
                             (env_ok_probe.slot2, MODEL_BEFORE_SLOT)):
            env_now = _read_env(home)
            assert env_now["ANTHROPIC_MODEL"] == MODEL_TARGET
            assert env_now["ANTHROPIC_BASE_URL"] == BASE_URL_TARGET
            assert env_now["ANTHROPIC_AUTH_TOKEN"] == TOKEN_TARGET
            # Rewrite touches ONLY the env block.
            settings = _read_settings(home)
            assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
            assert settings["includeCoAuthoredBy"] is False
            # Timestamped backup per home, holding the pre-switch state.
            backups = sorted(home.glob("settings.json.bak-*"))
            assert len(backups) == 1, backups
            backup_env = json.loads(
                backups[0].read_text(encoding="utf-8")
            )["env"]
            assert backup_env["ANTHROPIC_MODEL"] == before

        # The probe ran under every worker home before success was reported.
        probed = env_ok_probe.probe_homes()
        assert sorted(probed) == sorted([
            str(env_ok_probe.main), str(env_ok_probe.slot2)])
        # Engine-precedence facts are part of the success report.
        assert "claude-worker" in result.stdout

    def test_unknown_target_is_a_usage_error(self, env_ok_probe: Env):
        result = env_ok_probe.run("use", "no-such-role")
        assert result.returncode == 2
        assert _read_env(env_ok_probe.main)["ANTHROPIC_MODEL"] == MODEL_BEFORE_MAIN

    def test_refuses_when_a_loop_is_live_and_names_it(
        self, env_ok_probe: Env
    ):
        """AC2: a live runner pid under ILK_DATA_HOME with no --now refuses,
        names the project key, and touches nothing."""
        proj = env_ok_probe.data_home / "projects" / "fixture-proj" / "runtime"
        (proj / "launcher").mkdir(parents=True)
        fake = _spawn_marker_process(
            env_ok_probe.root / "fake-runner.sh", env_ok_probe.root / "runner.marker")
        (proj / "launcher" / "running.pid").write_text(f"{fake.pid}\n", encoding="utf-8")
        try:
            result = env_ok_probe.run("use", "manager")
            assert result.returncode != 0
            assert "fixture-proj" in result.stdout + result.stderr
            assert _read_env(env_ok_probe.main)["ANTHROPIC_MODEL"] == MODEL_BEFORE_MAIN
            assert fake.poll() is None  # not killed by the refusal
        finally:
            fake.send_signal(signal.SIGKILL)
            fake.wait()

    def test_now_stops_watchdog_before_runner(self, env_ok_probe: Env):
        """AC2: with --now the watchdog dies FIRST — asserted by the mtimes
        of the markers the fakes write when their TERM trap fires."""
        proj = env_ok_probe.data_home / "projects" / "fixture-proj" / "runtime"
        (proj / "launcher").mkdir(parents=True)
        (proj / "watchdog").mkdir(parents=True)
        w_marker = env_ok_probe.root / "watchdog.marker"
        r_marker = env_ok_probe.root / "runner.marker"
        watchdog = _spawn_marker_process(
            env_ok_probe.root / "fake-watchdog.sh", w_marker)
        runner = _spawn_marker_process(
            env_ok_probe.root / "fake-runner.sh", r_marker)
        (proj / "watchdog" / "watchdog.pid").write_text(
            f"{watchdog.pid}\n", encoding="utf-8")
        (proj / "launcher" / "running.pid").write_text(
            f"{runner.pid}\n", encoding="utf-8")
        try:
            result = env_ok_probe.run("use", "manager", "--now")
            assert result.returncode == 0, result.stdout + result.stderr
            assert w_marker.exists() and r_marker.exists()
            assert (w_marker.stat().st_mtime_ns
                    < r_marker.stat().st_mtime_ns), "watchdog must stop first"
        finally:
            for proc in (watchdog, runner):
                if proc.poll() is None:
                    proc.send_signal(signal.SIGKILL)
                    proc.wait()


class TestRollback:
    def test_probe_mismatch_leaves_homes_unchanged_and_exits_nonzero(
        self, env_bad_probe: Env
    ):
        """AC3: the probe reports the wrong model → no home keeps the new
        env, exit non-zero, and the failure is said out loud."""
        result = env_bad_probe.run("use", "manager")
        assert result.returncode != 0
        out = result.stdout + result.stderr
        assert "probe" in out.lower() or "rollback" in out.lower()
        assert _read_env(env_bad_probe.main)["ANTHROPIC_MODEL"] == MODEL_BEFORE_MAIN
        assert _read_env(env_bad_probe.slot2)["ANTHROPIC_MODEL"] == MODEL_BEFORE_SLOT


class TestRestore:
    def test_restore_returns_every_home_to_its_backup(
        self, env_ok_probe: Env
    ):
        """AC1 tail + the retro's hand-written-backups hazard: restore is
        one command, per home, newest backup wins."""
        # An ancient backup must NOT be the one restore picks.
        stale = env_ok_probe.main / "settings.json.bak-20000101000000"
        stale.write_text(json.dumps({"env": {
            "ANTHROPIC_MODEL": "garbage-ancient"}}), encoding="utf-8")

        assert env_ok_probe.run("use", "manager").returncode == 0
        result = env_ok_probe.run("restore")
        assert result.returncode == 0, result.stdout + result.stderr
        assert _read_env(env_ok_probe.main)["ANTHROPIC_MODEL"] == MODEL_BEFORE_MAIN
        assert _read_env(env_ok_probe.slot2)["ANTHROPIC_MODEL"] == MODEL_BEFORE_SLOT


# ── Registry sync (SP4 — registry-honest-after-switch) ────────────────────


def _read_registry(env: Env) -> dict:
    """Read the role registry as a dict."""
    return json.loads(env.registry.read_text(encoding="utf-8"))


def _registry_role_model(env: Env, role_name: str) -> str:
    """Read a role's model from the registry."""
    data = _read_registry(env)
    return data["roles"][role_name]["model"]


class TestRegistrySync:
    """AC1-AC3 for registry-honest-after-switch.

    Red-first: ``use`` currently does NOT update the registry.  These tests
    assert the expected post-switch registry state so step 1 can implement
    the sync and turn them green.
    """

    def test_use_updates_registry_model_for_matched_role(
        self, env_ok_probe: Env
    ):
        """AC1: after a verified switch, the matched role's model in the
        registry must match the target — not the stale pre-switch value."""
        # Pre-condition: registry says mimo-v2.5-pro for coder.
        assert _registry_role_model(env_ok_probe, "coder") == "mimo-v2.5-pro"

        result = env_ok_probe.run("use", "manager")
        assert result.returncode == 0, result.stdout + result.stderr

        # After switch, registry must reflect the actual model.
        assert _registry_role_model(env_ok_probe, "coder") == MODEL_TARGET

    def test_use_updates_registry_provider_for_matched_role(
        self, env_ok_probe: Env
    ):
        """AC1: provider field is synced alongside model."""
        data_before = _read_registry(env_ok_probe)
        assert data_before["roles"]["coder"]["provider"] == "Xiaomi MiMo V2.5 - Pro"

        env_ok_probe.run("use", "manager")

        data_after = _read_registry(env_ok_probe)
        # Provider must come from the source role (manager).
        assert data_after["roles"]["coder"]["provider"] == data_after["roles"]["manager"]["provider"]

    def test_use_preserves_registry_formatting(self, env_ok_probe: Env):
        """AC1: the registry rewrite preserves 2-space indent and key order."""
        before = env_ok_probe.registry.read_text(encoding="utf-8")
        env_ok_probe.run("use", "manager")
        after = env_ok_probe.registry.read_text(encoding="utf-8")
        # Must still be valid JSON with indent=2.
        data = json.loads(after)
        assert "roles" in data
        assert "coder" in data["roles"]
        # Key order within the role must be preserved (tier, home, provider, model).
        role_keys = list(data["roles"]["coder"].keys())
        assert role_keys.index("tier") < role_keys.index("home")
        assert role_keys.index("provider") < role_keys.index("model")

    def test_show_no_mismatch_after_registry_sync(self, env_ok_probe: Env):
        """AC1: after sync, show prints no mismatch line for the matched role."""
        use_result = env_ok_probe.run("use", "manager")
        assert use_result.returncode == 0, use_result.stdout + use_result.stderr
        result = env_ok_probe.run("show")
        assert result.returncode == 0
        # Check for the emitted marker, not the bare word — the test's own
        # tmpdir name contains "mismatch" (from the function name) and show
        # prints the absolute path, so a bare substring check always fires.
        assert "!! MISMATCH" not in result.stdout, (
            f"use output:\n{use_result.stdout}\n"
            f"registry after use:\n{env_ok_probe.registry.read_text()}\n"
            f"show output:\n{result.stdout}"
        )

    def test_failed_probe_rolls_back_registry(self, env_bad_probe: Env):
        """AC2: a probe that reports the wrong model rolls back the registry
        to byte-identical to its pre-attempt state."""
        registry_before = env_bad_probe.registry.read_bytes()
        result = env_bad_probe.run("use", "manager")
        assert result.returncode != 0  # probe failed

        registry_after = env_bad_probe.registry.read_bytes()
        assert registry_after == registry_before, (
            "registry changed after a failed probe — must be rolled back"
        )

    def test_failed_probe_rolls_back_homes(self, env_bad_probe: Env):
        """AC2: homes are also rolled back on probe failure."""
        env_main_before = _read_env(env_bad_probe.main)
        env_slot_before = _read_env(env_bad_probe.slot2)

        env_bad_probe.run("use", "manager")

        assert _read_env(env_bad_probe.main) == env_main_before
        assert _read_env(env_bad_probe.slot2) == env_slot_before


# ── Config + live probes (SP1 — probes-tell-the-truth) ────────────────────


class EnvProbe:
    """A hermetic probe-test environment with a stream-json fake claude.

    Unlike ``Env`` (whose fake claude echoes a plain-text model id),
    this fixture's fake claude emits a stream-json init event with the
    model field — matching the real ``claude --output-format stream-json``
    that ``probe_config`` will read.
    """

    def __init__(self, tmp_path: Path, config_model: str, live_ok: bool) -> None:
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.main = self.home / ".claude-worker"
        self.manager = self.home / ".claude-manager"
        self.registry = tmp_path / "role-registry.json"
        self.data_home = tmp_path / "ilk-data"

        # The home under test: config_model is what settings.json says.
        self.main.mkdir(parents=True, exist_ok=True)
        (self.main / "settings.json").write_text(json.dumps({
            "env": {
                "ANTHROPIC_MODEL": config_model,
                "ANTHROPIC_BASE_URL": "https://a.example/api",
                "ANTHROPIC_AUTH_TOKEN": "tok-a",
            },
        }, indent=2), encoding="utf-8")

        # Manager env block (the source role for use).
        self.manager.mkdir(parents=True, exist_ok=True)
        (self.manager / "settings.json").write_text(json.dumps({
            "env": {
                "ANTHROPIC_MODEL": config_model,
                "ANTHROPIC_BASE_URL": "https://b.example/api",
                "ANTHROPIC_AUTH_TOKEN": "tok-b",
            },
        }, indent=2), encoding="utf-8")

        self.registry.write_text(json.dumps({
            "version": 1,
            "roles": {
                "manager": {"tier": "manager", "home": "~/.claude-manager",
                            "provider": "test", "model": config_model},
                "coder": {"tier": "worker", "home": "~/.claude-worker",
                          "provider": "test", "model": "other"},
            },
        }, indent=2), encoding="utf-8")

        # Fake claude: emits stream-json init event + live probe result.
        self.probe_log = tmp_path / "probe.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        live_result = "OK" if live_ok else "403 Request not allowed"
        live_exit = "0" if live_ok else "1"
        (bin_dir / "claude").write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"{self.probe_log}\" \"$CLAUDE_CONFIG_DIR\" "
            f">> \"{self.probe_log}\"\n"
            # Stream-json init event (probe_config reads this).
            f"echo '{{\"model\": \"{config_model}\", \"type\": \"init\"}}'\n"
            # Live probe result (probe_live reads is_error / result).
            f"echo '{{\"is_error\": {str(not live_ok).lower()}, "
            f"\"result\": \"{live_result}\"}}'\n"
            f"exit {live_exit}\n",
            encoding="utf-8",
        )
        os.chmod(bin_dir / "claude", 0o755)
        self.bin_dir = bin_dir

    def run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["ILK_ROLE_REGISTRY"] = str(self.registry)
        env["ILK_DATA_HOME"] = str(self.data_home)
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["bash", str(TOOL), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=60,
        )

    def probe_homes(self) -> list:
        """CLAUDE_CONFIG_DIR values the probe was invoked with, in order."""
        if not self.probe_log.exists():
            return []
        rows = self.probe_log.read_text(encoding="utf-8").splitlines()
        return [rows[i] for i in range(1, len(rows), 2)]


class TestConfigProbe:
    """AC1: probe_config reads the init-event model, not the model's self-report."""

    def test_config_probe_reads_init_event_not_self_report(self, tmp_path: Path):
        """The fake claude's settings.json names the target model.
        The probe must read the init event's model field (which matches)
        rather than asking the model to state its own id (which would
        return whatever the fake emits as plain text).

        This test is RED: probe_model today asks the model to self-report
        and compares the last line of plain text — it will either fail
        or return a garbage comparison against the stream-json output.
        """
        probe_env = EnvProbe(tmp_path, "mimo-v2.5-pro", live_ok=True)
        # Import the module to call probe_config directly.
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            import worker_model
            ok, detail = worker_model.probe_config(probe_env.main)
        finally:
            sys.path.pop(0)
        assert ok is True, f"probe_config should match; got ok={ok}, detail={detail!r}"
        assert detail == "mimo-v2.5-pro"


class TestLiveProbe:
    """AC2: probe_live returns (ok, detail) from is_error / result."""

    def test_live_probe_reports_ok_on_success(self, tmp_path: Path):
        """Fake claude exits 0 with is_error=false → probe_live returns (True, 'OK')."""
        probe_env = EnvProbe(tmp_path, "mimo-v2.5-pro", live_ok=True)
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            import worker_model
            ok, detail = worker_model.probe_live(probe_env.main)
        finally:
            sys.path.pop(0)
        assert ok is True
        assert detail == "OK"

    def test_live_probe_reports_failure_on_403(self, tmp_path: Path):
        """Fake claude exits non-zero with is_error=true → probe_live returns
        (False, detail) — the 403 case from the 2026-09-21 incident."""
        probe_env = EnvProbe(tmp_path, "mimo-v2.5-pro", live_ok=False)
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            import worker_model
            ok, detail = worker_model.probe_live(probe_env.main)
        finally:
            sys.path.pop(0)
        assert ok is False
        assert "403" in detail


class TestSkipProbeMessage:
    """AC4: --skip-probe prints UNVERIFIED, never 'switch verified'."""

    def test_skip_probe_says_unverified(self, tmp_path: Path):
        """With --skip-probe, the command must print UNVERIFIED and must
        NOT print 'switch verified for every home above.'"""
        probe_env = EnvProbe(tmp_path, "mimo-v2.5-pro", live_ok=True)
        result = probe_env.run("use", "manager", "--skip-probe")
        assert result.returncode == 0, result.stdout + result.stderr
        out = result.stdout
        assert "UNVERIFIED" in out, (
            f"--skip-probe must print UNVERIFIED; got:\n{out}")
        assert "switch verified for every home above" not in out, (
            f"--skip-probe must NOT print 'switch verified'; got:\n{out}")
