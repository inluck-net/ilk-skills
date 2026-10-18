#!/usr/bin/env python3
"""Write a batch postmortem from ilk2's versioned JSON alone.

This is the other half of the ilk2-native ``/ilk2-feedback`` (docs/07
retiring-v1.md §7.4.3), built *beside* V1's ``skills/ilk-feedback``.  Where V1's
collector reads run files, this one reads three versioned ilk2 JSON documents
and nothing else::

    ilk2 report  --batch <id> --json     # the attempt records (report_version 2)
    ilk2 status  --batch <id> --json     # the batch's own state
    ilk2 events  --batch <id> --json     # submit warnings and the rest

No V1 data home, and no ilk2 run directory, is read by this module: the only
paths it touches are the ``ilk2`` executable it is given and the output root it
is told to write under.  Null timing fields are unknown, never zero.

The four entry points:

``collect(batch_id, ilk2="ilk2")``
    run exactly those three commands and return the parsed documents.
``render(collected)``
    Markdown with YAML frontmatter, a per-step table, the classification with
    its evidence, phase minutes, refusals and submit warnings.
``write(collected, root=...)``
    ``<root>/<project>/<batch>.md``, overwritten idempotently.  The default
    root is ``Path.home()/".ilk2"/"postmortems"``, resolved at call time.
``findings(collected, ilk2="ilk2", apply=False)``
    one ``ilk2 improvement add`` argv per non-``clean`` label.  They are only
    run when ``apply`` is true.

CLI::

    postmortem.py <batch-id> [--write] [--findings] [--apply] [--ilk2 PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["collect", "render", "write", "findings", "main"]

_SCRIPT_DIR = Path(__file__).resolve().parent
_CLASSIFY_PATH = _SCRIPT_DIR / "classify.py"

# Keys ``render`` puts in the frontmatter, in order.
_FRONTMATTER_KEYS = (
    "batch",
    "project",
    "status",
    "labels",
    "minutes_per_accepted_step",
    "first_attempt_share",
    "report_version",
)

_PLAIN_SCALAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")
_YAML_KEYWORDS = frozenset(
    {"null", "true", "false", "yes", "no", "on", "off", "~"}
)


def _load_classify():
    """Import the sibling classifier by path, under a non-clashing name."""
    spec = importlib.util.spec_from_file_location(
        "ilk2_feedback_classify", _CLASSIFY_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % _CLASSIFY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_classify_mod = _load_classify()
_classify = _classify_mod.classify


# ── collect ──────────────────────────────────────────────────────────────────


def _run_ilk2(ilk2: str, args: List[str]) -> dict:
    """Run one ``ilk2 ... --json`` command and parse its stdout."""
    cmd = [ilk2] + args
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "%s failed with exit %d: %s"
            % (" ".join(cmd), proc.returncode, (proc.stderr or "").strip())
        )
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ValueError(
            "%s did not print JSON: %s" % (" ".join(cmd), exc)
        ) from exc


def collect(batch_id: str, ilk2: str = "ilk2") -> dict:
    """Run the three ilk2 JSON commands for *batch_id* and return the results.

    Exactly three commands, in this order: ``report``, ``status``, ``events``.
    Nothing else is read.  The ``ilk2`` executable is a parameter so a test can
    point this at a stub.
    """
    report = _run_ilk2(ilk2, ["report", "--batch", str(batch_id), "--json"])
    status = _run_ilk2(ilk2, ["status", "--batch", str(batch_id), "--json"])
    events = _run_ilk2(ilk2, ["events", "--batch", str(batch_id), "--json"])
    return {
        "batch_id": str(batch_id),
        "report": report,
        "status": status,
        "events": events,
    }


# ── reading the collected documents ──────────────────────────────────────────


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _batch_row(collected: dict) -> Dict[str, Any]:
    """This batch's row in ``ilk2 status``, or {} when it is not listed."""
    report = _as_dict(collected.get("report"))
    batch_id = collected.get("batch_id")
    if not batch_id:
        batch = report.get("batch")
        batch_id = batch if isinstance(batch, str) else None
    status = _as_dict(collected.get("status"))
    rows = [row for row in _as_list(status.get("batches")) if isinstance(row, dict)]
    for row in rows:
        if batch_id is not None and row.get("id") == batch_id:
            return row
    return {}


def _batch_id(collected: dict) -> str:
    report = _as_dict(collected.get("report"))
    batch = report.get("batch")
    if isinstance(batch, str) and batch:
        return batch
    if isinstance(batch, dict):
        for key in ("id", "batch", "batch_id"):
            value = batch.get(key)
            if isinstance(value, str) and value:
                return value
    return str(collected.get("batch_id") or "unknown")


def _project(collected: dict) -> str:
    value = _batch_row(collected).get("project")
    return value if isinstance(value, str) and value else "unknown"


def _batch_status(collected: dict) -> str:
    value = _batch_row(collected).get("status")
    return value if isinstance(value, str) and value else "unknown"


