#!/usr/bin/env python3
"""Single source of truth for the scheduler blacklist-vs-resolve-ack decision.

Both schedulers (``scheduler.ps1`` ``Read-BlacklistFromPostmortems`` and
``scheduler.sh`` ``read_blacklist_from_postmortems``) consume this, so the
``cleared_at >= generated_at`` logic lives in exactly one place.

Decision (mirrors ``watchdog.ps1``'s newest-postmortem-overall semantics, plus
the resolve-ack override):

- Look at the NEWEST postmortem overall (by mtime). If its ``classification`` is
  not a blacklist class (e.g. a later ``clean-success`` run), NOT blacklisted —
  a clean run un-blacklists, even if an older stuck postmortem exists.
- If the newest postmortem IS a blacklist class:
  - ``now >= generated_at + BACKOFF_MIN`` -> NOT blacklisted (auto-expired; the
    existing 60-min behavior, unchanged).
  - a resolve-ack exists with ``cleared_at >= generated_at`` -> NOT blacklisted
    (the human vouched the fix lands AFTER the failing run — NEW). A stale ack
    older than the failure does NOT clear it.
  - otherwise -> blacklisted, with ``expiry = generated_at + BACKOFF_MIN``.

The resolve-ack sentinel is ``<project_data_dir>/runtime/launcher/blacklist-cleared.json``
with an ISO ``cleared_at``. Postmortems live under
``<project_data_dir>/runtime/launcher/postmortems/<run-id>.md`` with a
``classification`` + ``generated_at`` frontmatter (written by collect.py).

Files are read ``utf-8-sig`` (BOM-tolerant). Import is side-effect free.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

# Blacklist classifications (parity with watchdog.ps1 $BlacklistClasses incl.
# dependency-unreachable added in v0.9.6).
BLACKLIST_CLASSES = {
    "stuck-no-progress",
    "api-blocked",
    "budget-exhausted",
    "local-checks-stuck",
    "local-checks-broken",
    "dependency-unreachable",
}

BACKOFF_MIN = 60

_FM_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.+?)\s*$")


def _postmortems_dir(project_data_dir: str | os.PathLike) -> Path:
    return Path(project_data_dir) / "runtime" / "launcher" / "postmortems"


def _ack_path(project_data_dir: str | os.PathLike) -> Path:
    return Path(project_data_dir) / "runtime" / "launcher" / "blacklist-cleared.json"


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse the YAML-ish frontmatter between the leading --- fences."""
    fm: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            lines = [next(fh) for _ in range(30)] if False else fh.readlines()[:30]
    except (OSError, StopIteration):
        return fm
    in_fm = False
    for line in lines:
        s = line.rstrip("\n")
        if s.strip() == "---":
            if in_fm:
                break
            in_fm = True
            continue
        if in_fm:
            m = _FM_LINE.match(s)
            if m:
                fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return fm


def latest_postmortem(project_data_dir: str | os.PathLike) -> tuple[str | None, str | None] | None:
    """Return (classification, generated_at) of the NEWEST postmortem by mtime,
    or None when there are no postmortems."""
    pm_dir = _postmortems_dir(project_data_dir)
    if not pm_dir.is_dir():
        return None
    mds = [p for p in pm_dir.glob("*.md") if p.is_file()]
    if not mds:
        return None
    newest = max(mds, key=lambda p: p.stat().st_mtime)
    fm = _parse_frontmatter(newest)
    return fm.get("classification"), fm.get("generated_at")


def _parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def read_resume_ack(project_data_dir: str | os.PathLike) -> dt.datetime | None:
    """Return the ack's cleared_at as a naive datetime, or None."""
    p = _ack_path(project_data_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    return _parse_dt(data.get("cleared_at") if isinstance(data, dict) else None)


def is_blacklisted(project_data_dir: str | os.PathLike, now: dt.datetime | None = None) -> dict:
    """Decide whether the project is currently blacklisted (see module docstring)."""
    if now is None:
        now = dt.datetime.now()
    res = {
        "blacklisted": False,
        "reason": "no-postmortem",
        "classification": None,
        "postmortem_generated_at": None,
        "ack_cleared_at": None,
        "expiry": None,
    }
    pm = latest_postmortem(project_data_dir)
    if pm is None:
        return res
    classification, generated_at = pm
    res["classification"] = classification
    res["postmortem_generated_at"] = generated_at

    if classification not in BLACKLIST_CLASSES:
        res["reason"] = "latest-not-blacklist-class"
        return res

    gen = _parse_dt(generated_at)
    if gen is None:
        # Unparseable generated_at: be conservative (mirror the ps1 catch ->
        # now + BACKOFF), but still let a human ack clear it.
        ack = read_resume_ack(project_data_dir)
        if ack is not None:
            res["ack_cleared_at"] = ack.isoformat()
            res["reason"] = "resolved-by-ack"
            return res
        expiry = now + dt.timedelta(minutes=BACKOFF_MIN)
        res.update(blacklisted=True, reason="unparseable-generated_at", expiry=expiry.isoformat())
        return res

    expiry = gen + dt.timedelta(minutes=BACKOFF_MIN)
    res["expiry"] = expiry.isoformat()

    if now >= expiry:
        res["reason"] = "expired"
        return res

    ack = read_resume_ack(project_data_dir)
    if ack is not None:
        res["ack_cleared_at"] = ack.isoformat()
        if ack >= gen:
            res["reason"] = "resolved-by-ack"
            return res

    res["blacklisted"] = True
    res["reason"] = "within-backoff"
    return res


def write_resume_ack(project_data_dir: str | os.PathLike, cleared_at: str | None = None) -> Path:
    """Write the resolve-ack sentinel (atomic, BOM-free). Returns the path."""
    if cleared_at is None:
        cleared_at = dt.datetime.now().isoformat(timespec="seconds")
    path = _ack_path(project_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  # BOM-free
            json.dump({"cleared_at": cleared_at}, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Scheduler blacklist-vs-resolve-ack decision.")
    sub = parser.add_subparsers(dest="cmd")
    p_check = sub.add_parser("check", help="Print the blacklist decision as JSON.")
    p_check.add_argument("--project", required=True, help="Project data dir (~/.ilk-data/projects/<key>).")
    p_ack = sub.add_parser("ack", help="Write the resolve-ack sentinel.")
    p_ack.add_argument("--project", required=True)
    p_ack.add_argument("--cleared-at", default=None, help="ISO timestamp (default: now).")

    args = parser.parse_args()
    if args.cmd == "check":
        print(json.dumps(is_blacklisted(args.project)))
        return 0
    if args.cmd == "ack":
        path = write_resume_ack(args.project, args.cleared_at)
        print(json.dumps({"acked": True, "path": str(path),
                          "cleared_at": read_resume_ack(args.project).isoformat()}))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
