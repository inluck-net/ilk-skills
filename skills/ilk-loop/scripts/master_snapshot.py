#!/usr/bin/env python3
"""master_snapshot — the runner's pre-dispatch copy of every master's state.

A master's ``status`` and park fields belong to the runner and to
``park_master.py``, never to a worker (design §4, I3).  Nothing enforced
that: ``PRE_ITER_ALL_STEPS`` covers sub-plans only, the one-ship check skips
``MASTER*``, and ``reconcile_master_status`` un-parks a master a worker has
marked ``shipped`` (design §7 D3).

The runner takes a snapshot right before it dispatches the agent and checks it
right after the agent returns, before any runner-owned write.  A master whose
owned fields changed is restored to the snapshot, and the runner ends the run
``ship_integrity_violation``.

SCOPE: only the master(s) this run dispatches for (``--master``).  On a
resolver key one runner serves many issue masters, and while it is live on
one of them gh-resolve's daemons legitimately write the others (a pause at
escalation, an un-park of a foreign park, a new master from a handoff).
Restoring every master would undo those decisions and end an unrelated run
``ship_integrity_violation`` -- a false terminal state (gh-resolve-59,
2026-09-29).  A master created during the turn is not in the snapshot, so
``restore`` never touches it.

One change is not a worker's: ``scheduler_scan`` reconciles every master on
every poll, so a ``queued``/``active`` master can move to ``shipped`` while
the agent runs, because its last sub-plan shipped.  That exact transition is
accepted when the sub-plans agree (``is_master_all_shipped``); whether the
sub-plan ship itself was honest is ship-integrity's question, not this one's.

Usage:
  master_snapshot.py take    --plans-dir D --out F --master NAME [--master NAME ...]
  master_snapshot.py restore --plans-dir D --snapshot F

``restore`` prints one JSON object: ``{"restored": [...], "accepted": [...]}``.
Exit 0 = nothing restored; 3 = at least one master was restored; 2 = the
snapshot could not be read or a restore could not be written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_status import is_master_all_shipped, parse_frontmatter  # noqa: E402

#: The fields only the runner and park_master may change.
OWNED_FIELDS = ("status", "parked_at", "parked_reason", "hold", "yield")

_FIELD_RE = re.compile(r"^(" + "|".join(OWNED_FIELDS) + r")\s*:")


def _owned(path: Path) -> dict[str, str | None]:
    """Return the owned fields of one master; an absent field is None.

    Raw line values, not parsed ones: a restore writes back exactly what was
    there, quoting included.
    """
    text = path.read_text(encoding="utf-8-sig")
    out: dict[str, str | None] = {f: None for f in OWNED_FIELDS}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    for line in text[3:end].splitlines():
        m = _FIELD_RE.match(line)
        if m and out[m.group(1)] is None:
            out[m.group(1)] = line.partition(":")[2].strip()
    return out


def take(plans_dir: Path, masters: list[str]) -> dict[str, dict[str, str | None]]:
    """Snapshot the owned fields of the named masters only.

    A named master that does not exist is an error, not an empty snapshot:
    an empty snapshot would verify nothing and read as clean.
    """
    snap: dict[str, dict[str, str | None]] = {}
    for name in masters:
        p = plans_dir / name
        if not p.is_file():
            raise FileNotFoundError(f"master not found: {p}")
        snap[name] = _owned(p)
    return snap


_TOWARD_STOP_FIELDS = ("parked_at", "parked_reason", "hold", "yield")


def _moved_toward_stopping(field: str, before: str | None, after: str | None,
                           now: dict | None = None) -> bool:
    """True when *after* is a move toward stopping that should not be undone.

    ``status: blocked`` counts only when it arrives WITH a park marker
    (``parked_reason`` / ``parked_at`` / ``hold``) -- which is what
    ``park_master.py`` and an operator write.  A bare status flip is a
    worker editing master state, and the restore must still undo it:
    accepting every ``blocked`` (22eb4f0) broke
    test_the_runner_reverts_a_worker_master_edit, and the batch verify
    mis-excused it as red at base (2026-10-03).
    """
    if field == "status":
        if _norm(after) != "blocked":
            return False
        now = now or {}
        return any(_norm(now.get(k)) != "" for k in ("parked_reason", "parked_at", "hold"))
    if field in _TOWARD_STOP_FIELDS:
        return _norm(before) == "" and _norm(after) != ""
    return False


def _norm(v: str | None) -> str:
    """Compare values the way readers see them, not byte-for-byte."""
    if v is None:
        return ""
    fm = parse_frontmatter(f"---\nk: {v}\n---\n")
    return (fm.get("k") or "").strip().lower()


def _write_owned(path: Path, want: dict[str, str | None]) -> None:
    """Rewrite *path*'s owned frontmatter lines to *want*, atomically.

    Every other byte is preserved.  An owned field absent from *want* (None)
    is removed; one absent from the file is appended at the end of the
    frontmatter.
    """
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        raise ValueError(f"{path.name}: no frontmatter block")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError(f"{path.name}: unterminated frontmatter")
    head, rest = text[3:end], text[end:]
    lines = head.splitlines(keepends=True)
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = _FIELD_RE.match(line)
        if not m:
            out.append(line)
            continue
        key = m.group(1)
        if key in seen:
            continue  # a duplicate owned line would shadow the restore
        seen.add(key)
        if want.get(key) is not None:
            out.append(f"{key}: {want[key]}\n")
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    for key in OWNED_FIELDS:
        if key not in seen and want.get(key) is not None:
            out.append(f"{key}: {want[key]}\n")
    new_head = "".join(out)
    if new_head.endswith("\n"):
        new_head = new_head[:-1]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("---" + new_head + rest, encoding="utf-8")
    os.replace(tmp, path)


def restore(plans_dir: Path, snap: dict[str, dict[str, str | None]]) -> dict:
    restored: list[dict] = []
    accepted: list[dict] = []
    errors: list[dict] = []
    for name, before in snap.items():
        path = plans_dir / name
        if not path.is_file():
            # A deleted master is a worker change too, but there is nothing to
            # write the fields back into.  Report it; do not recreate files.
            restored.append({"master": name, "changed": ["<deleted>"],
                             "error": "master file is gone"})
            continue
        try:
            now = _owned(path)
        except OSError as e:
            errors.append({"master": name, "error": f"{type(e).__name__}: {e}"})
            continue
        changed = [f for f in OWNED_FIELDS if _norm(before[f]) != _norm(now[f])]
        if not changed:
            continue
        if (changed == ["status"]
                and _norm(before["status"]) in ("queued", "active", "pending")
                and _norm(now["status"]) == "shipped"
                and is_master_all_shipped(path, plans_dir)):
            accepted.append({"master": name, "from": before["status"],
                             "to": now["status"],
                             "why": "reconcile: every sub-plan is shipped"})
            continue
        # Accept changes that move toward stopping (park, hold, yield, blocked).
        # A worker or operator that parks/holds a master only stops itself;
        # restoring those decisions would be harmful.  (P0: gh-resolve 10-02g
        # held master ran 69 min after restore erased the hold.)
        toward_stop = [f for f in changed
                       if _moved_toward_stopping(f, before[f], now[f], now)]
        if toward_stop:
            for f in toward_stop:
                accepted.append({"master": name, "field": f,
                                 "from": before[f], "to": now[f],
                                 "why": "toward stop"})
            changed = [f for f in changed if f not in toward_stop]
            if not changed:
                continue  # everything was toward-stop; nothing to restore
        row = {"master": name, "changed": changed,
               "before": {f: before[f] for f in changed},
               "after": {f: now[f] for f in changed}}
        try:
            _write_owned(path, before)
        except (OSError, ValueError) as e:
            row["error"] = f"{type(e).__name__}: {e}"
            errors.append(row)
            continue
        restored.append(row)
    return {"restored": restored, "accepted": accepted, "errors": errors}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("take")
    t.add_argument("--plans-dir", type=Path, required=True)
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--master", action="append", required=True,
                   help="a master this run dispatches for (repeatable)")
    r = sub.add_parser("restore")
    r.add_argument("--plans-dir", type=Path, required=True)
    r.add_argument("--snapshot", type=Path, required=True)
    a = ap.parse_args(argv)

    if a.cmd == "take":
        try:
            snap = take(a.plans_dir, a.master)
        except OSError as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
            return 2
        a.out.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        print(json.dumps({"masters": len(snap), "out": str(a.out)}))
        return 0

    try:
        snap = json.loads(a.snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"cannot read snapshot: {type(e).__name__}: {e}",
                          "snapshot": str(a.snapshot)}))
        return 2
    result = restore(a.plans_dir, snap)
    print(json.dumps(result, indent=2))
    if result["errors"]:
        return 2
    return 3 if result["restored"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
