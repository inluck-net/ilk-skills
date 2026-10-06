"""Write release permits for every configured host.

Derives the project key, data dir and host list from the toolkit's own
resolvers (``ilk_paths.project_key``, ``ilk_paths.ilk_data_root``) and
``release_train._resolve_hosts`` instead of hard-coding them.

Refuses with exit 2, writing nothing, when any host shows a live loop or
a remote host is unreachable.

Self-checks with ``check_permits_for_dispatch`` from the scheduler.

Python 3.9 compatible, stdlib only (plus toolkit modules).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

# ── Path setup ──────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent.parent / "ilk-loop" / "scripts"
_WATCHDOG_SCRIPTS = _HERE.parent.parent / "ilk-watchdog" / "scripts"

for _p in (str(_LOOP_SCRIPTS), str(_WATCHDOG_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ilk_paths import ilk_data_root, project_key  # noqa: E402
from release_train import _resolve_hosts  # noqa: E402

# ── Probe ───────────────────────────────────────────────────────────────────

# Build the pattern from parts so this script's own argv never matches it.
_LOOP_PATTERN = "run_ilk_loop_claude" + r"\.(sh|ps1)"


def probe_host(host: str, *, local: bool) -> list[str] | str:
    """Check whether *host* has a live loop running.

    Returns:
        A list of PID strings when loops are found (empty = quiet).
        ``"UNREACHABLE"`` when ssh fails with a return code other than 0 or 1.
    """
    if local:
        try:
            r = subprocess.run(
                ["pgrep", "-f", _LOOP_PATTERN],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if r.returncode == 0:
                return [pid.strip() for pid in r.stdout.split() if pid.strip()]
            return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return "UNREACHABLE"

    # Remote host — probe over ssh
    cmd = f"pgrep -f '{_LOOP_PATTERN}' || true"
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             host, cmd],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if r.returncode == 0:
            pids = [pid.strip() for pid in r.stdout.split() if pid.strip()]
            return pids if pids else []
        if r.returncode == 1:
            return []
        return "UNREACHABLE"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "UNREACHABLE"


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point.  Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Write release permits for every configured host.",
    )
    parser.add_argument("--project", required=True, help="Path to the project repo")
    parser.add_argument("--hours", type=float, default=1.0,
                        help="Permit validity in hours (default: 1)")
    parser.add_argument("--hosts", default=None,
                        help="Comma-separated host list override")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written; write nothing")
    args = parser.parse_args(argv)

    repo = Path(args.project).resolve()
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        print(f"REFUSED: {repo} is not a git repository", file=sys.stderr)
        return 2

    pk = project_key(repo)
    data_dir = ilk_data_root() / "projects" / pk

    hosts_override = None
    if args.hosts:
        hosts_override = [h.strip() for h in args.hosts.split(",") if h.strip()]

    hosts = _resolve_hosts(data_dir, repo, hosts=hosts_override)
    if not hosts:
        print("REFUSED: no hosts configured")
        return 2

    # ── Quiet-fleet check ───────────────────────────────────────────────
    fleet_status: dict[str, str] = {}
    for i, host in enumerate(hosts):
        result = probe_host(host, local=(i == 0))
        if isinstance(result, str):
            fleet_status[host] = result
        elif result:
            fleet_status[host] = ",".join(result)

    if fleet_status:
        status_str = " ".join(f"{h}={v}" for h, v in fleet_status.items())
        print(f"REFUSED: fleet not quiet: {status_str}")
        return 2

    # ── Bind ────────────────────────────────────────────────────────────
    head_r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    if head_r.returncode != 0:
        print(f"REFUSED: git rev-parse HEAD failed: {head_r.stderr.strip()}")
        return 2
    candidate_head = head_r.stdout.strip()

    tag_r = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    base_tag = tag_r.stdout.strip() if tag_r.returncode == 0 else ""

    # ── Dry run ─────────────────────────────────────────────────────────
    if args.dry_run:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=args.hours)
        for host in hosts:
            print(f"would write: {data_dir / 'runtime' / 'permits' / f'{host}.json'}")
            print(f"  project={pk} base_tag={base_tag} "
                  f"candidate_head={candidate_head[:12]}…")
            print(f"  issued_at={now.isoformat()} expires_at={expires.isoformat()}")
        return 0

    # ── Write ───────────────────────────────────────────────────────────
    permit_dir = data_dir / "runtime" / "permits"
    permit_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=args.hours)
    utc_stamp = now.strftime("%Y%m%dT%H%M%SZ")

    for host in hosts:
        permit_path = permit_dir / f"{host}.json"

        # Back up existing permit
        if permit_path.exists():
            backup = permit_dir / f"{host}.json.prev-{utc_stamp}"
            permit_path.rename(backup)

        permit = {
            "project": pk,
            "base_tag": base_tag,
            "candidate_head": candidate_head,
            "host": host,
            "issued_at": now.isoformat(timespec="seconds"),
            "expires_at": expires.isoformat(timespec="seconds"),
            "consumed": False,
            "revoked": False,
        }

        tmp_path = permit_dir / f"{host}.json.tmp"
        tmp_path.write_text(json.dumps(permit, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp_path), str(permit_path))

    # ── Self-check ──────────────────────────────────────────────────────
    from release_train_dispatch import check_permits_for_dispatch  # noqa: E402
    result = check_permits_for_dispatch(data_dir, repo)
    print(f"dispatcher check: {result}")

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())