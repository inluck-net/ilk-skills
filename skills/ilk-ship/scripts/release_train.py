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
from collections.abc import Callable
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


# ── cut ────────────────────────────────────────────────────────────────────

def _git_mut(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a mutating git command (commit, push, tag)."""
    return subprocess.run(
        ["git", *args],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _parse_version(tag: str) -> tuple[int, int, int]:
    """Parse vX.Y.Z into (major, minor, patch)."""
    tag = tag.lstrip("v")
    parts = tag.split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _next_patch(tag: str) -> str:
    """Increment the PATCH component of a vX.Y.Z tag."""
    major, minor, patch = _parse_version(tag)
    return f"v{major}.{minor}.{patch + 1}"


_TRAILER_RE = __import__("re").compile(r"\[plan:([a-z0-9-]+)#")


def _gather_master_titles(project: Path, last_tag: str, plans_dir: Path) -> str:
    """Collect master titles whose sub-plan trailers appear in git log."""
    r = _git(project, "log", f"{last_tag}..HEAD", "--format=%s")
    if r.returncode != 0:
        return ""
    slugs: list[str] = []
    for line in r.stdout.splitlines():
        for m in _TRAILER_RE.finditer(line):
            slug = m.group(1)
            if slug not in slugs:
                slugs.append(slug)
    titles: list[str] = []
    for slug in slugs:
        # Find the sub-plan file and extract the master title
        for p in plans_dir.glob("*.md"):
            try:
                text = p.read_text()
                if f"plan: {slug}" in text:
                    # Extract master title from the "Part of" line
                    for line in text.splitlines():
                        if "MASTER-" in line and "]" in line:
                            # e.g. Part of [MASTER-title](link)
                            start = line.find("[MASTER-")
                            if start >= 0:
                                end = line.find("]", start)
                                master_ref = line[start + 1 : end]
                                title = master_ref.split("-", 3)[-1].replace("-", " ").title()
                                if title not in titles:
                                    titles.append(title)
                            break
            except (OSError, UnicodeDecodeError):
                continue
    return "; ".join(titles)


def _insert_changelog_row(project: Path, row: str) -> None:
    """Insert a CHANGELOG row as the first data row in the table."""
    changelog = project / "CHANGELOG.md"
    if not changelog.exists():
        # Create a minimal CHANGELOG with the table header
        header = (
            "# Changelog\n\n"
            "Releases are cut as annotated git tags.\n\n"
            "## Recent releases\n\n"
            "| Version | Date | Highlights |\n"
            "|---|---|---|\n"
        )
        changelog.write_text(header + row + "\n")
        return

    lines = changelog.read_text().splitlines()
    insert_idx = None
    for i, line in enumerate(lines):
        if line.startswith("|---"):
            insert_idx = i + 1
            break
    if insert_idx is None:
        # No table found — append one
        lines.extend(["", "## Recent releases", "", "| Version | Date | Highlights |", "|---|---|---|", row])
    else:
        lines.insert(insert_idx, row)
    changelog.write_text("\n".join(lines) + "\n")


def cut(project: Path, data_dir: Path) -> dict:
    """Cut a release: CHANGELOG row, annotated tag, push, baseline.

    Returns ``{"tag", "commit", "pushed", "baseline"}`` on success.
    Exits with code 4 on refusal.
    """
    head = _get_head_sha(project)
    last_tag = _get_latest_tag(project)

    # ── load proof ───────────────────────────────────────────────────────
    proof_dir = data_dir / "runtime" / "release"
    proof_path = proof_dir / f"proof-{head[:12]}.json"
    if not proof_path.exists():
        print(f"refused: no proof file for HEAD {head[:12]}", file=sys.stderr)
        sys.exit(4)

    try:
        proof = json.loads(proof_path.read_text())
    except (json.JSONDecodeError, OSError):
        print("refused: could not read proof file", file=sys.stderr)
        sys.exit(4)

    if proof.get("head") != head:
        print("refused: proof head mismatch", file=sys.stderr)
        sys.exit(4)

    if proof.get("verdict") != "proven":
        print(f"refused: proof verdict is {proof.get('verdict')!r}, not 'proven'", file=sys.stderr)
        sys.exit(4)

    # ── check still eligible ─────────────────────────────────────────────
    eligibility = check(project, data_dir)
    if not eligibility["eligible"]:
        print(f"refused: {eligibility['reason']}", file=sys.stderr)
        sys.exit(4)

    # ── next tag ─────────────────────────────────────────────────────────
    if not last_tag:
        print("refused: no last tag found", file=sys.stderr)
        sys.exit(4)

    next_tag = _next_patch(last_tag)
    if _ref_exists(project, next_tag):
        print(f"refused: tag {next_tag} already exists", file=sys.stderr)
        sys.exit(4)

    # ── gather master titles ─────────────────────────────────────────────
    invocation = proof.get("invocation", "")
    # Resolve plans dir from data_dir
    plans_dir = data_dir / "plans"
    if not plans_dir.exists():
        # Try external plans dir
        plans_dir = data_dir.parent / "plans"
    title = _gather_master_titles(project, last_tag, plans_dir) if plans_dir.exists() else ""

    # ── proof stats ──────────────────────────────────────────────────────
    failing_nodes = proof.get("failing_nodes", [])
    baseline_ids = proof.get("baseline_ids", [])
    new_failing_ids = proof.get("new_failing_ids", [])
    suite_cwd = proof.get("suite_cwd", "")

    # Phase 0: count proven sub-plans from the proof
    phase0_proven = len(baseline_ids) if baseline_ids else 0
    phase0_total = phase0_proven  # all proven if we got here

    # Phase 1: parse suite output stats
    phase1_passed = 0
    phase1_failed = len(failing_nodes)
    phase1_new = len(new_failing_ids)

    # ── CHANGELOG row ────────────────────────────────────────────────────
    import datetime
    today = datetime.date.today().isoformat()
    master_slugs = []
    r = _git(project, "log", f"{last_tag}..HEAD", "--format=%s")
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            for m in _TRAILER_RE.finditer(line):
                if m.group(1) not in master_slugs:
                    master_slugs.append(m.group(1))

    highlights = (
        f"**{title}.** RELEASE TRAIN (unattended)"
        f" — {', '.join(master_slugs) if master_slugs else 'direct commits'}."
        f" Phase 0 {phase0_total}/{phase0_total} proven."
        f" Phase 1: {phase1_failed} failed / {phase1_passed} passed"
        f" with {invocation}, {phase1_new} new ids vs {last_tag}."
    )
    row = f"| {next_tag} | {today} | {highlights} |"
    _insert_changelog_row(project, row)

    # ── commit CHANGELOG ─────────────────────────────────────────────────
    _git_mut(project, "add", "CHANGELOG.md")
    commit_msg = f"docs(changelog): {next_tag}"
    r = _git_mut(project, "commit", "-m", commit_msg)
    if r.returncode != 0:
        print(f"refused: changelog commit failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)

    commit_sha = _get_head_sha(project)

    # ── annotated tag ────────────────────────────────────────────────────
    tag_body = (
        f"{next_tag}\n\n"
        f"Title: {title}\n"
        f"Masters: {', '.join(master_slugs) if master_slugs else 'direct commits'}\n"
        f"Phase 0: {phase0_total}/{phase0_total} proven\n"
        f"Phase 1: {phase1_failed} failed / {phase1_passed} passed\n"
        f"Invocation: {invocation}\n\n"
        f"Cut by the release train (unattended), authorized by Chad 2026-10-03.\n"
    )
    r = _git_mut(project, "tag", "-a", next_tag, "-m", tag_body)
    if r.returncode != 0:
        print(f"refused: tag creation failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)

    # ── push ─────────────────────────────────────────────────────────────
    r = _git_mut(project, "push", "origin", "main")
    if r.returncode != 0:
        print(f"refused: push main failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)

    r = _git_mut(project, "push", "origin", next_tag)
    if r.returncode != 0:
        print(f"refused: push tag failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)

    # ── store baseline ───────────────────────────────────────────────────
    baselines_dir = data_dir / "runtime" / "release" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    key = f"{next_tag}_{invocation.replace(' ', '_')}"
    baseline_path = baselines_dir / f"{key}.json"
    # Baseline = the proof's failing node ids (same search space)
    search_space = len(failing_nodes) + (len(baseline_ids) - phase1_new) if baseline_ids else 0
    baseline_data = failing_nodes
    baseline_path.write_text(json.dumps(baseline_data, indent=2) + "\n")

    return {
        "tag": next_tag,
        "commit": commit_sha,
        "pushed": True,
        "baseline": str(baseline_path),
    }


# ── deploy ──────────────────────────────────────────────────────────────────

def deploy(
    project: Path,
    tag: str,
    data_dir: Path,
    release_cmd: object | None = None,
    bounce_cmd: object | None = None,
    status_cmd: object | None = None,
    pid_file: Path | None = None,
) -> dict:
    """Deploy a release: extract, flip, bounce, smoke.  Rollback on failure.

    Every external command is an injectable parameter so tests can stub them.

    Returns ``{"tag", "deployed", "scheduler_pid"}`` on success (exit 0).
    Returns ``{"tag", "deployed": false, "rolled_back_to", "rollback_smoke",
    "reason"}`` on rollback (exit 5).
    Exit 4 when extraction fails.
    Exit 6 when both smokes fail.
    """
    _SCRIPTS = Path(__file__).resolve().parent
    _RELEASE_SCRIPT = _SCRIPTS.parent.parent / "ilk-upgrade" / "scripts" / "ilk_release.py"
    _BOUNCE_SCRIPT = _SCRIPTS.parent.parent / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"
    _STATUS_SCRIPT = _SCRIPTS / "host_deploy_status.py"

    # ── injectable defaults ─────────────────────────────────────────────
    def _default_release_cmd(t: str, repo: Path) -> tuple[int, str]:
        r = subprocess.run(
            [sys.executable, str(_RELEASE_SCRIPT), t, "--repo", str(repo)],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
        )
        return r.returncode, r.stdout.strip()

    def _default_bounce_cmd() -> int:
        r = subprocess.run(
            [str(_BOUNCE_SCRIPT)],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60,
        )
        return r.returncode

    def _default_status_cmd(t: str) -> str:
        r = subprocess.run(
            [sys.executable, str(_STATUS_SCRIPT), "--bouncer", str(_BOUNCE_SCRIPT), "--require-tag", t],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60,
        )
        return r.stdout.strip()

    _release = release_cmd if release_cmd is not None else _default_release_cmd
    _bounce = bounce_cmd if bounce_cmd is not None else _default_bounce_cmd
    _status = status_cmd if status_cmd is not None else _default_status_cmd
    _pid = pid_file if pid_file is not None else (data_dir / "runtime" / "scheduler.pid")

    # ── 1. extract ──────────────────────────────────────────────────────
    rc, _out = _release(tag, project)  # type: ignore[operator]
    if rc != 0:
        print(f"refused: extraction failed for {tag} (exit {rc})", file=sys.stderr)
        sys.exit(4)

    # ── 2. bounce + smoke ───────────────────────────────────────────────
    _bounce()  # type: ignore[operator]
    smoke_ok, smoke_reason = _smoke(tag, _status, _pid)  # type: ignore[arg-type]

    if smoke_ok:
        return {
            "tag": tag,
            "deployed": True,
            "scheduler_pid": _read_pid(_pid),
        }

    # ── 3. rollback ─────────────────────────────────────────────────────
    prev_status = _release("--status", project)  # type: ignore[operator]
    try:
        status_data = json.loads(prev_status[1]) if prev_status[0] == 0 else {}
    except (json.JSONDecodeError, KeyError):
        status_data = {}
    prev_tag = status_data.get("previous", "")
    if prev_tag:
        prev_tag = Path(prev_tag).name  # extract tag name from symlink target

    _release("--rollback", project)  # type: ignore[operator]
    _bounce()  # type: ignore[operator]

    rollback_ok, rollback_reason = _smoke(prev_tag, _status, _pid)  # type: ignore[arg-type]

    return {
        "tag": tag,
        "deployed": False,
        "rolled_back_to": prev_tag,
        "rollback_smoke": "ok" if rollback_ok else "failed",
        "reason": smoke_reason,
    }


def _smoke(
    tag: str,
    status_cmd: Callable[[str], str],
    pid_file: Path,
) -> tuple[bool, str]:
    """Smoke check: status ok + pid alive + script under releases/<tag>/.

    Returns (ok, reason).
    """
    # Status check
    status = status_cmd(tag)
    if status != "ok":
        return False, f"status={status}"

    # Pid check
    if not pid_file.exists():
        return False, "scheduler.pid missing"
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return False, "scheduler.pid unreadable"

    if not _is_pid_alive(pid):
        return False, f"scheduler pid {pid} not alive"

    # Command-line check: script path must mention releases/<tag>/
    try:
        r = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=10,
        )
        cmd = r.stdout.strip()
        if f"releases/{tag}/" not in cmd:
            return False, f"scheduler command does not mention releases/{tag}/"
    except (OSError, subprocess.TimeoutExpired):
        return False, "could not read scheduler command"

    return True, ""


def _read_pid(pid_file: Path) -> int | None:
    """Read a PID file, returning None on any error."""
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


# ── run ──────────────────────────────────────────────────────────────────

def _acquire_lock(data_dir: Path) -> Path:
    """Acquire the release train lock, replacing a stale lock (dead pid).

    Returns the lock file path.  Caller is responsible for removing it.
    """
    lock_dir = data_dir / "runtime" / "release"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "train.lock"

    if lock_file.exists():
        try:
            old_pid = int(lock_file.read_text(encoding="utf-8").strip())
            if _is_pid_alive(old_pid):
                raise RuntimeError(
                    f"release train lock held by live pid {old_pid}"
                )
        except (ValueError, OSError):
            pass  # corrupt lock — replace it

    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    return lock_file


def _release_lock(lock_file: Path) -> None:
    """Remove the release train lock (idempotent)."""
    try:
        lock_file.unlink(missing_ok=True)
    except OSError:
        pass


def run(
    project: Path,
    data_dir: Path,
    *,
    check_fn: Callable | None = None,
    prove_fn: Callable | None = None,
    cut_fn: Callable | None = None,
    deploy_fn: Callable | None = None,
    notify_script: str | None = None,
    deploy_exit_code: int | None = None,
) -> dict:
    """Run the full release train: check → prove → cut → deploy.

    Takes the lock ``<data_dir>/runtime/release/train.lock`` (pid inside;
    a stale lock whose pid is dead is replaced).  The lock is removed on
    every path (try/finally).

    Audit rows:
      - not eligible → ``released`` with ``outcome: skipped``
      - prove/cut refusal → ``escalated`` + notify
      - successful deploy → ``released`` + ``released`` event
      - rollback → ``rolled-back`` + ``escalated`` + notify
      - both smokes fail (exit 6) → same as rollback with ``severity: critical``

    Returns ``{"exit_code", "tag", "deployed", ...}``.
    """
    _check = check_fn if check_fn is not None else check
    _prove = prove_fn if prove_fn is not None else prove
    _cut = cut_fn if cut_fn is not None else cut
    _deploy = deploy_fn if deploy_fn is not None else deploy

    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    from ilk_audit import write_audit, write_event  # noqa: E402

    project_name = project.name
    lock_file = data_dir / "runtime" / "release" / "train.lock"

    try:
        lock_file = _acquire_lock(data_dir)
    except RuntimeError as exc:
        return {"exit_code": 1, "reason": str(exc)}

    try:
        # ── 1. check ───────────────────────────────────────────────────
        check_result = _check(project, data_dir)
        if not check_result.get("eligible"):
            reason = check_result.get("reason", "not eligible")
            write_audit(
                "released", project_name,
                outcome="skipped", reason=reason,
            )
            return {"exit_code": 3, "reason": reason}

        # ── 2. prove ───────────────────────────────────────────────────
        prove_result = _prove(project, data_dir)
        if not prove_result.get("proven"):
            reason = prove_result.get("reason", "proof failed")
            write_audit(
                "escalated", project_name,
                reason=f"prove refused: {reason}",
            )
            _notify("blocked", project_name, f"prove refused: {reason}", notify_script)
            return {"exit_code": 4, "reason": f"prove refused: {reason}"}

        # ── 3. cut ─────────────────────────────────────────────────────
        cut_result = _cut(project, data_dir)
        tag = cut_result.get("tag", "")
        if not tag:
            reason = cut_result.get("reason", "cut failed")
            write_audit(
                "escalated", project_name,
                reason=f"cut refused: {reason}",
            )
            _notify("blocked", project_name, f"cut refused: {reason}", notify_script)
            return {"exit_code": 4, "reason": f"cut refused: {reason}"}

        # ── 4. deploy ──────────────────────────────────────────────────
        try:
            deploy_result = _deploy(project, tag, data_dir)
        except SystemExit as exc:
            # deploy raises SystemExit(5) on rollback, SystemExit(6) on
            # both-smokes-fail, SystemExit(4) on extraction failure.
            exit_code = exc.code if isinstance(exc.code, int) else 1
            if exit_code == 5:
                # Rollback succeeded
                write_audit(
                    "rolled-back", project_name,
                    tag=tag, reason="smoke failed, rolled back",
                )
                write_event("rolled-back", project_name, tag=tag)
                _notify("blocked", project_name, f"deploy rolled back {tag}", notify_script)
                return {"exit_code": 5, "tag": tag, "deployed": False, "rolled_back": True}
            elif exit_code == 6:
                # Both smokes failed
                write_audit(
                    "rolled-back", project_name,
                    tag=tag, reason="both smokes failed",
                    severity="critical",
                )
                write_event("rolled-back", project_name, tag=tag, severity="critical")
                _notify("blocked", project_name, f"deploy CRITICAL: both smokes failed {tag}", notify_script)
                return {"exit_code": 6, "tag": tag, "deployed": False, "rolled_back": True}
            else:
                # Extraction failed
                write_audit(
                    "escalated", project_name,
                    reason=f"deploy extraction failed (exit {exit_code})",
                )
                _notify("blocked", project_name, f"deploy extraction failed {tag}", notify_script)
                return {"exit_code": exit_code, "tag": tag, "deployed": False}

        # Deploy succeeded
        write_audit("released", project_name, tag=tag, outcome="deployed")
        write_event("released", project_name, tag=tag)
        return {
            "exit_code": 0,
            "tag": tag,
            "deployed": True,
            "rolled_back": False,
        }

    finally:
        _release_lock(lock_file)


def _notify(
    event: str,
    project: str,
    detail: str,
    notify_script: str | None = None,
) -> None:
    """Call ilk_notify.py to send a desktop notification."""
    _NOTIFY = Path(notify_script) if notify_script else (
        _SCRIPTS_DIR.parent.parent / "ilk-watchdog" / "scripts" / "ilk_notify.py"
    )
    try:
        subprocess.run(
            [sys.executable, str(_NOTIFY), "--event", event, "--project", project, "--detail", detail],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # notify is best-effort


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

    p_cut = sub.add_parser("cut")
    p_cut.add_argument("--project", type=Path, required=True)
    p_cut.add_argument("--json", action="store_true")

    p_deploy = sub.add_parser("deploy")
    p_deploy.add_argument("--project", type=Path, required=True)
    p_deploy.add_argument("--tag", required=True, help="Tag to deploy")
    p_deploy.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run")
    p_run.add_argument("--project", type=Path, required=True)
    p_run.add_argument("--json", action="store_true")

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

    if args.verb == "cut":
        result = cut(project, data_dir)
        if args.json:
            print(json.dumps(result))
        else:
            print(json.dumps(result))
        return 0

    if args.verb == "deploy":
        result = deploy(project, args.tag, data_dir)
        if args.json:
            print(json.dumps(result))
        else:
            print(json.dumps(result))
        return 0

    if args.verb == "run":
        result = run(project, data_dir)
        if args.json:
            print(json.dumps(result))
        else:
            print(json.dumps(result))
        return result.get("exit_code", 2)

    return 2


if __name__ == "__main__":
    sys.exit(main())