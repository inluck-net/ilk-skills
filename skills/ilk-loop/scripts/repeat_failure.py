#!/usr/bin/env python3
"""Detect and file repeated failures as improvement backlog rows.

When the same failure ends two consecutive runs of the same slug at the
same step, this module files ONE evidence-carrying backlog row via
``improvement_backlog.add_candidate``.

Two repeat kinds:

* **revert** — a revert row in run A and one in run B with equal
  ``(site, from_step, ship_commit)``.
* **red-gate** — the last gate row of run A at ``(slug, step)`` and
  the last gate row of run B at ``(slug, step)`` both have ``outcome``
  in ``{"fail","error"}`` and the same non-empty ``head_sha``.

Run order per slug: the distinct ``run_id``s that have any row for that
slug, ordered by their earliest ``timestamp``.  Pairs are adjacent
entries ``(A, B)`` in that order.

CLI::

    python3 repeat_failure.py --project-data <D> [--file] [--backlog-dir X] [--host H]

Without ``--file`` it only detects (dry).  Prints one JSON object::

    {"status":"ok","repeats":N,"filed":[source_id...],"already_filed":[...]}

Exits 0 on success, 3 on unreadable input.

Python 3.9 compatible.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── imports ──────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve()
_FEEDBACK_SCRIPTS = _HERE.parent.parent.parent / "ilk-feedback" / "scripts"
if str(_FEEDBACK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_FEEDBACK_SCRIPTS))

import improvement_backlog  # noqa: E402

# ── constants ────────────────────────────────────────────────────────────────

_FILED_STATE_NAME = "repeat-failure-filed.json"
_SOURCE = "repeat-detector"
_KIND = "bug"
_SEVERITY = "high"
_LEVERAGE = "high"
_LOG_PREFIX = "repeat-detector"

# ── data ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Repeat:
    """One detected repeat pair."""

    kind: str  # "revert" or "red-gate"
    slug: str
    step: int | None
    run_a: str
    run_b: str
    signature_key: tuple  # hashable dedup key
    row_a: dict[str, Any]
    row_b: dict[str, Any]


# ── helpers ──────────────────────────────────────────────────────────────────


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], None] | tuple[None, dict]:
    """Read a JSONL file. Returns (rows, None) or (None, error_dict).

    A missing file returns ([], None) — not an error.
    A non-JSON line returns (None, {"status": "unreadable", "file": ..., "line": N}).
    """
    if not path.exists():
        return [], None
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rows.append(json.loads(raw))
                except json.JSONDecodeError:
                    return None, {
                        "status": "unreadable",
                        "file": str(path),
                        "line": lineno,
                    }
    except OSError as exc:
        return None, {
            "status": "unreadable",
            "file": str(path),
            "line": 0,
            "error": str(exc),
        }
    return rows, None


def _source_id(key: tuple) -> str:
    raw = "|".join(str(k) for k in key)
    return "repeat:" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def _ts_sort_key(row: dict[str, Any]) -> str:
    return str(row.get("timestamp", ""))


def _latest_per_step(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, int], dict[str, Any]]:
    """Map (run_id, slug, step) → the latest gate row at that position."""
    best: dict[tuple[str, str, int], dict[str, Any]] = {}
    for r in rows:
        rid = r.get("run_id", "")
        slug = r.get("slug", "")
        step = r.get("step")
        if step is None:
            continue
        key = (rid, slug, int(step))
        prev = best.get(key)
        if prev is None or _ts_sort_key(r) > _ts_sort_key(prev):
            best[key] = r
    return best


# ── core detection ───────────────────────────────────────────────────────────


def detect(
    gate_rows: list[dict[str, Any]],
    revert_rows: list[dict[str, Any]],
) -> list[Repeat]:
    """Find repeat pairs from pre-loaded gate and revert rows.

    Pure: reads no files, writes nothing.
    """
    repeats: list[Repeat] = []
    seen_keys: set[tuple] = set()

    # ── revert repeats ───────────────────────────────────────────────────
    by_slug_reverts: dict[str, list[dict[str, Any]]] = {}
    for r in revert_rows:
        by_slug_reverts.setdefault(r.get("slug", ""), []).append(r)

    for slug, rows in by_slug_reverts.items():
        run_ids = sorted(
            {r["run_id"] for r in rows},
            key=lambda rid: _ts_sort_key(
                min(
                    (r for r in rows if r["run_id"] == rid),
                    key=_ts_sort_key,
                )
            ),
        )
        run_rows: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            run_rows.setdefault(r["run_id"], []).append(r)

        for i in range(len(run_ids) - 1):
            a_id, b_id = run_ids[i], run_ids[i + 1]
            for ra in run_rows.get(a_id, []):
                for rb in run_rows.get(b_id, []):
                    if (
                        ra.get("site") == rb.get("site")
                        and ra.get("from_step") == rb.get("from_step")
                        and ra.get("ship_commit") == rb.get("ship_commit")
                    ):
                        step = ra.get("from_step")
                        sig = ("revert", slug, ra["site"], step, ra["ship_commit"])
                        if sig not in seen_keys:
                            seen_keys.add(sig)
                            repeats.append(
                                Repeat(
                                    kind="revert",
                                    slug=slug,
                                    step=step,
                                    run_a=a_id,
                                    run_b=b_id,
                                    signature_key=sig,
                                    row_a=ra,
                                    row_b=rb,
                                )
                            )

    # ── red-gate repeats ─────────────────────────────────────────────────
    latest = _latest_per_step(gate_rows)
    slug_gate_runs: dict[str, dict[str, list[tuple[int, str]]]] = {}
    for (rid, slug, step), row in latest.items():
        slug_gate_runs.setdefault(slug, {}).setdefault(rid, []).append(
            (step, row.get("timestamp", ""))
        )

    for slug, run_map in slug_gate_runs.items():
        run_ids = sorted(run_map, key=lambda rid: min(run_map[rid], key=lambda x: x[1])[1])
        for i in range(len(run_ids) - 1):
            a_id, b_id = run_ids[i], run_ids[i + 1]
            steps_a = {s for s, _ in run_map.get(a_id, [])}
            steps_b = {s for s, _ in run_map.get(b_id, [])}
            for step in sorted(steps_a & steps_b):
                ra = latest.get((a_id, slug, step))
                rb = latest.get((b_id, slug, step))
                if ra is None or rb is None:
                    continue
                if ra.get("outcome") not in ("fail", "error"):
                    continue
                if rb.get("outcome") not in ("fail", "error"):
                    continue
                sha_a = ra.get("head_sha")
                sha_b = rb.get("head_sha")
                if not sha_a or sha_a != sha_b:
                    continue
                sig = ("red-gate", slug, step, sha_a)
                if sig not in seen_keys:
                    seen_keys.add(sig)
                    repeats.append(
                        Repeat(
                            kind="red-gate",
                            slug=slug,
                            step=step,
                            run_a=a_id,
                            run_b=b_id,
                            signature_key=sig,
                            row_a=ra,
                            row_b=rb,
                        )
                    )

    return repeats


# ── filing ───────────────────────────────────────────────────────────────────


def _load_filed_state(project_data_dir: Path) -> dict[str, list[list[str]]]:
    p = project_data_dir / "runtime" / "launcher" / _FILED_STATE_NAME
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_filed_state(project_data_dir: Path, state: dict[str, list[list[str]]]) -> None:
    p = project_data_dir / "runtime" / "launcher" / _FILED_STATE_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _find_integrity_message(
    project_data_dir: Path, slug: str, run_ids: list[str],
) -> str | None:
    logs_dir = project_data_dir / "logs" / "launcher"
    if not logs_dir.is_dir():
        return None
    # Resolve project key from directory name
    project_key = project_data_dir.name
    prefix = f"[ship-integrity VIOLATION] {slug}:"
    for rid in run_ids:
        log_path = logs_dir / f"{project_key}-{rid}.log"
        if not log_path.is_file():
            continue
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if prefix in line:
                        return line.strip()
        except OSError:
            continue
    return None


def _read_subplan_progress(
    plans_dir: Path, slug: str,
) -> tuple[int | None, int | None]:
    """Read current_step and estimated_steps from the sub-plan frontmatter."""
    if not plans_dir.is_dir():
        return None, None
    for p in plans_dir.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        fm: dict[str, str] = {}
        end = text.find("\n---", 3)
        if end < 0:
            continue
        for line in text[3:end].splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
        if fm.get("plan") == slug:
            cur = fm.get("current_step")
            est = fm.get("estimated_steps")
            return (int(cur) if cur and cur.isdigit() else None,
                    int(est) if est and est.isdigit() else None)
    return None, None


def _find_master_filename(plans_dir: Path, subplan_filename: str) -> str | None:
    if not plans_dir.is_dir():
        return None
    for p in plans_dir.glob("MASTER-*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if subplan_filename in text:
            return p.name
    return None


def file_repeats(
    project_data_dir: Path,
    *,
    backlog_dir: Path | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    """Detect repeats and file new ones as backlog rows.

    Returns a result dict with status, repeats, filed, already_filed.
    Raises SystemExit(3) on unreadable input.
    """
    project_data_dir = Path(project_data_dir)
    if backlog_dir is None:
        backlog_dir = Path.home() / ".ilk-data" / "ilk-skills-improvements"
    else:
        backlog_dir = Path(backlog_dir)
    if host is None:
        host = socket.gethostname()

    # Read inputs
    gate_path = project_data_dir / "runtime" / "launcher" / "gate-history.jsonl"
    reverts_path = project_data_dir / "runtime" / "launcher" / "ship-reverts.jsonl"

    gate_rows, gate_err = _read_jsonl(gate_path)
    if gate_err is not None:
        print(json.dumps(gate_err, ensure_ascii=False))
        raise SystemExit(3)

    revert_rows, revert_err = _read_jsonl(reverts_path)
    if revert_err is not None:
        print(json.dumps(revert_err, ensure_ascii=False))
        raise SystemExit(3)

    # Detect
    repeats = detect(gate_rows, revert_rows)

    # Resolve plans dir and sub-plan progress (best-effort)
    plans_dir = project_data_dir / "plans"
    current_step: int | None = None
    estimated_steps: int | None = None
    master_filename: str | None = None

    # Filed state
    filed_state = _load_filed_state(project_data_dir)
    filed_ids: list[str] = []
    already_ids: list[str] = []

    for rep in repeats:
        sid = _source_id(rep.signature_key)
        pair = [rep.run_a, rep.run_b]

        # Check if already filed
        pairs_for_sid = filed_state.get(sid, [])
        if pair in pairs_for_sid:
            already_ids.append(sid)
            continue

        # Read sub-plan progress for this slug (once per slug, cached)
        if rep.slug:
            current_step, estimated_steps = _read_subplan_progress(
                plans_dir, rep.slug,
            )
            # Find master filename that references this slug's sub-plan
            for sp_file in plans_dir.glob("*.md"):
                if sp_file.name.startswith("MASTER-"):
                    continue
                try:
                    sp_text = sp_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                fm_end = sp_text.find("\n---", 3)
                if fm_end < 0:
                    continue
                fm_text = sp_text[3:fm_end]
                if f"plan: {rep.slug}" in fm_text:
                    master_filename = _find_master_filename(
                        plans_dir, sp_file.name,
                    )
                    break

        # Build evidence
        project_name = project_data_dir.name
        integrity_msg = _find_integrity_message(
            project_data_dir, rep.slug, [rep.run_b, rep.run_a],
        )

        evidence = {
            "project": project_name,
            "run_id": rep.run_b,
            "runs": [rep.run_a, rep.run_b],
            "file": str(reverts_path if rep.kind == "revert" else gate_path),
            "rows": [rep.row_a, rep.row_b],
            "host": host,
            "current_step": current_step,
            "estimated_steps": estimated_steps,
            "master": master_filename,
            "integrity_message": integrity_msg,
        }

        kind_label = rep.kind
        step_label = rep.step if rep.step is not None else rep.row_a.get("from_step", "?")
        title = f"repeated {kind_label} on {rep.slug} step {step_label}"
        gap = (
            f"Repeated {kind_label} on {rep.slug} step {step_label} "
            f"(runs {rep.run_a}, {rep.run_b}): "
            f"{rep.row_a.get('reason', rep.row_a.get('outcome', 'unknown'))}"
        )
        proposed_fix = (
            "A second failure at the same step is a design defect, not state: "
            "fix the step or its gate "
            "(memory relaunch-fixes-state-not-step-design)."
        )

        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

        entry = improvement_backlog.add_candidate(
            title=title,
            kind=_KIND,
            gap=gap,
            evidence=evidence,
            proposed_fix=proposed_fix,
            leverage=_LEVERAGE,
            severity=_SEVERITY,
            source=_SOURCE,
            source_id=sid,
            relations={
                "run_id": rep.run_b,
                "runs": [rep.run_a, rep.run_b],
            },
            backlog_dir=backlog_dir,
        )

        # Update filed state
        pairs_for_sid = filed_state.get(sid, [])
        if pair not in pairs_for_sid:
            pairs_for_sid.append(pair)
        filed_state[sid] = pairs_for_sid
        filed_ids.append(sid)

    # Persist filed state
    _save_filed_state(project_data_dir, filed_state)

    return {
        "status": "ok",
        "repeats": len(repeats),
        "filed": filed_ids,
        "already_filed": already_ids,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect and file repeated failures.",
    )
    parser.add_argument(
        "--project-data", required=True,
        help="Project data directory (<D>)",
    )
    parser.add_argument(
        "--file", action="store_true", dest="do_file",
        help="File new repeats as backlog rows (default: dry detect only)",
    )
    parser.add_argument(
        "--backlog-dir", default=None,
        help="Backlog directory (default: ~/.ilk-data/ilk-skills-improvements)",
    )
    parser.add_argument(
        "--host", default=None,
        help="Host name for evidence (default: socket.gethostname())",
    )
    args = parser.parse_args()

    project_data_dir = Path(args.project_data)

    if not args.do_file:
        # Dry detect
        gate_path = project_data_dir / "runtime" / "launcher" / "gate-history.jsonl"
        reverts_path = project_data_dir / "runtime" / "launcher" / "ship-reverts.jsonl"

        gate_rows, gate_err = _read_jsonl(gate_path)
        if gate_err is not None:
            print(json.dumps(gate_err, ensure_ascii=False))
            return 3

        revert_rows, revert_err = _read_jsonl(reverts_path)
        if revert_err is not None:
            print(json.dumps(revert_err, ensure_ascii=False))
            return 3

        repeats = detect(gate_rows, revert_rows)
        result = {
            "status": "ok",
            "repeats": len(repeats),
            "filed": [],
            "already_filed": [],
        }
    else:
        try:
            result = file_repeats(
                project_data_dir,
                backlog_dir=args.backlog_dir,
                host=args.host,
            )
        except SystemExit as exc:
            return exc.code

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())