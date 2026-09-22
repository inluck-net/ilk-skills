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


def _ccswitch_cmd(*args: str) -> list[str]:
    """Build a subprocess command for ccswitch_import.

    Tries the bare command on PATH first (respects test fakes and manual
    installs), then falls back to invoking the .py script next to this file
    with the current interpreter.
    """
    bare = shutil.which("ccswitch_import")
    if bare:
        return [bare, *args]
    return [sys.executable, str(SCRIPT_DIR / "ccswitch_import.py"), *args]

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


def _homes_for_roles(roles: dict, home_base: Path) -> list:
    """Collect every home referenced by a registry role, including non-worker roles.

    For worker-tier roles, also sweeps numeric worker slots (e.g.
    ~/.claude-worker-2) that share the same base path.

    Returns a deduplicated list of Path objects for homes that exist on disk.
    """
    seen = set()
    homes = []
    for role_name, role in roles.items():
        home = expand_home(str(role.get("home", "")), home_base)
        resolved = home.resolve()
        if resolved not in seen and home.is_dir():
            seen.add(resolved)
            homes.append(home)
        # For worker-tier roles, also sweep numeric slots.
        if role.get("tier") == "worker":
            # home.name is like ".claude-worker", so we look for
            # ".claude-worker-<digits>" siblings.
            pattern = f"{home.name}-*"
            for child in sorted(home_base.glob(pattern)):
                suffix = child.name[len(f"{home.name}-"):]
                if suffix.isdigit() and child.is_dir():
                    child_resolved = child.resolve()
                    if child_resolved not in seen:
                        seen.add(child_resolved)
                        homes.append(child)
    return homes