# ── YAML frontmatter ─────────────────────────────────────────────────────────


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value)
    if _PLAIN_SCALAR.match(text) and text.lower() not in _YAML_KEYWORDS:
        return text
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def _yaml_block(key: str, value: Any) -> List[str]:
    """One frontmatter entry: a scalar line, or a key plus a sequence."""
    if isinstance(value, list):
        if not value:
            return ["%s: []" % key]
        lines = ["%s:" % key]
        for item in value:
            lines.append("  - %s" % _yaml_scalar(item))
        return lines
    return ["%s: %s" % (key, _yaml_scalar(value))]


# ── render ───────────────────────────────────────────────────────────────────


def _cell(value: Any) -> str:
    """A table cell.  Unknown stays unknown: null is never rendered as 0."""
    if value is None:
        return "unknown"
    return str(value)


def _attempts_of(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [a for a in _as_list(step.get("attempt_records")) if isinstance(a, dict)]


def _known_sum(attempts: List[Dict[str, Any]], field: str) -> Optional[int]:
    """Sum of *field* across attempts, or None if any attempt does not say.

    Null is unknown, never zero: one unknown attempt makes the total unknown
    rather than quietly undercounting.
    """
    if not attempts:
        return None
    total = 0
    for attempt in attempts:
        value = attempt.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        total += value
    return total


def _known_max(attempts: List[Dict[str, Any]], field: str) -> Optional[float]:
    if not attempts:
        return None
    best: Any = None
    for attempt in attempts:
        value = attempt.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if best is None or value > best:
            best = value
    return best


def _warning_text(payload: Any) -> str:
    """A short line for a submit.warning payload, whatever its shape."""
    if isinstance(payload, dict):
        for key in ("warning", "name", "reason", "detail", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                extra = [
                    "%s=%s" % (k, v)
                    for k, v in sorted(payload.items())
                    if k != key and not isinstance(v, (dict, list))
                ]
                return value + (" (%s)" % ", ".join(extra) if extra else "")
        return ", ".join(
            "%s=%s" % (k, v) for k, v in sorted(payload.items())
        ) or "(empty)"
    if isinstance(payload, str) and payload:
        return payload
    return json.dumps(payload, sort_keys=True, default=str)


def render(collected: dict) -> str:
    """The postmortem as Markdown with YAML frontmatter."""
    report = _as_dict(collected.get("report"))
    status = _as_dict(collected.get("status"))
    events = _as_dict(collected.get("events"))
    result = _classify(report, status)

    front = {
        "batch": _batch_id(collected),
        "project": _project(collected),
        "status": _batch_status(collected),
        "labels": result["labels"],
        "minutes_per_accepted_step": report.get("minutes_per_accepted_step"),
        "first_attempt_share": report.get("first_attempt_share"),
        "report_version": report.get("report_version"),
    }
    lines: List[str] = ["---"]
    for key in _FRONTMATTER_KEYS:
        lines.extend(_yaml_block(key, front[key]))
    lines.append("---")
    lines.append("")
    lines.append("# Postmortem: %s" % front["batch"])
    lines.append("")

    # per-step table
    steps = [s for s in _as_list(report.get("steps")) if isinstance(s, dict)]
    lines.append("## Steps")
    lines.append("")
    lines.append(
        "| Step | Minutes | Attempts | Red | First edit (min) "
        "| Long requests | Longest request (min) |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for position, step in enumerate(steps, start=1):
        attempts = _attempts_of(step)
        plan = step.get("plan") if isinstance(step.get("plan"), str) else "?"
        idx = step.get("idx")
        if not isinstance(idx, int) or isinstance(idx, bool):
            idx = position
        name = "%s step %d" % (plan, idx)
        attempts_cell = step.get("attempts")
        if not isinstance(attempts_cell, int) or isinstance(attempts_cell, bool):
            attempts_cell = len(attempts) if attempts else None
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                name,
                _cell(step.get("minutes")),
                _cell(attempts_cell),
                _cell(step.get("red")),
                _cell(step.get("first_edit_min")),
                _cell(_known_sum(attempts, "long_requests")),
                _cell(_known_max(attempts, "longest_request_min")),
            )
        )
    if not steps:
        lines.append("| (no steps in this report) | unknown | unknown | unknown | unknown | unknown | unknown |")
    lines.append("")

    # classification and its evidence
    lines.append("## Classification")
    lines.append("")
    if result["labels"]:
        for label in result["labels"]:
            evidence = result["evidence"].get(label, [])
            if evidence:
                lines.append("- **%s** — %s" % (label, "; ".join(evidence)))
            else:
                lines.append("- **%s**" % label)
    else:
        lines.append("- no label in the kept subset matched this report")
    for note in result["notes"]:
        lines.append("- note: %s" % note)
    lines.append("")

    # phase minutes
    lines.append("## Phase minutes")
    lines.append("")
    phases = _as_dict(report.get("phase_minutes"))
    if phases:
        lines.append("| Phase | Minutes |")
        lines.append("|---|---|")
        for name in sorted(phases):
            lines.append("| %s | %s |" % (name, _cell(phases[name])))
    else:
        lines.append("0 phases recorded in this report.")
    lines.append("")

    # refusals
    lines.append("## Refusals")
    lines.append("")
    refusals = [r for r in _as_list(report.get("refusals")) if isinstance(r, dict)]
    if refusals:
        for refusal in refusals:
            lines.append(
                "- %s: %s (opened %s, cleared %s)"
                % (
                    _cell(refusal.get("reason")),
                    _cell(refusal.get("detail")),
                    _cell(refusal.get("opened_at")),
                    _cell(refusal.get("cleared_at")),
                )
            )
    else:
        lines.append("0 refusals recorded in this report.")
    lines.append("")

    # submit warnings from the events
    lines.append("## Submit warnings")
    lines.append("")
    all_events = [e for e in _as_list(events.get("events")) if isinstance(e, dict)]
    warnings = [e for e in all_events if e.get("kind") == "submit.warning"]
    if warnings:
        for event in warnings:
            lines.append(
                "- %s: %s (cursor %s)"
                % (_cell(event.get("at")), _warning_text(event.get("payload")), _cell(event.get("cursor")))
            )
    else:
        lines.append(
            "0 submit warnings in %d event(s) for this batch." % len(all_events)
        )
    lines.append("")
    return "\n".join(lines)


# ── write ────────────────────────────────────────────────────────────────────


def _safe_component(name: str) -> str:
    """One path component: no separators, no traversal."""
    cleaned = re.sub(r"[/\\\0]", "_", str(name)).strip()
    return cleaned if cleaned not in ("", ".", "..") else "unknown"


def write(collected: dict, root: Optional[Path] = None) -> Path:
    """Write the postmortem to ``<root>/<project>/<batch>.md``.

    Idempotent: the same batch rewrites the same file.  The default root is
    ``Path.home()/".ilk2"/"postmortems"``, resolved when this is called (so a
    test can point ``HOME`` at a scratch directory).
    """
    if root is None:
        root = Path.home() / ".ilk2" / "postmortems"
    out_dir = Path(root) / _safe_component(_project(collected))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (_safe_component(_batch_id(collected)) + ".md")
    path.write_text(render(collected), encoding="utf-8")
    return path


# ── findings ─────────────────────────────────────────────────────────────────


def findings(
    collected: dict, ilk2: str = "ilk2", apply: bool = False
) -> List[List[str]]:
    """One ``ilk2 improvement add`` argv per non-``clean`` label.

    The argv is always returned; it is only run when *apply* is true.  The
    fingerprint is ``<label>:<project>`` so a label filed twice for one project
    dedups in ilk2 rather than piling up (docs/07 §7.4.3, D7).
    """
    report = _as_dict(collected.get("report"))
    status = _as_dict(collected.get("status"))
    result = _classify(report, status)
    project = _project(collected)
    batch = _batch_id(collected)

    argvs: List[List[str]] = []
    for label in result["labels"]:
        if label == "clean":
            continue
        evidence = result["evidence"].get(label, [])
        payload = {
            "batch": batch,
            "project": project,
            "label": label,
            "evidence": evidence,
            "notes": result["notes"],
        }
        argvs.append(
            [
                ilk2,
                "improvement",
                "add",
                "--project",
                project,
                "--source",
                "postmortem",
                "--fingerprint",
                "%s:%s" % (label, project),
                "--title",
                "%s on batch %s" % (label, batch),
                "--evidence",
                json.dumps(payload, sort_keys=True),
            ]
        )

    if apply:
        for argv in argvs:
            subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
    return argvs


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Postmortem an ilk2 batch from its report/status/events JSON."
    )
    parser.add_argument("batch_id", help="ilk2 batch id")
    parser.add_argument(
        "--write",
        action="store_true",
        help="also write <root>/<project>/<batch>.md under ~/.ilk2/postmortems",
    )
    parser.add_argument(
        "--findings",
        action="store_true",
        help="print one ilk2 improvement add argv per non-clean label",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="with --findings, run those commands instead of only printing them",
    )
    parser.add_argument(
        "--ilk2", default="ilk2", help="the ilk2 executable to run (default: ilk2)"
    )
    args = parser.parse_args(argv)

    collected = collect(args.batch_id, ilk2=args.ilk2)
    sys.stdout.write(render(collected))

    if args.write:
        path = write(collected)
        sys.stderr.write("wrote %s\n" % path)

    if args.findings:
        argvs = findings(collected, ilk2=args.ilk2, apply=args.apply)
        for item in argvs:
            verb = "ran" if args.apply else "would run"
            sys.stderr.write("findings: %s %s\n" % (verb, " ".join(item)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
