#!/usr/bin/env python3
"""ilk-worker-model — switch the loop worker's model in one command.

Replaces the five-step manual sequence of retro-2026-09-21-switching-the-
worker-model: a config key defeated by engine-flag precedence, a watchdog
respawn that landed on the PRIMARY account, hand-mirrored env blocks,
hand-written backups, and a by-hand verification session.

Commands:
  show                    Report every worker home's live model + base-url
                          host, cross-checked against role-registry.json
                          (mismatch prints LOUDLY). Exit 0 always — a report.
  use <role|model@role>   Switch every existing worker home to the target:
                          refuse (or, with --now, stop watchdogs FIRST then
                          runners), back up, rewrite ONLY the env block,
                          verify via a probe session per home, roll back on
                          mismatch, then print the engine-precedence facts.
  restore                 Restore the newest settings.json.bak-<ts> per home.

Environment:
  ILK_ROLE_REGISTRY  role registry file (default: role-registry.json next
                     to this script)
  ILK_DATA_HOME      loop data home scanned for live loops (canonical
                     override; default ~/.ilk-data)
  HOME               base for worker homes (~/.claude-worker plus every
                     existing ~/.claude-worker-<digits> sibling — see
                     worker_homes) and for "~" in registry homes

Exit codes: 0 ok · 2 usage / unresolvable target · 3 live loop refused ·
4 probe mismatch (rolled back).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = SCRIPT_DIR / "role-registry.json"

# Mirror stop_watchdog.sh: TERM the process group and the pid, wait up to
# 3s, then KILL. The wait between the watchdog stop and the runner stop is
# what makes the ordering observable (and safe — a half-dead watchdog can
# still respawn a runner, on the wrong account).
TERM_WAIT_S = 3.0
POLL_S = 0.05

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_LIVE_LOOP = 3
EXIT_PROBE = 4


# ── homes, registry, settings ────────────────────────────────────────────────

def worker_homes(home_base: Path) -> list:
    """Every existing worker home: main + numeric-suffix siblings.

    scheduler.sh's get_slot_home maps slot 1 → ~/.claude-worker and slot
    i>=2 → ~/.claude-worker-<i>, but the sweep is deliberately wider: any
    existing ~/.claude-worker-<digits> counts (this host has a live
    ~/.claude-worker-1 from worker-slot provisioning). A home that exists
    but is skipped silently keeps the old model — the retro's hazard 5.
    Non-numeric suffixes (~/.claude-worker-draw) are other ROLES' homes,
    not coder slots, and are not swept.
    """
    homes = []
    main = home_base / ".claude-worker"
    if main.is_dir():
        homes.append(main)
    for child in sorted(home_base.glob(".claude-worker-*")):
        suffix = child.name[len(".claude-worker-"):]
        if suffix.isdigit() and child.is_dir():
            homes.append(child)
    return homes


def load_registry(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    roles = data.get("roles") or {}
    if not isinstance(roles, dict) or not roles:
        raise ValueError(f"role registry {path} has no roles")
    return roles


def expand_home(path_str: str, home_base: Path) -> Path:
    if path_str == "~":
        return home_base
    if path_str.startswith("~/"):
        return home_base / path_str[2:]
    return Path(path_str)


def read_env_block(home: Path) -> dict:
    settings = home / "settings.json"
    if not settings.is_file():
        return {}
    try:
        return (json.loads(settings.read_text(encoding="utf-8")) or {}).get("env") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def base_url_host(url: str) -> str:
    url = url or ""
    if "://" not in url:
        return url or "(unset)"
    tail = url.split("://", 1)[1]
    return tail.split("/", 1)[0] or "(unset)"


# ── live loops ───────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return -1


def find_live_loops(data_home: Path) -> dict:
    """Live loops per project key: {"key": {"runner": pid, "watchdog": pid}}.

    Reads the canonical pidfiles under ILK_DATA_HOME/projects/<key>/runtime/
    and trusts only pids the process table confirms (stale pidfiles — the
    normal debris of a crashed run — read as dead).
    """
    me = {os.getpid(), os.getppid()}
    live: dict = {}
    projects = data_home / "projects"
    if not projects.is_dir():
        return live
    for proj in sorted(projects.iterdir()):
        if not proj.is_dir():
            continue
        runtime = proj / "runtime"
        found = {}
        for role, rel in (("watchdog", "watchdog/watchdog.pid"),
                          ("runner", "launcher/running.pid")):
            pid = _read_pid(runtime / rel)
            if pid > 0 and pid not in me and _pid_alive(pid):
                found[role] = pid
        if found:
            live[proj.name] = found
    return live


def _stop_pid(pid: int, label: str) -> None:
    """TERM (group + pid), wait, KILL — stop_watchdog.sh's escalation."""
    try:
        os.kill(-pid, signal.SIGTERM)  # the process group…
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, signal.SIGTERM)  # …and the pid itself
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.monotonic() + TERM_WAIT_S
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(POLL_S)
    if _pid_alive(pid):
        for target in (-pid, pid):
            try:
                os.kill(target, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    print(f"[stop] {label} (pid {pid}) stopped", flush=True)


def stop_live_loops(live: dict) -> None:
    """The retro's ordering invariant, encoded: WATCHDOGS FIRST, then runners.

    Killing the runner first hands a live watchdog a reason to respawn it —
    possibly without the engine flag, i.e. on the PRIMARY account.
    """
    for key, found in sorted(live.items()):
        if "watchdog" in found:
            _stop_pid(found["watchdog"], f"watchdog of {key}")
    for key, found in sorted(live.items()):
        if "runner" in found:
            _stop_pid(found["runner"], f"runner of {key}")


# ── show ─────────────────────────────────────────────────────────────────────

def cmd_show(home_base: Path, registry_path: Path) -> int:
    roles = None
    try:
        roles = load_registry(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[registry] unreadable: {exc}", file=sys.stderr)

    homes = worker_homes(home_base)
    if not homes:
        print(f"no worker homes under {home_base} "
              f"(looked for .claude-worker and .claude-worker-<n>)")
    main_model = None
    for home in homes:
        env = read_env_block(home)
        model = env.get("ANTHROPIC_MODEL", "(unset)")
        host = base_url_host(env.get("ANTHROPIC_BASE_URL", ""))
        print(f"{home}: model={model}  base-url-host={host}")
        if home.name == ".claude-worker":
            main_model = model
        if roles is not None:
            for role_name, role in roles.items():
                role_home = expand_home(str(role.get("home", "")), home_base)
                if role_home == home and role.get("model") != model:
                    print(f"  !! MISMATCH: registry role '{role_name}' says "
                          f"{role.get('model')}, home says {model}")
    if main_model is not None:
        for home in homes:
            if home.name == ".claude-worker":
                continue
            model = read_env_block(home).get("ANTHROPIC_MODEL", "(unset)")
            if model != main_model:
                print(f"  !! DRIFT: {home.name} says {model}, main home says "
                      f"{main_model}")
    return EXIT_OK


# ── use ──────────────────────────────────────────────────────────────────────


def _sync_registry(
    registry_path: Path,
    roles: dict,
    homes: list,
    home_base: Path,
    model: str,
    source_role: dict,
) -> None:
    """Update the role registry to match the live state after a verified switch.

    For every role whose home matches one of the worker homes we just switched,
    update the model and provider to match the source role.  Atomic: writes to
    a tmp file then renames.  Preserves 2-space indent and key order.
    """
    # Build a set of switched home paths for matching.
    switched = {h.resolve() for h in homes}

    # Read the raw JSON so we can preserve key order.
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    changed = False
    for role_name, role in data.get("roles", {}).items():
        role_home = expand_home(str(role.get("home", "")), home_base).resolve()
        if role_home in switched:
            if role.get("model") != model:
                role["model"] = model
                changed = True
            # Sync provider from the source role.
            src_provider = source_role.get("provider", "")
            if role.get("provider") != src_provider:
                role["provider"] = src_provider
                changed = True

    if changed:
        tmp = registry_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(registry_path)
        print(f"[registry] synced {registry_path.name}")

def resolve_target(target: str, roles: dict) -> tuple:
    """'<model>@<role>' or '<role>' -> (role_name, model, role_dict)."""
    if "@" in target:
        model, _,role_name = target.partition("@")
        model = model or None
    else:
        model, role_name = None, target
    role = roles.get(role_name)
    if role is None:
        print(f"error: '{role_name}' is not a role in the registry; "
              f"known: {', '.join(sorted(roles))}", file=sys.stderr)
        return None, None, None
    return role_name, model or str(role.get("model", "")), role


def probe_model(home: Path, timeout: float = 30.0) -> str:
    """The operator's manual check, automated: spawn a probe session under
    the worker home and read the model it reports (last non-empty line)."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(home)
    try:
        result = subprocess.run(
            ["claude", "-p", "Reply with ONLY your model id, nothing else."],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"(probe failed: {exc})"
    if result.returncode != 0:
        return f"(probe failed, exit {result.returncode})"
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else "(probe returned nothing)"


def cmd_use(target: str, now: bool, skip_probe: bool, home_base: Path,
            registry_path: Path, data_home: Path) -> int:
    try:
        roles = load_registry(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read role registry: {exc}", file=sys.stderr)
        return EXIT_USAGE

    role_name, model, role = resolve_target(target, roles)
    if role is None:
        return EXIT_USAGE

    source_home = expand_home(str(role.get("home", "")), home_base)
    source_env = read_env_block(source_home)
    if not source_env:
        print(f"error: role '{role_name}' home {source_home} has no settings.json "
              f"env block to source from", file=sys.stderr)
        return EXIT_USAGE
    target_env = dict(source_env)
    target_env["ANTHROPIC_MODEL"] = model
    print(f"target: {model} (role '{role_name}' @ {source_home}, "
          f"base-url {base_url_host(target_env.get('ANTHROPIC_BASE_URL', ''))})")

    live = find_live_loops(data_home)
    if live:
        keys = ", ".join(sorted(live))
        if not now:
            print(f"refusing: live loop(s) under {data_home}: {keys}\n"
                  f"  stop them first, or re-run with --now (stops WATCHDOGS "
                  f"first, then runners — the safe order)", file=sys.stderr)
            return EXIT_LIVE_LOOP
        print(f"[--now] stopping live loops: {keys}")
        stop_live_loops(live)

    homes = worker_homes(home_base)
    if not homes:
        print(f"error: no worker homes under {home_base}", file=sys.stderr)
        return EXIT_USAGE

    stamp = time.strftime("%Y%m%d%H%M%S")
    backups = {}
    for home in homes:
        settings = home / "settings.json"
        backup = home / f"settings.json.bak-{stamp}"
        if settings.exists():
            shutil.copy2(settings, backup)
        else:
            backup.write_text("{}", encoding="utf-8")
        backups[home] = backup
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        data["env"] = target_env
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"[switch] {home}: env rewritten (backup {backup.name})")

    if skip_probe:
        print("[probe] SKIPPED (--skip-probe) — switch is UNVERIFIED")
    else:
        for home in homes:
            reported = probe_model(home)
            if reported != model:
                print(f"error: probe under {home} reported '{reported}', "
                      f"expected '{model}' — ROLLING BACK", file=sys.stderr)
                for h, b in backups.items():
                    shutil.copy2(b, h / "settings.json")
                    print(f"[rollback] {h}: restored {b.name}", file=sys.stderr)
                return EXIT_PROBE
            print(f"[probe] {home}: reports {reported} ✓")

    # Sync the role registry: update every role whose home matches a worker
    # home we just switched.  This closes the gap where the registry says one
    # model while the homes run another (retro hazard 6).
    _sync_registry(registry_path, roles, homes, home_base, model, role)

    print("switch verified for every home above.")
    print("engine-precedence facts:")
    print("  - the scheduler dispatches with an explicit --engine claude-worker "
          "(hardcoded flag, scheduler.sh)")
    print("  - a watchdog relaunch carries the run's engine only after SP3 "
          "(respawn-never-lands-on-primary); until then DEFAULT_ENGINE "
          "applies — see launch.sh")
    print(f"  - backups: {', '.join(str(b) for b in sorted(backups.values()))}")
    print("  - restore with: ilk-worker-model restore")
    return EXIT_OK


# ── restore ──────────────────────────────────────────────────────────────────

def cmd_restore(home_base: Path) -> int:
    homes = worker_homes(home_base)
    if not homes:
        print(f"no worker homes under {home_base}")
        return EXIT_OK
    for home in homes:
        backups = sorted(home.glob("settings.json.bak-*"))
        if not backups:
            print(f"[restore] {home}: no backup found — untouched")
            continue
        newest = backups[-1]
        shutil.copy2(newest, home / "settings.json")
        print(f"[restore] {home}: restored {newest.name}")
    return EXIT_OK


# ── entry ────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="ilk-worker-model",
        description="Switch the loop worker's model in one command.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="report every worker home's live model")
    use_p = sub.add_parser("use", help="switch every worker home to a target")
    use_p.add_argument("target", help="<role> or <model>@<role>")
    use_p.add_argument("--now", action="store_true",
                       help="stop live loops (watchdogs first, then runners)")
    use_p.add_argument("--skip-probe", action="store_true",
                       help="skip probe verification (air-gapped) — says so")
    sub.add_parser("restore", help="restore the newest backup per home")
    args = ap.parse_args(argv)

    home_base = Path.home()
    registry_path = Path(os.environ.get("ILK_ROLE_REGISTRY") or DEFAULT_REGISTRY)
    data_home = Path(os.environ.get("ILK_DATA_HOME")
                     or Path(home_base / ".ilk-data"))

    if args.cmd == "show":
        return cmd_show(home_base, registry_path)
    if args.cmd == "use":
        return cmd_use(args.target, args.now, args.skip_probe,
                       home_base, registry_path, data_home)
    return cmd_restore(home_base)


if __name__ == "__main__":
    sys.exit(main())
