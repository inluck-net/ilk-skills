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
        # Handles --output-format for probe_config (stream-json) and
        # probe_live (json) in addition to plain-text probe_model.
        self.probe_log = tmp_path / "probe.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "claude").write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"{self.probe_log}\" \"$CLAUDE_CONFIG_DIR\" "
            f">> \"{self.probe_log}\"\n"
            "fmt=plain\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            "    --output-format) ;;\n"
            "    stream-json|json) fmt=\"$arg\" ;;\n"
            "  esac\n"
            "done\n"
            "case \"$fmt\" in\n"
            f"  stream-json) echo '{{\"model\": \"{fake_claude_model}\", \"type\": \"init\"}}' ;;\n"
            f"  json)        echo '{{\"is_error\": false, \"result\": \"OK\"}}' ;;\n"
            f"  *)           echo \"{fake_claude_model}\" ;;\n"
            "esac\n",
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
        # Each home gets two probes: probe_config + probe_live.
        probed = env_ok_probe.probe_homes()
        assert sorted(probed) == sorted([
            str(env_ok_probe.main), str(env_ok_probe.main),
            str(env_ok_probe.slot2), str(env_ok_probe.slot2)])
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

        # Fake claude: emits stream-json init event for probe_config
        # and plain JSON for probe_live, based on --output-format.
        self.probe_log = tmp_path / "probe.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        live_result = "OK" if live_ok else "403 Request not allowed"
        live_exit = "0" if live_ok else "1"
        (bin_dir / "claude").write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"{self.probe_log}\" \"$CLAUDE_CONFIG_DIR\" "
            f">> \"{self.probe_log}\"\n"
            "fmt=plain\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            "    --output-format) ;;\n"
            "    stream-json|json) fmt=\"$arg\" ;;\n"
            "  esac\n"
            "done\n"
            "case \"$fmt\" in\n"
            f"  stream-json) echo '{{\"model\": \"{config_model}\", \"type\": \"init\"}}' ;;\n"
            f"  json)        echo '{{\"is_error\": {str(not live_ok).lower()}, "
            f"\"result\": \"{live_result}\"}}'; exit {live_exit} ;;\n"
            f"  *)           echo \"{config_model}\" ;;\n"
            "esac\n",
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
        # Pass extra_env so the probe finds the fake claude on PATH.
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            import worker_model
            ok, detail = worker_model.probe_config(
                probe_env.main,
                extra_env={"PATH": f"{probe_env.bin_dir}{os.pathsep}{os.environ['PATH']}"},
            )
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
            ok, detail = worker_model.probe_live(
                probe_env.main,
                extra_env={"PATH": f"{probe_env.bin_dir}{os.pathsep}{os.environ['PATH']}"},
            )
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
            ok, detail = worker_model.probe_live(
                probe_env.main,
                extra_env={"PATH": f"{probe_env.bin_dir}{os.pathsep}{os.environ['PATH']}"},
            )
        finally:
            sys.path.pop(0)
        assert ok is False
        assert "403" in detail


# ── Provider switch (SP2 — use-switches-providers) ────────────────────────