def load_registry(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    roles = data.get("roles") or {}
    if not isinstance(roles, dict) or not roles:
        raise ValueError(f"role registry {path} has no roles")
    return roles


def load_hosts(path: Path) -> list:
    """Read the ``hosts`` block from the role registry.

    Each entry has ``name`` and ``ssh`` fields — no secrets (no token,
    password, or key_path).  Returns an empty list when the registry has
    no ``hosts`` key.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("hosts") or []


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


def resolve_provider_env(provider_id: str) -> dict:
    """Resolve provider env from ccswitch_import export --machine.

    Returns a dict with ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN,
    ANTHROPIC_MODEL, and any extra env fields.  Raises ValueError
    if the provider is not found or has incomplete env.
    """
    try:
        result = subprocess.run(
            _ccswitch_cmd("export", "--provider", provider_id, "--machine"),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except FileNotFoundError:
        raise ValueError("ccswitch_import not found on PATH")
    if result.returncode != 0:
        raise ValueError(f"ccswitch_import export failed: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ValueError(f"ccswitch_import returned invalid JSON: {result.stdout[:200]}")
    # The export payload includes metadata (id, name, category, is_official)
    # plus the env fields.  We only want the env fields.
    env_keys = {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"}
    env = {k: v for k, v in data.items() if k.startswith("ANTHROPIC_")}
    missing = env_keys - set(env.keys())
    if missing:
        raise ValueError(f"provider {provider_id!r} missing env fields: {missing}")
    return env


def resolve_provider_env_or_home(
    provider_id: str, home: Path, *, fallback_model: str | None = None
) -> dict:
    """Resolve provider env from ccswitch_import, falling back to the home.

    If ccswitch_import is not available or fails, reads the env block
    from the home's settings.json as a fallback.  When *fallback_model*
    is given, the fallback overrides ANTHROPIC_MODEL with it — without
    this, the fallback always returns the home's stale model, which makes
    every ``use`` call a silent no-op on hosts where CCSwitch is not
    installed.
    """
    try:
        return resolve_provider_env(provider_id)
    except ValueError:
        pass
    # Fallback: read from the home, optionally overriding the model.
    env = read_env_block(home)
    if not env and fallback_model:
        # Official-auth homes have an empty env block and no CCSwitch
        # provider env.  Return a minimal dict so the caller can still
        # update the model (via the top-level settings.json "model" key).
        return {"ANTHROPIC_MODEL": fallback_model}
    if not env:
        raise ValueError(
            f"ccswitch_import unavailable and home {home} has no env block"
        )
    if fallback_model:
        env = {**env, "ANTHROPIC_MODEL": fallback_model}
    return env


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

def cmd_show(home_base: Path, registry_path: Path, as_json: bool = False) -> int:
    roles = None
    try:
        roles = load_registry(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if not as_json:
            print(f"[registry] unreadable: {exc}", file=sys.stderr)

    homes = worker_homes(home_base)
    if as_json:
        entries = []
        for home in homes:
            env = read_env_block(home)
            model = env.get("ANTHROPIC_MODEL", "(unset)")
            host = base_url_host(env.get("ANTHROPIC_BASE_URL", ""))
            entry = {"home": str(home), "model": model, "base_url_host": host}
            if roles is not None:
                for role_name, role in roles.items():
                    role_home = expand_home(str(role.get("home", "")), home_base)
                    if role_home == home and role.get("model") != model:
                        entry["mismatch"] = {
                            "role": role_name,
                            "registry_model": role.get("model"),
                        }
            entries.append(entry)
        print(json.dumps(entries, indent=2))
        return EXIT_OK

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


# ── credential cache (SP4 — one-command-every-host) ────────────────────────


def cmd_providers_cache(
    out_path: Path | None,
    push_hosts: list[str] | None,
    registry_path: Path,
    home_base: Path,
    data_home: Path,
    transport=None,
) -> int:
    """Write a ``providers.json`` credential cache (0600) and optionally
    push it to remote hosts.

    The cache contains full provider env blocks (including tokens) so a
    remote host without a cc-switch store can still switch providers.
    It is private to this repo's tooling — never referenced by the
    published contract of SP5.
    """
    import subprocess as _sp
    transport = transport or SshTransport()

    # Read providers from ccswitch_import with full tokens.
    try:
        result = _sp.run(
            _ccswitch_cmd("list", "--format", "json"),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except FileNotFoundError:
        print("error: ccswitch_import not found on PATH", file=sys.stderr)
        return EXIT_USAGE
    if result.returncode != 0:
        print(f"error: ccswitch_import failed: {result.stderr}",
              file=sys.stderr)
        return EXIT_USAGE

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"error: ccswitch_import returned invalid JSON: "
              f"{result.stdout}", file=sys.stderr)
        return EXIT_USAGE

    # Build the cache entries — same shape as the provider export but
    # with the full env block (base_url, auth_token, model).
    cache = []
    for p in raw:
        cache.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "base_url": p.get("base_url", ""),
            "auth_token": p.get("auth_token", ""),
            "model": p.get("model", ""),
        })

    # Default output path: next to the role registry.
    if out_path is None:
        out_path = registry_path.parent / "providers.json"

    # Write with 0600 permissions (owner read/write only).
    out_path.write_text(json.dumps(cache, indent=2) + "\n",
                        encoding="utf-8")
    out_path.chmod(0o600)
    print(f"[cache] wrote {out_path} (0600, {len(cache)} providers)")

    # Push to remote hosts if requested.
    if push_hosts:
        try:
            hosts = load_hosts(registry_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: cannot read hosts from registry: {exc}",
                  file=sys.stderr)
            return EXIT_USAGE

        target_hosts = [h for h in hosts
                        if h.get("name") in push_hosts
                        or h.get("ssh") in push_hosts]
        if not target_hosts:
            print(f"error: no matching hosts for {push_hosts}",
                  file=sys.stderr)
            return EXIT_USAGE

        for host_entry in target_hosts:
            host_name = host_entry.get("name") or host_entry.get("ssh", "")
            ssh_target = host_entry.get("ssh", host_name)
            env = {"HOME": str(home_base)}
            # Write the cache on the remote host via ssh + cat.
            cache_content = out_path.read_text(encoding="utf-8")
            remote_path = f"$HOME/.ilk-data/providers.json"
            cmd = ["sh", "-c",
                   f'mkdir -p "$(dirname {remote_path})" && '
                   f'cat > {remote_path} && chmod 600 {remote_path}']
            try:
                proc = transport.run(ssh_target, cmd, env,
                                     input=cache_content)
                if proc.returncode == 0:
                    print(f"[cache] pushed to {host_name}:{remote_path}")
                else:
                    print(f"[cache] push to {host_name} failed: "
                          f"{proc.stderr}", file=sys.stderr)
            except Exception as exc:
                print(f"[cache] push to {host_name} error: {exc}",
                      file=sys.stderr)

    return EXIT_OK


# ── host fan-out (SP4 — one-command-every-host) ────────────────────────────


class SshTransport:
    """Run a command on a remote host via ssh(1).

    Does NOT use timeout(1)/gtimeout(1) — AC3.  The caller is responsible
    for any time-boxing.
    """

    def run(self, host: str, cmd: list[str], env: dict,
            timeout: float = 60.0, input: str | None = None,
            ) -> subprocess.CompletedProcess:
        # Inject the script directory so the remote side can find
        # worker_model.py without computing a relative path from HOME.
        env = dict(env)
        env.setdefault("ILK_SCRIPT_DIR", str(SCRIPT_DIR))
        # Build environment prefix: VAR='value' ssh host -- cmd ...
        env_parts = []
        for key, val in sorted(env.items()):
            env_parts.append(f"{key}={val}")
        remote_cmd = env_parts + cmd
        return subprocess.run(
            ["ssh", host, "--", *remote_cmd],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, input=input,
        )


def _resolve_transport(name: str | None):
    """Return a transport instance by name (for test injection via CLI)."""
    if name == "local":
        return _LocalTransport()
    return SshTransport()


class _LocalTransport:
    """Run a command locally (test double for SshTransport).

    Sets ILK_SCRIPT_DIR so ``sh -c 'python3 "$ILK_SCRIPT_DIR/…"'`` resolves.
    """

    def run(self, host: str, cmd: list[str], env: dict,
            timeout: float = 60.0, input: str | None = None,
            ) -> subprocess.CompletedProcess:
        full_env = dict(os.environ)
        full_env.update(env)
        full_env.setdefault("ILK_SCRIPT_DIR", str(SCRIPT_DIR))
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=full_env, timeout=timeout, input=input,
        )


def cmd_show_host_all(
    home_base: Path,
    registry_path: Path,
    data_home: Path,
    as_json: bool,
    transport=None,
) -> int:
    """Fan out ``show --json`` to every host in the registry's hosts block.

    Each host runs its own probes locally (via ssh).  The report names
    every host with its own state — no blanket "done" when any host fails.
    Exit non-zero when any host is unreachable (AC2).
    """
    transport = transport or SshTransport()
    try:
        hosts = load_hosts(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read role registry: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not hosts:
        print("no hosts in registry", file=sys.stderr)
        return EXIT_USAGE

    results: list[dict] = []
    any_unreachable = False
    for host_entry in hosts:
        host_name = host_entry.get("name") or host_entry.get("ssh", "")
        ssh_target = host_entry.get("ssh", host_name)

        # ILK_SCRIPT_DIR is injected by the transport so the remote side
        # can locate worker_model.py without a relative path from HOME.
        env = {
            "HOME": str(home_base),
            "ILK_ROLE_REGISTRY": str(registry_path),
            "ILK_DATA_HOME": str(data_home),
        }
        cmd = ["sh", "-c",
               'python3 "$ILK_SCRIPT_DIR/worker_model.py" show --json']

        try:
            proc = transport.run(ssh_target, cmd, env)
        except subprocess.TimeoutExpired:
            results.append({"host": host_name, "error": "timeout"})
            any_unreachable = True
            continue

        if proc.returncode != 0:
            results.append({
                "host": host_name,
                "error": proc.stderr.strip() or f"exit {proc.returncode}",
            })
            any_unreachable = True
            continue

        try:
            host_homes = json.loads(proc.stdout)
        except json.JSONDecodeError:
            results.append({
                "host": host_name,
                "error": f"invalid JSON from remote: {proc.stdout[:200]}",
            })
            any_unreachable = True
            continue

        results.append({"host": host_name, "homes": host_homes})

    # Print the report.
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for entry in results:
            host_name = entry["host"]
            if "error" in entry:
                print(f"  {host_name}: UNREACHABLE — {entry['error']}")
            else:
                for home in entry.get("homes", []):
                    h = home.get("home", "")
                    model = home.get("model", "")
                    url_host = home.get("base_url_host", "")
                    mismatch = home.get("mismatch")
                    line = f"  {host_name}: {h} model={model}  base-url-host={url_host}"
                    if mismatch:
                        line += (f"  !! MISMATCH: registry role "
                                 f"'{mismatch['role']}' says "
                                 f"{mismatch['registry_model']}")
                    print(line)

    return EXIT_USAGE if any_unreachable else EXIT_OK


# ── roles & providers (SP3 — roles-and-providers-enumerable) ────────────────


def cmd_roles(home_base: Path, registry_path: Path, as_json: bool) -> int:
    """List every registry role with its resolved home, model, tier, and auth."""
    try:
        roles = load_registry(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read role registry: {exc}", file=sys.stderr)
        return EXIT_USAGE

    entries = []
    for role_name in sorted(roles):
        role = roles[role_name]
        home = expand_home(str(role.get("home", "")), home_base)
        env = read_env_block(home) if home.is_dir() else {}
        entries.append({
            "name": role_name,
            "tier": role.get("tier", ""),
            "home": str(home),
            "model": env.get("ANTHROPIC_MODEL", role.get("model", "")),
            "auth": role.get("auth", "custom"),
        })

    if as_json:
        print(json.dumps(entries, indent=2))
    else:
        for e in entries:
            print(f"  {e['name']}  tier={e['tier']}  home={e['home']}")
            print(f"    model: {e['model']}  auth: {e['auth']}")
    return EXIT_OK


def cmd_roles_show(name: str, home_base: Path, registry_path: Path,
                   as_json: bool) -> int:
    """Show one role by name.  AC4: exit non-zero and list all valid values
    when the name is unknown."""
    try:
        roles = load_registry(registry_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read role registry: {exc}", file=sys.stderr)
        return EXIT_USAGE

    role = roles.get(name)
    if role is None:
        valid = sorted(roles.keys())
        print(f"error: '{name}' is not a role in the registry; "
              f"known: {', '.join(valid)}", file=sys.stderr)
        return EXIT_USAGE

    home = expand_home(str(role.get("home", "")), home_base)
    env = read_env_block(home) if home.is_dir() else {}
    entry = {
        "name": name,
        "tier": role.get("tier", ""),
        "home": str(home),
        "model": env.get("ANTHROPIC_MODEL", role.get("model", "")),
        "auth": role.get("auth", "custom"),
    }

    if as_json:
        print(json.dumps(entry, indent=2))
    else:
        print(f"  {entry['name']}  tier={entry['tier']}  home={entry['home']}")
        print(f"    model: {entry['model']}  auth: {entry['auth']}")
    return EXIT_OK


def cmd_providers(as_json: bool) -> int:
    """List every CCSwitch Claude provider with token_present (never the raw token)."""
    import subprocess as _sp
    try:
        result = _sp.run(
            _ccswitch_cmd("list", "--format", "json"),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        print("error: ccswitch_import not found on PATH", file=sys.stderr)
        return EXIT_USAGE
    if result.returncode != 0:
        print(f"error: ccswitch_import failed: {result.stderr}", file=sys.stderr)
        return EXIT_USAGE

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"error: ccswitch_import returned invalid JSON: {result.stdout}",
              file=sys.stderr)
        return EXIT_USAGE

    entries = []
    for p in raw:
        token_raw = p.get("auth_token", "")
        entries.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "model": p.get("model", ""),
            "base_url": p.get("base_url", ""),
            "token_present": bool(token_raw and token_raw != "(missing)"),
        })

    if as_json:
        print(json.dumps(entries, indent=2))
    else:
        for e in entries:
            present = "yes" if e["token_present"] else "no"
            print(f"  {e['id']}  {e['name']}")
            print(f"    model: {e['model']}  base_url: {e['base_url'] or '(not set)'}"
                  f"  token: {present}")
    return EXIT_OK


def cmd_providers_show(name: str, as_json: bool) -> int:
    """Show one provider by id or name.  AC4: exit non-zero and list all
    valid values when the name is unknown."""
    import subprocess as _sp
    try:
        result = _sp.run(
            _ccswitch_cmd("list", "--format", "json"),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        print("error: ccswitch_import not found on PATH", file=sys.stderr)
        return EXIT_USAGE
    if result.returncode != 0:
        print(f"error: ccswitch_import failed: {result.stderr}", file=sys.stderr)
        return EXIT_USAGE

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"error: ccswitch_import returned invalid JSON: {result.stdout}",
              file=sys.stderr)
        return EXIT_USAGE

    # Match by id or name (case-insensitive).
    match = None
    for p in raw:
        if p.get("id") == name or p.get("name", "").lower() == name.lower():
            match = p
            break

    if match is None:
        valid = sorted({p.get("id", "") for p in raw} |
                       {p.get("name", "") for p in raw} - {""})
        print(f"error: '{name}' is not a provider; "
              f"known: {', '.join(valid)}", file=sys.stderr)
        return EXIT_USAGE

    token_raw = match.get("auth_token", "")
    entry = {
        "id": match.get("id", ""),
        "name": match.get("name", ""),
        "model": match.get("model", ""),
        "base_url": match.get("base_url", ""),
        "token_present": bool(token_raw and token_raw != "(missing)"),
    }

    if as_json:
        print(json.dumps(entry, indent=2))
    else:
        present = "yes" if entry["token_present"] else "no"
        print(f"  {entry['id']}  {entry['name']}")
        print(f"    model: {entry['model']}  base_url: {entry['base_url'] or '(not set)'}"
              f"  token: {present}")
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

    For every worker-tier role whose home matches one of the homes we just
    switched, update the model and provider to match the source role.
    Non-worker roles (planner, manager) are skipped: they use their own
    auth mechanisms and their model is not controlled by CCSwitch.
    Atomic: writes to a tmp file then renames.  Preserves 2-space indent
    and key order.
    """
    # Build a set of switched home paths for matching.
    switched = {h.resolve() for h in homes}

    # Read the raw JSON so we can preserve key order.
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    changed = False
    for role_name, role in data.get("roles", {}).items():
        # Skip non-worker roles — their model is not controlled by the
        # CCSwitch provider switch.
        if role.get("tier") != "worker":
            continue
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


def probe_config(home: Path, timeout: float = 30.0,
                  extra_env: dict | None = None) -> tuple[bool, str]:
    """Read the model from the stream-json init event — what the process
    loaded, not what the model says about itself.

    Returns (ok, detail): ok=True when the first event's model field is
    non-empty, detail is the model string (or an error message).
    """
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    env["CLAUDE_CONFIG_DIR"] = str(home)
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "stream-json",
             "--verbose", "--max-turns", "1", "hi"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
    except FileNotFoundError:
        return False, "claude not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "probe timed out"
    if result.returncode != 0:
        return False, f"exit {result.returncode}"
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and "model" in event:
            return True, str(event["model"])
    return False, "no init event found"


def probe_live(home: Path, timeout: float = 30.0,
               extra_env: dict | None = None) -> tuple[bool, str]:
    """One real round-trip to confirm auth and quota are alive.

    Returns (ok, detail): ok=True when the call succeeds, detail is the
    result text or a diagnostic string on failure (403, 429, etc.).
    """
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    env["CLAUDE_CONFIG_DIR"] = str(home)
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--max-turns", "1",
             "Say OK"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout,
        )
    except FileNotFoundError:
        return False, "claude not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "probe timed out"
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        if result.returncode != 0:
            return False, f"exit {result.returncode}, non-JSON output"
        return False, "non-JSON output from live probe"
    if data.get("is_error"):
        return False, data.get("result", "unknown error")
    return True, data.get("result", "ok")


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

    # Resolve provider env from ccswitch_import, falling back to the home.
    provider_id = role.get("provider", "")
    if not provider_id:
        print(f"error: role '{role_name}' has no provider in registry",
              file=sys.stderr)
        return EXIT_USAGE
    source_home = expand_home(str(role.get("home", "")), home_base)
    try:
        target_env = resolve_provider_env_or_home(
            provider_id, source_home, fallback_model=model)
    except ValueError as exc:
        print(f"error: cannot resolve provider {provider_id!r}: {exc}",
              file=sys.stderr)
        return EXIT_USAGE

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

    # Switch only the target role's homes — its declared home and any
    # numeric siblings (e.g. ~/.claude-worker, ~/.claude-worker-1, -2).
    # Scoped to the named role: use mimo-v2.6-pro@coder must never touch
    # ~/.claude or ~/.claude-manager.  Sweeping all roles redirected the
    # planner to the worker's endpoint.  Measured on chad-mbp 2026-09-22.
    #
    # Match exact name or name-<digits> (numeric worker slots).  A bare
    # prefix match is wrong: .claude-manager.startswith(".claude") is True,
    # so use opus@planner would sweep the manager and worker homes too.
    target_base = source_home.resolve()
    target_name = target_base.name

    def _is_target_sibling(h: Path) -> bool:
        hr = h.resolve()
        if hr == target_base:
            return True
        if hr.parent != target_base.parent:
            return False
        name = hr.name
        if not name.startswith(target_name + "-"):
            return False
        suffix = name[len(target_name) + 1:]
        return suffix.isdigit()

    homes = [h for h in _homes_for_roles(roles, home_base)
             if _is_target_sibling(h)]
    if not homes:
        homes = [source_home] if source_home.is_dir() else []
    if not homes:
        print(f"error: no homes found for registry roles under {home_base}",
              file=sys.stderr)
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
        data["model"] = model
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"[switch] {home}: env rewritten (backup {backup.name})")

    if skip_probe:
        print("[probe] SKIPPED (--skip-probe) — switch is UNVERIFIED")
    else:
        target_model = target_env.get("ANTHROPIC_MODEL", model)
        worker_home_resolved = {h.resolve() for h in worker_homes(home_base)}
        for home in homes:
            cfg_ok, cfg_detail = probe_config(home)
            live_ok, live_detail = probe_live(home)
            # Config-mismatch check only for worker-tier homes.  Non-worker
            # roles (planner, manager) use their own auth mechanisms
            # (official/OAuth) and the probe session may report a different
            # model than the env block we just wrote — that is expected, not
            # a rollback condition.
            if home.resolve() in worker_home_resolved:
                if cfg_ok and cfg_detail != target_model:
                    cfg_ok = False
            if not cfg_ok or not live_ok:
                print(f"error: probe under {home} — "
                      f"config={'OK: ' + cfg_detail if cfg_ok else cfg_detail}, "
                      f"live={'OK' if live_ok else live_detail} — "
                      f"ROLLING BACK", file=sys.stderr)
                for h, b in backups.items():
                    shutil.copy2(b, h / "settings.json")
                    print(f"[rollback] {h}: restored {b.name}", file=sys.stderr)
                return EXIT_PROBE
            print(f"[probe] {home}: config={cfg_detail}, live=OK ✓")

    if skip_probe:
        print("switch is UNVERIFIED — skipped probe.")
    else:
        print("switch verified for every home above.")

    # Sync the role registry: update every role whose home matches a worker
    # home we just switched.  This closes the gap where the registry says one
    # model while the homes run another (retro hazard 6).
    _sync_registry(registry_path, roles, homes, home_base, model, role)

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
    show_p = sub.add_parser("show", help="report every worker home's live model")
    show_p.add_argument("--json", action="store_true", dest="as_json",
                        help="output as JSON")
    show_p.add_argument("--host", default=None,
                        help="fan out to a host or 'all' (reads hosts from registry)")
    show_p.add_argument("--transport", default=None, choices=["ssh", "local"],
                        help="transport for --host (default: ssh; local = test double)")
    use_p = sub.add_parser("use", help="switch every worker home to a target")
    use_p.add_argument("target", help="<role> or <model>@<role>")
    use_p.add_argument("--now", action="store_true",
                       help="stop live loops (watchdogs first, then runners)")
    use_p.add_argument("--skip-probe", action="store_true",
                       help="skip probe verification (air-gapped) — says so")
    sub.add_parser("restore", help="restore the newest backup per home")
    roles_p = sub.add_parser("roles", help="list registry roles")
    roles_p.add_argument("name", nargs="?", default=None,
                         help="role name to show")
    roles_p.add_argument("--json", action="store_true", dest="as_json",
                         help="output as JSON")
    providers_p = sub.add_parser("providers", help="list CCSwitch providers")
    providers_p.add_argument("name", nargs="?", default=None,
                             help="provider id or name to show")
    providers_p.add_argument("--json", action="store_true", dest="as_json",
                             help="output as JSON")
    cache_p = sub.add_parser(
        "providers-cache",
        help="write a 0600 providers.json credential cache")
    cache_p.add_argument("--out", default=None, type=Path,
                         help="output path (default: next to registry)")
    cache_p.add_argument("--push", nargs="*", default=None,
                         help="push cache to named hosts")
    args = ap.parse_args(argv)

    home_base = Path.home()
    registry_path = Path(os.environ.get("ILK_ROLE_REGISTRY") or DEFAULT_REGISTRY)
    data_home = Path(os.environ.get("ILK_DATA_HOME")
                     or Path(home_base / ".ilk-data"))

    if args.cmd == "show":
        if args.host == "all":
            transport = _resolve_transport(getattr(args, "transport", None))
            return cmd_show_host_all(
                home_base, registry_path, data_home, as_json=args.as_json,
                transport=transport)
        return cmd_show(home_base, registry_path, as_json=args.as_json)
    if args.cmd == "use":
        return cmd_use(args.target, args.now, args.skip_probe,
                       home_base, registry_path, data_home)
    if args.cmd == "roles":
        if args.name:
            return cmd_roles_show(args.name, home_base, registry_path,
                                  args.as_json)
        return cmd_roles(home_base, registry_path, args.as_json)
    if args.cmd == "providers":
        if args.name:
            return cmd_providers_show(args.name, args.as_json)
        return cmd_providers(args.as_json)
    if args.cmd == "providers-cache":
        return cmd_providers_cache(
            out_path=getattr(args, "out", None),
            push_hosts=getattr(args, "push", None),
            registry_path=registry_path, home_base=home_base,
            data_home=data_home)
    return cmd_restore(home_base)


if __name__ == "__main__":
    sys.exit(main())
