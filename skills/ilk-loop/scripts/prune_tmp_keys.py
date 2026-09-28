#!/usr/bin/env python3
"""prune_tmp_keys — list and move leaked pytest-tmp-derived project keys.

Dry-run by default.  ``--apply`` moves (never deletes) candidates into
``<data>/trash/tmp-keys-<YYYY-MM-DD>/``.  Real project keys are never
candidates — only keys whose prefix matches the current tmp dir's
``project_key()`` slug minus its hash suffix, followed by ``-pytest-of-``,
are considered.

Usage::

    python3 prune_tmp_keys.py                  # dry-run, human output
    python3 prune_tmp_keys.py --json           # dry-run, machine output
    python3 prune_tmp_keys.py --apply          # move candidates to trash
    python3 prune_tmp_keys.py --apply --json   # move + machine output
    python3 prune_tmp_keys.py --fail-if-candidate-prefix users-  # exit 1 if any match

Stdlib only (plus sibling ilk_paths / pid_health).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

# ── sibling imports ───────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ilk_paths import ilk_data_root, project_key  # noqa: E402
from pid_health import pid_alive  # noqa: E402


# ── classifier ───────────────────────────────────────────────────────────────

def _tmp_slug_prefix(extra_roots: list[Path] | None = None) -> str:
    """The prefix slug that all tmp-derived keys share.

    Mirrors conftest.py:724-730.  ``project_key(tmpdir)`` returns
    ``<slug>-<7-char-hash>``; we strip the 8-char hash suffix to get
    the prefix that every child tmp path's key starts with.
    """
    roots = [Path(os.path.realpath(tempfile.gettempdir()))]
    if extra_roots:
        roots.extend(extra_roots)
    # Use the longest prefix (most specific tmp root) as the classifier.
    prefixes = []
    for r in roots:
        full_key = project_key(r)
        prefixes.append(full_key[: len(full_key) - 8])
    return max(prefixes, key=len) if prefixes else ""


def _is_tmp_derived(key: str, prefix: str) -> bool:
    """A key is tmp-derived iff it starts with the tmp slug prefix."""
    return bool(prefix) and key.startswith(prefix)


# ── refusals ─────────────────────────────────────────────────────────────────

def _has_live_pid(data_home: Path, key: str) -> bool:
    """True if ``runtime/launcher/running.pid`` holds a live pid."""
    pid_file = data_home / "projects" / key / "runtime" / "launcher" / "running.pid"
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    return pid_alive(pid)


def _registered_keys(data_home: Path) -> set[str]:
    """Keys that are registered in the launcher's ``projects.json``.

    The canonical registry is ``<skill-root>/ilk-launcher/projects.json``;
    we also check ``<data_home>/launcher/projects.json`` (test convenience).

    For each entry's ``path``, we compute ``project_key(Path(path))`` and
    also check whether the path *itself* is a project key directory (the
    launcher sometimes stores data-home-relative paths).
    """
    json_paths: list[Path] = []
    try:
        from ilk_paths import skill_root
        json_paths.append(skill_root() / "ilk-launcher" / "projects.json")
    except FileNotFoundError:
        pass
    json_paths.append(data_home / "launcher" / "projects.json")

    registered: set[str] = set()
    for p in json_paths:
        if not p.is_file():
            continue
        try:
            entries = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            ep = entry.get("path", "")
            if not ep:
                continue
            ep_path = Path(ep)
            # Key derived from the registered repo path.
            registered.add(project_key(ep_path))
            # If the path is itself a key directory under projects/,
            # its name is also a registered key.
            try:
                rel = ep_path.resolve().relative_to(
                    (data_home / "projects").resolve()
                )
                registered.add(rel.parts[0])
            except (ValueError, OSError):
                pass
    return registered


# ── main logic ───────────────────────────────────────────────────────────────

def _collect(
    data_home: Path,
    prefix: str,
    extra_roots: list[Path] | None,
    *,
    apply: bool,
    fail_prefixes: list[str],
    as_json: bool,
) -> int:
    projects_dir = data_home / "projects"
    if not projects_dir.is_dir():
        if as_json:
            print(json.dumps({"candidates": [], "refused": [], "moved": []}))
        return 0

    reg_keys = _registered_keys(data_home)
    today = date.today().isoformat()

    candidates: list[dict] = []
    refused: list[dict] = []
    moved: list[dict] = []
    all_keys = sorted(e.name for e in projects_dir.iterdir() if e.is_dir())

    for key in all_keys:
        if not _is_tmp_derived(key, prefix):
            continue
        # Refusals
        if _has_live_pid(data_home, key):
            refused.append({"key": key, "reason": "live pid"})
            continue
        if key in reg_keys:
            refused.append({"key": key, "reason": "registered"})
            continue
        # Count files and newest mtime
        key_dir = projects_dir / key
        n_files = 0
        newest = 0.0
        for f in key_dir.rglob("*"):
            if f.is_file():
                n_files += 1
                try:
                    mt = f.stat().st_mtime
                    if mt > newest:
                        newest = mt
                except OSError:
                    pass
        from datetime import datetime, timezone
        newest_iso = (
            datetime.fromtimestamp(newest, tz=timezone.utc).isoformat()
            if newest > 0
            else None
        )
        candidates.append({"key": key, "files": n_files, "newest": newest_iso})

    # --fail-if-candidate-prefix (dry-run only, check before apply)
    if fail_prefixes:
        for c in candidates:
            for pfx in fail_prefixes:
                if c["key"].startswith(pfx):
                    if not as_json:
                        print(f"FAIL: candidate {c['key']} starts with prefix {pfx!r}")
                    else:
                        print(json.dumps({
                            "candidates": candidates,
                            "refused": refused,
                            "moved": [],
                            "fail": {"key": c["key"], "prefix": pfx},
                        }))
                    return 1

    # --apply: move candidates to trash
    if apply and candidates:
        trash_dir = data_home / "trash" / f"tmp-keys-{today}"
        trash_dir.mkdir(parents=True, exist_ok=True)
        for c in candidates:
            src = projects_dir / c["key"]
            dst = trash_dir / c["key"]
            try:
                shutil.move(str(src), str(dst))
                moved.append({"key": c["key"], "dest": str(dst)})
            except OSError as exc:
                if not as_json:
                    print(f"ERROR: move {c['key']}: {exc}", file=sys.stderr)
                else:
                    print(json.dumps({
                        "candidates": candidates,
                        "refused": refused,
                        "moved": moved,
                        "error": {"key": c["key"], "detail": str(exc)},
                    }))
                return 1

    # Output
    if as_json:
        print(json.dumps({
            "candidates": candidates,
            "refused": refused,
            "moved": moved,
        }))
    else:
        for c in candidates:
            newest_str = c["newest"] or "n/a"
            print(f"{c['key']}  files={c['files']}  newest={newest_str}")
        n = len(candidates)
        m = len(all_keys)
        k = len(refused)
        print(f"{n} candidates of {m} keys; {k} refused")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List and move leaked pytest-tmp-derived project keys."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Move candidates to trash/ (default: dry-run only).",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Machine-readable JSON output.",
    )
    parser.add_argument(
        "--tmp-root", action="append", default=[], dest="tmp_roots",
        help="Extra tmp root to derive prefix from (repeatable).",
    )
    parser.add_argument(
        "--fail-if-candidate-prefix", action="append", default=[],
        dest="fail_prefixes",
        help="Exit 1 if any candidate starts with this prefix (dry-run only).",
    )
    args = parser.parse_args()

    data_home = ilk_data_root()
    extra_roots = [Path(r) for r in args.tmp_roots]
    prefix = _tmp_slug_prefix(extra_roots if extra_roots else None)

    return _collect(
        data_home,
        prefix,
        extra_roots if extra_roots else None,
        apply=args.apply,
        fail_prefixes=args.fail_prefixes,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())