class EnvProviderSwitch:
    """Hermetic switch environment with a fake ccswitch_import on PATH.

    Two providers: glm (base url GLM_URL, token tok-glm) and mimo
    (base url MIMO_URL, token tok-mimo).  The registry names a `coder`
    role whose home carries glm-shaped env; ``use coder mimo`` should
    resolve the mimo env from ccswitch_import, not from the home.
    """

    GLM_URL = "https://glm.example/api"
    MIMO_URL = "https://mimo.example/api"
    MIMO_TOKEN = "tok-mimo"
    MIMO_MODEL = "mimo-v2.5-pro"
    GLM_MODEL = "glm-5.3"

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.main = self.home / ".claude-worker"
        self.manager = self.home / ".claude-manager"
        self.registry = tmp_path / "role-registry.json"
        self.data_home = tmp_path / "ilk-data"

        # Coder home: glm-shaped env (the "old" provider).
        _write_settings(self.main, self.GLM_MODEL, self.GLM_URL,
                        token="tok-glm")
        # Manager home: already on mimo.
        _write_settings(self.manager, self.MIMO_MODEL, self.MIMO_URL,
                        token=self.MIMO_TOKEN)

        self.registry.write_text(json.dumps({
            "version": 1,
            "roles": {
                "manager": {"tier": "manager", "home": "~/.claude-manager",
                            "provider": "Xiaomi MiMo V2.5 - Pro",
                            "model": self.MIMO_MODEL},
                "coder": {"tier": "worker", "home": "~/.claude-worker",
                          "provider": "Zhipu GLM", "model": self.GLM_MODEL},
            },
        }, indent=2), encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        # Fake claude (probe reports target model).
        (bin_dir / "claude").write_text(
            "#!/usr/bin/env bash\n"
            "fmt=plain\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            "    --output-format) ;;\n"
            "    stream-json|json) fmt=\"$arg\" ;;\n"
            "  esac\n"
            "done\n"
            "case \"$fmt\" in\n"
            f"  stream-json) echo '{{\"model\": \"{self.MIMO_MODEL}\", \"type\": \"init\"}}' ;;\n"
            f"  json)        echo '{{\"is_error\": false, \"result\": \"OK\"}}' ;;\n"
            f"  *)           echo \"{self.MIMO_MODEL}\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        os.chmod(bin_dir / "claude", 0o755)

        # Fake ccswitch_import: returns the mimo provider's env.
        mimo_export = json.dumps({
            "id": "mimo",
            "name": "Xiaomi MiMo V2.5 - Pro",
            "category": "",
            "is_official": False,
            "ANTHROPIC_BASE_URL": self.MIMO_URL,
            "ANTHROPIC_AUTH_TOKEN": self.MIMO_TOKEN,
            "ANTHROPIC_MODEL": self.MIMO_MODEL,
        }, indent=2)
        (bin_dir / "ccswitch_import").write_text(
            "#!/usr/bin/env bash\n"
            f"echo '{mimo_export}'\n",
            encoding="utf-8",
        )
        os.chmod(bin_dir / "ccswitch_import", 0o755)
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


class TestUseProviderSwitch:
    """SP2 step-0 red: a provider switch keeps the old base url."""

    def test_switch_writes_provider_base_url_not_home_url(self, tmp_path: Path):
        """After switching coder from glm to mimo, the env must carry the
        mimo base url — not the glm url that was in the home before.

        This is RED: cmd_use today reads env from the role's home (glm),
        so the written base url stays GLM_URL.
        """
        env = EnvProviderSwitch(tmp_path)
        result = env.run("use", "coder")
        assert result.returncode == 0, result.stdout + result.stderr
        env_now = _read_env(env.main)
        assert env_now["ANTHROPIC_BASE_URL"] == env.MIMO_URL, (
            f"base url should be mimo's ({env.MIMO_URL}), "
            f"got {env_now['ANTHROPIC_BASE_URL']} — "
            f"cmd_use reads from the home, not from ccswitch_import"
        )

    def test_switch_writes_provider_token(self, tmp_path: Path):
        """Auth token must come from the provider, not the old home.

        This is RED: same root cause (env sourced from home).
        """
        env = EnvProviderSwitch(tmp_path)
        result = env.run("use", "coder")
        assert result.returncode == 0, result.stdout + result.stderr
        env_now = _read_env(env.main)
        assert env_now["ANTHROPIC_AUTH_TOKEN"] == env.MIMO_TOKEN

    def test_switch_writes_provider_model(self, tmp_path: Path):
        """Model must come from the provider export, not the registry row alone.

        This is RED: same root cause.
        """
        env = EnvProviderSwitch(tmp_path)
        result = env.run("use", "coder")
        assert result.returncode == 0, result.stdout + result.stderr
        env_now = _read_env(env.main)
        assert env_now["ANTHROPIC_MODEL"] == env.MIMO_MODEL


class TestUseManagerSweep:
    """SP2 step-0 red: the sweep misses ~/.claude-manager."""

    def test_sweep_includes_manager_home_from_registry(self, tmp_path: Path):
        """When the registry names ~/.claude-manager, the switch must
        rewrite that home too — not just .claude-worker*.

        This is RED: worker_homes() globs ~/.claude-worker* and misses
        ~/.claude-manager entirely.
        """
        env = EnvProviderSwitch(tmp_path)
        result = env.run("use", "coder")
        assert result.returncode == 0, result.stdout + result.stderr
        # If the manager home was swept, its env carries the mimo provider
        # values (resolved from ccswitch_import).
        mgr_env = _read_env(env.manager)
        assert mgr_env["ANTHROPIC_BASE_URL"] == env.MIMO_URL, (
            f"manager home not swept: base url is {mgr_env['ANTHROPIC_BASE_URL']}, "
            f"expected {env.MIMO_URL}"
        )


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


# ── Roles & providers enumerable (SP3 — roles-and-providers-enumerable) ──


class EnvEnumerate:
    """Hermetic environment for testing roles and providers subcommands.

    Two roles (manager, coder) with homes carrying distinct env blocks,
    plus a fake ccswitch_import that returns two providers (glm, mimo).
    The official entry has no base url and token_present: false.
    """

    GLM_URL = "https://glm.example/api"
    GLM_TOKEN = "sk-ant-glm-secret-token-abcdef"
    GLM_MODEL = "glm-5.3"
    MIMO_URL = "https://mimo.example/api"
    MIMO_TOKEN = "tp-mimo-secret-token-12345678"
    MIMO_MODEL = "mimo-v2.5-pro"

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.manager = self.home / ".claude-manager"
        self.main = self.home / ".claude-worker"
        self.registry = tmp_path / "role-registry.json"
        self.data_home = tmp_path / "ilk-data"

        # Manager home: official (no base url, no token).
        self.manager.mkdir(parents=True, exist_ok=True)
        (self.manager / "settings.json").write_text(json.dumps({
            "env": {
                "ANTHROPIC_MODEL": "opus",
                "ANTHROPIC_AUTH_TOKEN": "sk-ant-official-secret",
            },
        }, indent=2), encoding="utf-8")

        # Coder home: custom provider.
        _write_settings(self.main, self.GLM_MODEL, self.GLM_URL,
                        token=self.GLM_TOKEN)

        # Role registry.
        self.registry.write_text(json.dumps({
            "version": 1,
            "roles": {
                "manager": {"tier": "manager", "home": "~/.claude-manager",
                             "provider": "Claude Official", "model": "opus",
                             "auth": "official"},
                "coder": {"tier": "worker", "home": "~/.claude-worker",
                           "provider": "Zhipu GLM", "model": self.GLM_MODEL},
            },
        }, indent=2), encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        # Fake claude (probe).
        (bin_dir / "claude").write_text(
            "#!/usr/bin/env bash\n"
            "fmt=plain\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            "    --output-format) ;;\n"
            "    stream-json|json) fmt=\"$arg\" ;;\n"
            "  esac\n"
            "done\n"
            "case \"$fmt\" in\n"
            f"  stream-json) echo '{{\"model\": \"{self.MIMO_MODEL}\", \"type\": \"init\"}}' ;;\n"
            f"  json)        echo '{{\"is_error\": false, \"result\": \"OK\"}}' ;;\n"
            f"  *)           echo \"{self.MIMO_MODEL}\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        os.chmod(bin_dir / "claude", 0o755)

        # Fake ccswitch_import: returns two providers.
        glm_export = json.dumps({
            "id": "glm",
            "name": "Zhipu GLM",
            "category": "custom",
            "is_official": False,
            "ANTHROPIC_BASE_URL": self.GLM_URL,
            "ANTHROPIC_AUTH_TOKEN": self.GLM_TOKEN,
            "ANTHROPIC_MODEL": self.GLM_MODEL,
        }, indent=2)
        mimo_export = json.dumps({
            "id": "mimo",
            "name": "Xiaomi MiMo V2.5 - Pro",
            "category": "custom",
            "is_official": False,
            "ANTHROPIC_BASE_URL": self.MIMO_URL,
            "ANTHROPIC_AUTH_TOKEN": self.MIMO_TOKEN,
            "ANTHROPIC_MODEL": self.MIMO_MODEL,
        }, indent=2)
        official_export = json.dumps({
            "id": "official",
            "name": "Claude Official",
            "category": "official",
            "is_official": True,
            "ANTHROPIC_BASE_URL": "",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_MODEL": "opus",
        }, indent=2)
        (bin_dir / "ccswitch_import").write_text(
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            "  list)\n"
            f"    echo '[{glm_export},{mimo_export},{official_export}]'\n"
            "    ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        os.chmod(bin_dir / "ccswitch_import", 0o755)
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


# Token-shaped patterns that must never appear in human output.
_TOKEN_PATTERNS = ["sk-ant-", "tp-", "sk-"]


def _assert_no_tokens_in_lines(output: str) -> None:
    """Fail if any output line contains a token-shaped string.

    A token-shaped string is: a line matching any of the known prefixes
    (sk-ant-, tp-, sk-), OR any line containing a 40+ character opaque
    alphanumeric+dash+underscore run that isn't a known word.
    """
    import re
    opaque_re = re.compile(r"[A-Za-z0-9_-]{40,}")
    for i, line in enumerate(output.splitlines(), 1):
        for pat in _TOKEN_PATTERNS:
            assert pat not in line, (
                f"line {i} contains token prefix {pat!r}: {line!r}")
        for match in opaque_re.finditer(line):
            val = match.group(0)
            # Allow known non-token long strings (URLs, paths, json keys).
            if val.startswith("http") or "/" in val or val == "permissions":
                continue
            assert False, (
                f"line {i} contains opaque 40+ char string: {val!r} in {line!r}")


class TestRoles:
    """SP3 step-0 red: the roles subcommand does not exist yet."""

    def test_roles_json_parses_and_contains_expected_keys(self, tmp_path: Path):
        """roles --json must return a JSON array where each entry has
        name, tier, home, model, and auth.

        RED: the 'roles' subcommand does not exist — argparse rejects it.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("roles", "--json")
        assert result.returncode == 0, (
            f"roles --json failed: {result.stdout}\n{result.stderr}")
        data = json.loads(result.stdout)
        assert isinstance(data, list), f"expected JSON array, got {type(data)}"
        assert len(data) >= 2, f"expected at least 2 roles, got {len(data)}"
        for entry in data:
            for key in ("name", "tier", "home", "model"):
                assert key in entry, (
                    f"role entry missing {key!r}: {entry}")

    def test_roles_json_includes_auth_field(self, tmp_path: Path):
        """Each role entry must carry an auth field (official / custom / ...).

        RED: subcommand does not exist.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("roles", "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(result.stdout)
        for entry in data:
            assert "auth" in entry, (
                f"role entry missing 'auth': {entry}")

    def test_roles_text_lists_every_registry_role(self, tmp_path: Path):
        """The text form must name every role in the registry.

        RED: subcommand does not exist.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("roles")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "manager" in result.stdout
        assert "coder" in result.stdout

    def test_roles_output_has_no_tokens(self, tmp_path: Path):
        """Neither text nor JSON form may contain token-shaped strings.

        RED: subcommand does not exist.
        """
        env = EnvEnumerate(tmp_path)
        for args in [("roles",), ("roles", "--json")]:
            result = env.run(*args)
            _assert_no_tokens_in_lines(result.stdout)


class TestProviders:
    """SP3 step-0 red: the providers subcommand does not exist yet."""

    def test_providers_json_parses_and_contains_expected_keys(
        self, tmp_path: Path
    ):
        """providers --json must return a JSON array where each entry has
        id, name, model, base_url, and token_present.

        RED: the 'providers' subcommand does not exist — argparse rejects it.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("providers", "--json")
        assert result.returncode == 0, (
            f"providers --json failed: {result.stdout}\n{result.stderr}")
        data = json.loads(result.stdout)
        assert isinstance(data, list), f"expected JSON array, got {type(data)}"
        assert len(data) >= 2, f"expected at least 2 providers, got {len(data)}"
        for entry in data:
            for key in ("id", "name", "model", "base_url", "token_present"):
                assert key in entry, (
                    f"provider entry missing {key!r}: {entry}")

    def test_providers_json_token_present_is_bool(self, tmp_path: Path):
        """token_present must be true/false, never the raw token value.

        RED: subcommand does not exist.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("providers", "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(result.stdout)
        for entry in data:
            val = entry.get("token_present")
            assert isinstance(val, bool), (
                f"token_present must be bool, got {type(val)}: {val!r}")

    def test_providers_official_entry_has_no_base_url(self, tmp_path: Path):
        """The official provider entry must show empty base_url and
        token_present: false.

        RED: subcommand does not exist.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("providers", "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        data = json.loads(result.stdout)
        official = [e for e in data if e.get("name") == "Claude Official"]
        assert len(official) == 1, (
            f"expected one 'Claude Official' entry, got {official}")
        assert official[0]["base_url"] in ("", None), (
            f"official base_url must be empty: {official[0]}")
        assert official[0]["token_present"] is False, (
            f"official token_present must be False: {official[0]}")

    def test_providers_output_has_no_tokens(self, tmp_path: Path):
        """Neither text nor JSON form may contain token-shaped strings.

        RED: subcommand does not exist.
        """
        env = EnvEnumerate(tmp_path)
        for args in [("providers",), ("providers", "--json")]:
            result = env.run(*args)
            _assert_no_tokens_in_lines(result.stdout)

    def test_providers_text_lists_every_ccswitch_provider(self, tmp_path: Path):
        """The text form must name every provider.

        RED: subcommand does not exist.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("providers")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Zhipu GLM" in result.stdout
        assert "Claude Official" in result.stdout


class TestValidationErrorListsOptions:
    """SP3 step-0 red: naming an unknown role or provider must exit non-zero
    and print the full list of valid values."""

    def test_unknown_role_prints_valid_roles(self, tmp_path: Path):
        """use <bad-role> must list the valid role names in the error.

        RED: resolve_target prints 'known: …' but this sub-plan wants
        the full list, not just the error.
        """
        env = EnvEnumerate(tmp_path)
        result = env.run("use", "no-such-role")
        assert result.returncode != 0
        out = result.stdout + result.stderr
        assert "manager" in out, f"error must list valid roles: {out}"
        assert "coder" in out, f"error must list valid roles: {out}"
