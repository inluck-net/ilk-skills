#!/usr/bin/env python3
"""release_train.py — check eligibility and prove HEAD before cutting a release.

Part of sub-plan: a-release-is-proven-before-it-is-cut.

Verbs:
  check  — is there anything to release, and is it safe to try now?
  prove  — Phase 0 (ship_audit) + Phase 1 (fresh-clone suite, baseline diff)

CLI: release_train.py check|prove --project PATH [--json]

Exit codes: 0 eligible/proven, 3 not eligible, 4 refused, 2 internal error.
Stdlib only (imports verification_record from sibling).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ── path setup ───────────────────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).resolve().parent
_LOOP_SCRIPTS = _SCRIPTS_DIR.parent.parent / "ilk-loop" / "scripts"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only git command with stdlib encoding."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


# ── small helpers ────────────────────────────────────────────────────────

def _get_head_sha(project: Path) -> str:
    r = _git(project, "rev-parse", "HEAD")
    return r.stdout.strip()


def _get_latest_tag(project: Path) -> str | None:
    r = _git(project, "describe", "--tags", "--abbrev=0")
    return r.stdout.strip() if r.returncode == 0 else None


def _tag_sha(project: Path, tag: str) -> str | None:
    """Resolve tag to its commit SHA (dereference annotated tags)."""
    r = _git(project, "rev-parse", f"{tag}^{{}}")
    return r.stdout.strip() if r.returncode == 0 else None


def _is_tree_dirty(project: Path) -> bool:
    r = _git(project, "status", "--porcelain")
    return bool(r.stdout.strip())


def _ref_exists(project: Path, ref: str) -> bool:
    r = _git(project, "rev-parse", "--verify", ref)
    return r.returncode == 0


def _is_ancestor(project: Path, ancestor: str, descendant: str) -> bool:
    r = _git(project, "merge-base", "--is-ancestor", ancestor, descendant)
    return r.returncode == 0


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _resolve_invocation(project: Path) -> str:
    """Build the suite invocation from .ilk-launch.json ship.suite."""
    config_path = project / ".ilk-launch.json"
    if not config_path.exists():
        return ""
    try:
        config = json.loads(config_path.read_text())
        suite = config.get("ship", {}).get("suite", {})
        command = suite.get("command", "")
        if not command:
            return ""
        flags = suite.get("flags", [])
        return command if not flags else f"{command} {' '.join(flags)}"
    except (json.JSONDecodeError, KeyError):
        return ""


def _baseline_key(tag: str, invocation: str) -> str:
    """Key matching the test helper's convention."""
    return f"{tag}_{invocation.replace(' ', '_')}"


def _load_baseline_ids(data_dir: Path, tag: str, invocation: str) -> list[str] | None:
    """Load stored baseline node ids.  Returns None when absent."""
    baselines_dir = data_dir / "runtime" / "release" / "baselines"
    p = baselines_dir / f"{_baseline_key(tag, invocation)}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        if isinstance(data, list):
            return data
        return None
    except (json.JSONDecodeError, KeyError):
        return None


def _write_proof(
    data_dir: Path,
    head: str,
    payload: dict,
) -> Path:
    """Write proof-<head12>.json and return its path."""
    proof_dir = data_dir / "runtime" / "release"
    proof_dir.mkdir(parents=True, exist_ok=True)
    p = proof_dir / f"proof-{head[:12]}.json"
    p.write_text(json.dumps(payload, indent=2) + "\n")
    return p


# ── check ────────────────────────────────────────────────────────────────

def check(project: Path, data_dir: Path) -> dict:
    """Check whether a release is eligible.

    Returns ``{eligible, reason, last_tag, head}``.
    """
    head = _get_head_sha(project)
    last_tag = _get_latest_tag(project)

    # 1. ship.release_train flag
    config_path = project / ".ilk-launch.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            if not cfg.get("ship", {}).get("release_train", False):
                return {
                    "eligible": False,
                    "reason": "ship.release_train is not true",
                    "last_tag": last_tag,
                    "head": head,
                }
        except (json.JSONDecodeError, KeyError):
            pass

    # 2. kill switch
    data_root = data_dir.parent
    if (data_root / "release-train.disabled").exists():
        return {
            "eligible": False,
            "reason": "kill switch release-train.disabled exists",
            "last_tag": last_tag,
            "head": head,
        }

    # 3. HEAD == latest tag
    if last_tag:
        tag_sha = _tag_sha(project, last_tag)
        if tag_sha and tag_sha == head:
            return {
                "eligible": False,
                "reason": f"HEAD equals latest tag {last_tag}",
                "last_tag": last_tag,
                "head": head,
            }

    # 4. dirty tree
    if _is_tree_dirty(project):
        return {
            "eligible": False,
            "reason": "working tree is dirty",
            "last_tag": last_tag,
            "head": head,
        }

    # 5. origin/main ancestor check (skip if ref absent)
    if _ref_exists(project, "origin/main"):
        if not _is_ancestor(project, "origin/main", "HEAD"):
            return {
                "eligible": False,
                "reason": "origin/main is not an ancestor of HEAD",
                "last_tag": last_tag,
                "head": head,
            }

    # 6. live runner
    pid_file = data_dir / "runtime" / "launcher" / "running.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _is_pid_alive(pid):
                return {
                    "eligible": False,
                    "reason": f"runner is live (pid {pid})",
                    "last_tag": last_tag,
                    "head": head,
                }
        except (ValueError, OSError):
            pass

    return {"eligible": True, "reason": "", "last_tag": last_tag, "head": head}


# ── prove ────────────────────────────────────────────────────────────────

def prove(project: Path, data_dir: Path) -> dict:
    """Prove HEAD against the last-tag baseline in a fresh clone.

    Phase 0: every sub-plan in ``git log <last_tag>..HEAD`` whose
    ``[plan:<slug>#…]`` trailer appears must be proven by ship_audit.
    (Stubbed for now — full Phase 0 comes when the run verb wires it.)

    Phase 1: ``git clone`` → run the resolved invocation → parse output
    → diff node ids against the stored baseline → write proof file.

    Returns ``{proven, reason, new_failing_ids, proof_file}``.
    """
    head = _get_head_sha(project)
    last_tag = _get_latest_tag(project)

    # ── no tag ────────────────────────────────────────────────────────
    if not last_tag:
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": None,
            "verdict": "refused", "reason": "no tag found",
        })
        return {
            "proven": False, "reason": "no tag found",
            "new_failing_ids": [], "proof_file": proof_path,
        }

    # ── resolve invocation ────────────────────────────────────────────
    invocation = _resolve_invocation(project)
    if not invocation:
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "verdict": "refused",
            "reason": "could not resolve suite invocation",
        })
        return {
            "proven": False,
            "reason": "could not resolve suite invocation",
            "new_failing_ids": [], "proof_file": proof_path,
        }

    # ── load baseline ─────────────────────────────────────────────────
    baseline_ids = _load_baseline_ids(data_dir, last_tag, invocation)
    if baseline_ids is None:
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused", "reason": "could_not_compare",
        })
        return {
            "proven": False, "reason": "could_not_compare",
            "new_failing_ids": [], "proof_file": proof_path,
        }

    # ── Phase 1: clone + run suite ────────────────────────────────────
    tmp_dir = tempfile.mkdtemp(prefix="release_train_")
    clone_dir = Path(tmp_dir) / "clone"

    r = _git(project, "clone", "--quiet", str(project), str(clone_dir))
    if r.returncode != 0:
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused",
            "reason": f"clone failed: {r.stderr.strip()}",
        })
        return {
            "proven": False,
            "reason": f"clone failed: {r.stderr.strip()}",
            "new_failing_ids": [], "proof_file": proof_path,
        }

    _git(clone_dir, "checkout", head)

    env = os.environ.copy()
    env["ILK_ALLOW_FULL_SUITE"] = "1"

    suite_result = subprocess.run(
        invocation,
        shell=True,
        cwd=clone_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        env=env,
    )

    # parse output
    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    from verification_record import parse_pytest_output  # noqa: E402

    try:
        parsed = parse_pytest_output(suite_result.stdout + suite_result.stderr)
        failing_nodes: list[str] = parsed["failing_nodes"]
    except ValueError:
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "suite_cwd": str(clone_dir),
            "verdict": "refused",
            "reason": "could not parse suite output",
        })
        return {
            "proven": False, "reason": "could not parse suite output",
            "new_failing_ids": [], "proof_file": proof_path,
        }

    # ── diff against baseline ─────────────────────────────────────────
    baseline_set = set(baseline_ids)
    new_failing = sorted(set(failing_nodes) - baseline_set)

    if new_failing:
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "suite_cwd": str(clone_dir),
            "verdict": "refused",
            "reason": f"new failing tests: {', '.join(new_failing)}",
            "new_failing_ids": new_failing,
            "failing_nodes": failing_nodes,
            "baseline_ids": baseline_ids,
        })
        return {
            "proven": False,
            "reason": f"new failing tests: {', '.join(new_failing)}",
            "new_failing_ids": new_failing,
            "proof_file": proof_path,
        }

    # ── proven ────────────────────────────────────────────────────────
    proof_path = _write_proof(data_dir, head, {
        "head": head, "last_tag": last_tag,
        "invocation": invocation,
        "suite_cwd": str(clone_dir),
        "verdict": "proven",
        "new_failing_ids": [],
        "failing_nodes": failing_nodes,
        "baseline_ids": baseline_ids,
    })
    return {
        "proven": True, "reason": "",
        "new_failing_ids": [], "proof_file": proof_path,
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Release train: check eligibility and prove HEAD",
    )
    sub = parser.add_subparsers(dest="verb")

    p_check = sub.add_parser("check")
    p_check.add_argument("--project", type=Path, required=True)
    p_check.add_argument("--json", action="store_true")

    p_prove = sub.add_parser("prove")
    p_prove.add_argument("--project", type=Path, required=True)
    p_prove.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if not args.verb:
        parser.print_help()
        return 2

    # resolve data dir
    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    from ilk_paths import ilk_data_root  # noqa: E402

    project = args.project.resolve()
    data_root = ilk_data_root()
    data_dir = data_root / "projects" / project.name

    if args.verb == "check":
        result = check(project, data_dir)
        if args.json:
            print(json.dumps(result))
        else:
            if result["eligible"]:
                print("eligible")
            else:
                print(f"not eligible: {result['reason']}")
        return 0 if result["eligible"] else 3

    if args.verb == "prove":
        result = prove(project, data_dir)
        if args.json:
            print(json.dumps(result, default=str))
        else:
            if result["proven"]:
                print("proven")
            else:
                print(f"refused: {result['reason']}")
        return 0 if result["proven"] else 4

    return 2


if __name__ == "__main__":
    sys.exit(main())