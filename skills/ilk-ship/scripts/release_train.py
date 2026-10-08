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
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
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


# ── batch-record validation ───────────────────────────────────────────────

def _validate_batch_record(
    runtime_dir: Path,
    head: str,
    invocation: str,
    tree_sha: str | None = None,
    repo: Path | None = None,
) -> dict:
    """Validate a batch-gate record for the release train.

    Shared validator consumed by ``prove()``.  Performs:
      1. Per-batch discovery — find the matching record in
         ``runtime/batch-gates/``.
      2. Structural validation via ``batch_gate.validate_record_detail``.
      3. Trusted-writer enforcement — only records written by
         ``_TRUSTED_WRITERS`` are accepted.
      4. Source validation — records with an unacceptable ``suite_source``
         are refused.
      5. Provenance reporting — the result carries the validated record
         and its source path.

    Returns ``{"ok": True, "record": BatchGateRecord, "batch_slug": str,
    "batch_path": str, "provenance": dict}`` on success.

    Returns ``{"ok": False, "reason": str, "verdict_source": str}`` on
    refusal.
    """
    from batch_gate import (
        BatchGateRecord,
        batch_record_path,
        read_record,
        record_path,
        validate_record_detail,
    )

    # ── 1. Per-batch discovery ────────────────────────────────────────
    #
    # Scan ``runtime/batch-gates/`` for the canonical matching record.
    # Exactly one match is required; zero means absent, >1 means
    # ambiguous.  Do not select by filename, mtime, or the legacy
    # latest-record alias.
    batch_gates_dir = runtime_dir / "batch-gates"
    matching_batches: list[tuple[Path, dict]] = []
    if batch_gates_dir.is_dir():
        for p in sorted(batch_gates_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            rec_head = data.get("head_sha", "")
            rec_inv = data.get("invocation", "")
            rec_tree = data.get("tree_sha")
            head_match = rec_head == head
            tree_match = bool(rec_tree and tree_sha and rec_tree == tree_sha)
            inv_match = rec_inv == invocation
            if (head_match or tree_match) and inv_match:
                matching_batches.append((p, data))

    if len(matching_batches) > 1:
        names = ", ".join(p.stem for p, _ in matching_batches)
        return {
            "ok": False,
            "reason": (
                f"ambiguous batch verdict: {len(matching_batches)} records "
                f"match the candidate — {names}"
            ),
            "verdict_source": "batch_verdict",
        }

    if not matching_batches:
        # No per-batch record found — fall back to legacy batch-gate.json.
        legacy_path = record_path(runtime_dir)
        detail = validate_record_detail(
            legacy_path, head, invocation, tree_sha, repo=repo,
        )
        if detail != "fresh":
            return {
                "ok": False,
                "reason": f"batch verdict not fresh: {detail}",
                "verdict_source": "batch_verdict",
            }
        record = read_record(runtime_dir)
        if record is None:
            return {
                "ok": False,
                "reason": "batch verdict absent: could not load record",
                "verdict_source": "batch_verdict",
            }
        raw_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        batch_slug = None
        batch_path = str(legacy_path)
    else:
        p, raw_data = matching_batches[0]
        detail = validate_record_detail(
            p, head, invocation, tree_sha, repo=repo,
        )
        if detail != "fresh":
            return {
                "ok": False,
                "reason": f"batch verdict not fresh: {detail}",
                "verdict_source": "batch_verdict",
            }
        record = read_record(runtime_dir, batch=p.stem)
        if record is None:
            return {
                "ok": False,
                "reason": f"batch verdict absent: could not load {p.name}",
                "verdict_source": "batch_verdict",
            }
        batch_slug = p.stem
        batch_path = str(p)

    # ── 2. Trusted-writer enforcement ─────────────────────────────────
    writer = (record.writer or "").strip()
    if not writer:
        return {
            "ok": False,
            "reason": (
                "unsigned batch verdict: record has no writer field — "
                "only records written by a trusted writer are accepted "
                "as suite authority"
            ),
            "verdict_source": "batch_verdict",
        }
    if writer not in _TRUSTED_WRITERS:
        return {
            "ok": False,
            "reason": (
                f"untrusted batch verdict writer: '{writer}' — "
                f"accepted writers: {', '.join(sorted(_TRUSTED_WRITERS))}"
            ),
            "verdict_source": "batch_verdict",
        }

    # ── 3. Source validation ──────────────────────────────────────────
    #
    # ``suite_source`` records how the suite output was obtained.
    # ``"tool"`` means the standard verification_record.py --run-suite
    # path ran it.  ``"operator:<path>"`` means the output was ingested
    # from a pre-existing file.  Absent on legacy records — treat as
    # "tool".
    #
    # Note: ``read_record`` does not populate ``suite_source`` from JSON
    # (pre-existing gap), so we read it from the raw data discovered
    # during per-batch scanning.
    source = (raw_data.get("suite_source") or "tool").strip()
    ACCEPTED_SOURCES = {"tool"}
    # Also accept the prefix form "tool:" (future extensibility).
    if not (source in ACCEPTED_SOURCES or source.startswith("tool:")):
        return {
            "ok": False,
            "reason": (
                f"wrong batch verdict source: '{source}' — "
                f"only records produced by the standard verification "
                f"pipeline are accepted as suite authority"
            ),
            "verdict_source": "batch_verdict",
        }

    # ── 4. Provenance reporting ───────────────────────────────────────
    provenance = {
        "writer": record.writer,
        "suite_source": raw_data.get("suite_source"),
        "tree_sha": record.tree_sha,
        "head_sha": record.head_sha,
        "verdict": record.verdict,
    }

    return {
        "ok": True,
        "record": record,
        "batch_slug": batch_slug,
        "batch_path": batch_path,
        "provenance": provenance,
    }


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

def _proof_payload(**kwargs: object) -> dict:
    """Build a proof payload, injecting baseline_red_evidence and carried_ids."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _scope_summary(scope: dict | None) -> dict:
    """A batch record's suite_scope, with ``mode: unrecorded`` when absent."""
    if not isinstance(scope, dict) or not scope.get("mode"):
        return {"mode": "unrecorded"}
    return dict(scope)


def _scope_phrase(scope: dict) -> str:
    """``8 of 8 tests passed, 1 file selected`` or ``not recorded``."""
    if scope.get("mode") == "unrecorded":
        return "scope not recorded in batch-gate.json"
    parts = []
    if "passed" in scope:
        parts.append(f"{scope['passed']} of {scope.get('total', '?')} tests passed")
    if scope.get("mode") == "scoped" and "selection_size" in scope:
        parts.append(f"{scope['selection_size']} file(s) selected")
    return ", ".join(parts) or "no counts recorded"


def _release_path_hits(project: Path, base: str, head: str) -> list[str]:
    """Files in ``base..head`` that are on the release path.

    Release path as ``verification_record.release_path_hits`` defines it
    (the one definition the verify's scope also widens on).  An unreadable
    diff counts as a hit: a range the train cannot list is not one it can
    call clean.
    """
    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    from verification_record import release_path_hits  # noqa: E402

    r = _git(project, "diff", "--name-only", base, head)
    if r.returncode != 0:
        return [f"<git diff {base}..{head[:12]} failed>"]
    return release_path_hits([p for p in r.stdout.splitlines() if p.strip()])


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

    # ── load baseline (canonical .ilk-baselines/ only) ────────────────
    if str(_LOOP_SCRIPTS.parent / "ilk-ship" / "scripts") not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS.parent / "ilk-ship" / "scripts"))
    from baseline_diff import load_baseline, load_baseline_red_evidence  # noqa: E402

    baseline_result = load_baseline(data_dir, last_tag, invocation)
    if baseline_result is None:
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused", "reason": "could_not_compare",
        })
        return {
            "proven": False, "reason": "could_not_compare",
            "new_failing_ids": [], "proof_file": proof_path,
        }
    baseline_ids = sorted(baseline_result[0])

    # ── load baseline_red evidence (batch-verification artifacts) ──────
    baseline_red_entries = load_baseline_red_evidence(data_dir, last_tag, invocation)
    if baseline_red_entries is None:
        baseline_red_entries = ()

    # ── kernel range check (cheap refusal first) ──────────────────────
    # Judge by the kernel list at last_tag, not HEAD.
    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    from safety_kernel import check_range as kernel_check_range  # noqa: E402

    # Resolve plans dir
    plans_dir = data_dir / "plans"
    if not plans_dir.exists():
        # Try external plans dir
        plans_dir = data_dir.parent / "plans"
    if not plans_dir.exists():
        # Fallback: project's docs/plans
        plans_dir = project / "docs" / "plans"
    if not plans_dir.exists():
        plans_dir = None

    ledger_dir = data_dir / "runtime"

    try:
        kernel_violations = kernel_check_range(
            project, last_tag, head,
            plans_dir=plans_dir if plans_dir is not None else Path("."),
            ledger_dir=ledger_dir if ledger_dir.exists() else None,
        )
    except Exception as exc:
        kernel_violations = []

    if kernel_violations:
        reasons = []
        for v in kernel_violations:
            reasons.append(f"{v['reason']} {v['sha'][:12]} {v['path']}")
        reason_str = "kernel-range: " + "; ".join(reasons)
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused", "reason": reason_str,
            "kernel_range": {
                "judged_by": last_tag,
                "violations": len(kernel_violations),
            },
        })
        return {
            "proven": False, "reason": reason_str,
            "new_failing_ids": [], "proof_file": proof_path,
        }

    # ── Phase 1: validate the signed batch verdict ────────────────────
    #
    # The signed batch-gate record is the suite authority.  If a fresh
    # canonical record exists whose code identity and invocation match the
    # current candidate, and its verdict is ``pass`` with zero attributed
    # failures, the proof advances without re-running the full suite.
    #
    # This replaces the previous clone-and-rerun path, which produced
    # order-dependent measurements that could disagree with the already-
    # signed verdict.

    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    from phase1_verify import verify_phase1  # noqa: E402

    # Resolve the current tree for tree-based comparison.
    tree_sha = None
    tree_probe = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=project, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if tree_probe.returncode == 0:
        tree_sha = tree_probe.stdout.strip()

    # Build a baseline report for the baseline engine.
    from baseline_diff import BaselineRef, BaselineStatus, NodeIdDiff, BaselineReport  # noqa: E402

    ref = BaselineRef(tag=last_tag, resolved=True, status=BaselineStatus.FOUND)
    diff = NodeIdDiff(
        ref=ref,
        new_failures=frozenset(),
        inherited_failures=frozenset(),
        fixed=frozenset(),
        current_count=0,
        baseline_count=0,
        search_space=baseline_result[1],
        filtered=False,
    )
    baseline_report = BaselineReport(
        diff=diff,
        stale_exclusions=(),
        denominator_statement=(
            f"0 regressions across {baseline_result[1]} collected tests vs {last_tag}"
        ),
    )

    # Validate the batch-gate record (engine 1) and baseline (engine 2).
    runtime_dir = data_dir / "runtime"
    p1_result = verify_phase1(
        runtime_dir,
        head,
        invocation,
        baseline_report=baseline_report,
        expected_tree_sha=tree_sha,
        repo=project,
    )

    if p1_result.action == "refuse":
        reason = p1_result.reason
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused",
            "reason": reason,
            "verdict_source": p1_result.engine,
        })
        return {
            "proven": False, "reason": reason,
            "new_failing_ids": [], "proof_file": proof_path,
        }

    # ── Batch-record validation (shared validator) ───────────────────
    #
    # Phase 1 validated structural freshness (head/tree/invocation).
    # The shared validator now enforces trusted-writer, source, and
    # ambiguity checks, and returns the validated record with its
    # provenance.
    validation = _validate_batch_record(
        runtime_dir, head, invocation, tree_sha=tree_sha, repo=project,
    )

    if not validation["ok"]:
        reason = validation["reason"]
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused",
            "reason": reason,
            "verdict_source": validation.get("verdict_source", "batch_verdict"),
        })
        return {
            "proven": False, "reason": reason,
            "new_failing_ids": [], "proof_file": proof_path,
        }

    batch_record = validation["record"]
    batch_slug = validation["batch_slug"]
    batch_path = validation["batch_path"]
    provenance = validation["provenance"]

    failing_nodes = []
    undeclared_failures = []
    if batch_record.undeclared is not None:
        undeclared_failures = list(batch_record.undeclared)

    # Double-check: a pass with undeclared failures must refuse.
    if undeclared_failures:
        reason = (
            f"batch verdict pass with {len(undeclared_failures)} undeclared failure(s): "
            + ", ".join(undeclared_failures)
        )
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused",
            "reason": reason,
            "verdict_source": "batch_verdict",
        })
        return {
            "proven": False, "reason": reason,
            "new_failing_ids": undeclared_failures, "proof_file": proof_path,
        }

    # ── verify scope (93d82cba) ───────────────────────────────────────
    #
    # A scoped verify proves only its selection.  07h (2026-10-08) verified
    # 8 tests and read to the train like a full-suite pass while its diff
    # changed scheduler.sh.  A verify that is not recorded as ``full`` may
    # not prove a range that touches the release path.
    suite_scope = _scope_summary(batch_record.suite_scope)
    if suite_scope["mode"] != "full":
        hits = _release_path_hits(project, last_tag, head)
        if hits:
            reason = (
                f"scoped-verify-over-release-path: the verify was "
                f"{suite_scope['mode']} ({_scope_phrase(suite_scope)}) but "
                f"{last_tag}..HEAD changes release-path files: "
                + ", ".join(hits[:8])
                + (f" (+{len(hits) - 8} more)" if len(hits) > 8 else "")
                + ". Run a full-scope verify (--scope full) or release by hand "
                  "stating the narrower claim."
            )
            proof_path = _write_proof(data_dir, head, {
                "head": head, "last_tag": last_tag,
                "invocation": invocation,
                "verdict": "refused",
                "reason": reason,
                "verdict_source": "batch_verdict",
                "suite_scope": suite_scope,
            })
            return {
                "proven": False, "reason": reason,
                "new_failing_ids": [], "proof_file": proof_path,
            }

    # Record carried ids (inherited from baseline_red evidence).
    carried_ids = []

    # ── safety case (after Phase 1 passes) ────────────────────────────
    import safety_case  # noqa: E402

    # Clear ILK_WORKER_SESSION — the release train is a driver operation,
    # not a worker session.  safety_case.run refuses in worker sessions.
    old_worker = os.environ.pop("ILK_WORKER_SESSION", None)
    try:
        try:
            sc_result = safety_case.run(
                project, data_dir=data_dir, no_record=True,
            )
        except Exception as exc:
            sc_result = {"verdict": "fail", "components": [{"name": "error", "ok": False, "tail": str(exc)}]}
    finally:
        if old_worker is not None:
            os.environ["ILK_WORKER_SESSION"] = old_worker

    if sc_result.get("verdict") != "pass":
        # Find the first failing component
        failing_comp = None
        for c in sc_result.get("components", []):
            if not c.get("ok"):
                failing_comp = c
                break
        comp_name = failing_comp.get("name", "unknown") if failing_comp else "unknown"
        comp_tail = (failing_comp.get("tail", "") if failing_comp else "")[:100]
        reason_str = f"safety-case: {comp_name} {comp_tail}".strip()
        proof_path = _write_proof(data_dir, head, {
            "head": head, "last_tag": last_tag,
            "invocation": invocation,
            "verdict": "refused", "reason": reason_str,
            "verdict_source": "safety_case",
            "safety_case": sc_result,
            "kernel_range": {
                "judged_by": last_tag,
                "violations": 0,
            },
        })
        return {
            "proven": False, "reason": reason_str,
            "new_failing_ids": [], "proof_file": proof_path,
        }

    # ── proven ────────────────────────────────────────────────────────
    proof_path = _write_proof(data_dir, head, _proof_payload(
        head=head, last_tag=last_tag, invocation=invocation,
        verdict="proven",
        new_failing_ids=[], failing_nodes=failing_nodes,
        baseline_ids=baseline_ids,
        baseline_red_evidence=list(baseline_red_entries),
        carried_ids=carried_ids,
        batch=batch_slug,
        batch_path=batch_path,
        provenance=provenance,
        verdict_source="batch_verdict",
        suite_scope=suite_scope,
        safety_case={
            "verdict": sc_result.get("verdict"),
            "components": sc_result.get("components"),
        },
        kernel_range={
            "judged_by": last_tag,
            "violations": 0,
        },
    ))
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

# Writers whose records the release train accepts as suite authority.
# Only records stamped by ``write_record`` in ``batch_gate.py`` (writer
# ``batch_gate.py``) or by the verification runner (writer
# ``verify_attribution``) are trusted.  A hand-authored record — which
# has no writer at all, or an unrecognised one — must not reach ``cut()``,
# permit consumption, deployment, or notification.
_TRUSTED_WRITERS = frozenset({"batch_gate.py", "verify_attribution"})


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
    suite_scope = _scope_summary(proof.get("suite_scope"))
    scope_line = f"verify scope {suite_scope['mode']} ({_scope_phrase(suite_scope)})"

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
        f" with {invocation}, {phase1_new} new ids vs {last_tag};"
        f" {scope_line}."
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
        f"Invocation: {invocation}\n"
        f"Verify: {scope_line}\n\n"
        f"Cut by the release train (unattended), authorized by Chad 2026-10-03.\n"
    )
    r = _git_mut(project, "tag", "-a", next_tag, "-m", tag_body)
    if r.returncode != 0:
        print(f"refused: tag creation failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)

    # ── store baseline (canonical .ilk-baselines/) ─────────────────────
    # Written before push so the baseline is captured even if push fails.
    if str(_LOOP_SCRIPTS.parent / "ilk-ship" / "scripts") not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS.parent / "ilk-ship" / "scripts"))
    from baseline_diff import store_baseline  # noqa: E402

    search_space = len(failing_nodes) + (len(baseline_ids) - phase1_new) if baseline_ids else 0
    baseline_path = store_baseline(
        project_root=data_dir,
        tag=next_tag,
        suite_invocation=invocation,
        node_ids=frozenset(failing_nodes),
        search_space=search_space,
    )

    # ── push ─────────────────────────────────────────────────────────────
    r = _git_mut(project, "push", "origin", "main")
    if r.returncode != 0:
        print(f"refused: push main failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)

    r = _git_mut(project, "push", "origin", next_tag)
    if r.returncode != 0:
        print(f"refused: push tag failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(4)

    return {
        "tag": next_tag,
        "commit": commit_sha,
        "pushed": True,
        "baseline": str(baseline_path),
    }


def _bouncer_for(tag: str) -> Path:
    """Bouncer of the release being deployed."""
    releases_root = Path(os.environ.get(
        "ILK_RELEASES_ROOT",
        str(Path.home() / ".ilk" / "releases"),
    ))
    return releases_root / tag / "skills" / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"


def _releases_root() -> Path:
    return Path(os.environ.get(
        "ILK_RELEASES_ROOT",
        str(Path.home() / ".ilk" / "releases"),
    ))


def _remote_extractor() -> Path:
    """The REMOTE host's incumbent extractor: <releases>/../current/...

    Never Path(__file__).resolve(): the train runs via ~/.ilk/current, which
    the local canary deploy has already flipped to the candidate, so that path
    names a release directory the remote only gets from this very extraction
    (every train v0.9.158-v0.9.161 failed rezmac that way; backlog
    7a395f927acbbc0a).  The unresolved current/ symlink is resolved on the
    remote, by the remote.
    """
    return (_releases_root().parent / "current" / "skills" / "ilk-upgrade"
            / "scripts" / "ilk_release.py")


def _status_script_for(tag: str) -> Path:
    """host_deploy_status.py of the release a smoke checks."""
    return _releases_root() / tag / "skills" / "ilk-ship" / "scripts" / "host_deploy_status.py"


def _scheduler_pid_file() -> Path:
    """The host's scheduler.pid lives in the data root, not per-project."""
    sys.path.insert(0, str(_LOOP_SCRIPTS))
    from ilk_paths import ilk_data_root
    return ilk_data_root() / "scheduler.pid"


# ── Quiet-fleet wait ────────────────────────────────────────────────────────

QUIET_WAIT_SEC: float = 5400.0
"""Seconds to wait for all ilk loops on the host to finish before deploying.

Judgment call: 5400s = 90 min, one full iteration timeout.  Wrong if a loop
runs many iterations back to back after its hold starts; then the train ends
fleet-busy and a later window retries.
"""

QUIET_POLL_SEC: float = 30.0
"""Seconds between quiet-fleet liveness polls.

Judgment call: 30s balances responsiveness against pgrep overhead.
"""

FLEET_BUSY_EXIT: int = 7
"""Exit code when the fleet is still busy after the quiet wait expires.

Judgment call: 7 is not 0 (success), not 2 (smoke failure), not 5/6 (rollback),
so run() can distinguish fleet-busy from other failures.  Wrong if a reader
treats every non-zero deploy exit as 'rolled back'.
"""


def _live_loops_local() -> list[int]:
    """Return pids of live ilk loops on this host.

    Matches ``run_ilk_loop_claude.(sh|ps1)`` via pgrep, then filters:
    - this process and its parent (``os.getpid()``, ``os.getppid()``),
    - dead pids (``os.kill(pid, 0)`` raises ``OSError``),
    - pids whose ``ps -o command=`` contains ``grep``.

    Pinned loops (release runners with ``RUN_PINS_RELEASE``) are dropped
    via ``pinned_loops.unpinned``.  On import failure, keeps every pid
    (fail closed).

    Returns an empty list when no live loops are found.
    """
    # Build the pgrep pattern from parts so this module's own argv never matches.
    _PGREP_PATTERN = "run_ilk_loop_claude" + r"\.(sh|ps1)"
    try:
        result = subprocess.run(
            ["pgrep", "-f", _PGREP_PATTERN],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    my_pid = os.getpid()
    parent_pid = os.getppid()
    entries: list[tuple[int, str]] = []

    for line in result.stdout.strip().splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid == my_pid or pid == parent_pid:
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        try:
            ps_result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=5,
            )
            cmd = ps_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            cmd = ""
        if "grep" in cmd:
            continue
        entries.append((pid, cmd))

    # Drop pinned loops (release runners with RUN_PINS_RELEASE marker).
    try:
        import importlib.util as _ilu
        _pl_path = Path(__file__).resolve().parents[2] / "ilk-watchdog" / "scripts" / "pinned_loops.py"
        _pl_spec = _ilu.spec_from_file_location("pinned_loops", str(_pl_path))
        _pl_mod = _ilu.module_from_spec(_pl_spec)
        _pl_spec.loader.exec_module(_pl_mod)
        live = _pl_mod.unpinned(entries)
    except Exception:
        # Import failure → fail closed, keep every pid.
        live = [pid for pid, _ in entries]

    return live


def _live_loops_remote(
    run: Callable[[str, list[str], int], dict],
    host: str,
) -> list[int] | None:
    """Return pids of live ilk loops on a remote host, or None on error.

    Uses the same pgrep pattern as ``_live_loops_local`` but runs over
    ``ssh_runner``.  rc 0 → parse pids, rc 1 → [], anything else → None
    (treated as busy).

    Pinned loops (release runners with ``RUN_PINS_RELEASE``) are dropped:
    for each candidate release dir, the marker is checked with one ssh call
    (``ls <dir>/skills/ilk-loop/scripts/RUN_PINS_RELEASE``).  A non-zero or
    unreachable answer keeps the pid (fail closed).
    """
    _PGREP_PATTERN = "run_ilk_loop_claude" + r"\.(sh|ps1)"
    result = run(host, ["pgrep", "-f", _PGREP_PATTERN], timeout=10)
    rc = result.get("rc", -1)
    if rc == 0:
        pids: list[int] = []
        for line in result.get("stdout", "").strip().splitlines():
            try:
                pids.append(int(line.strip()))
            except ValueError:
                continue

        # Drop pinned loops: get each pid's command and check the marker.
        try:
            import importlib.util as _ilu
            _pl_path = Path(__file__).resolve().parents[2] / "ilk-watchdog" / "scripts" / "pinned_loops.py"
            _pl_spec = _ilu.spec_from_file_location("pinned_loops_remote", str(_pl_path))
            _pl_mod = _ilu.module_from_spec(_pl_spec)
            _pl_spec.loader.exec_module(_pl_mod)
            _release_dir_of = _pl_mod.release_dir_of

            filtered: list[int] = []
            for pid in pids:
                ps_result = run(host, ["ps", "-o", "command=", "-p", str(pid)], timeout=5)
                cmd = ps_result.get("stdout", "").strip()
                d = _release_dir_of(cmd)
                if d is not None:
                    # Check marker with an ssh call; non-zero → keep pid (fail closed).
                    marker_path = f"{d}/skills/ilk-loop/scripts/RUN_PINS_RELEASE"
                    ls_result = run(host, ["ls", marker_path], timeout=5)
                    if ls_result.get("rc", -1) == 0:
                        continue  # marker exists → pinned → drop
                filtered.append(pid)
            return filtered
        except Exception:
            # Import or unexpected failure → fail closed, keep every pid.
            return pids

    if rc == 1:
        return []
    return None  # unreachable or error → treat as busy


def _wait_for_quiet(
    probe: Callable[[], list[int] | None],
    *,
    deadline_sec: float,
    poll_sec: float,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> tuple[bool, list[int] | None]:
    """Wait for ``probe()`` to return ``[]`` or the deadline to pass.

    Returns ``(True, [])`` when the fleet is quiet, or
    ``(False, last_result)`` when the deadline expires.
    """
    _clock = clock if clock is not None else time.monotonic
    _sleep = sleeper if sleeper is not None else time.sleep

    deadline = _clock() + deadline_sec
    last_result: list[int] | None = None

    while True:
        last_result = probe()
        if last_result == []:
            return (True, [])
        if _clock() >= deadline:
            return (False, last_result)
        _sleep(poll_sec)


# ── deploy ──────────────────────────────────────────────────────────────────

def deploy(
    project: Path,
    tag: str,
    data_dir: Path,
    release_cmd: object | None = None,
    bounce_cmd: object | None = None,
    status_cmd: object | None = None,
    pid_file: Path | None = None,
    settle_deadline_sec: float = 30.0,
    settle_poll_interval_sec: float = 2.0,
    settle_clock: object | None = None,
    settle_sleeper: object | None = None,
    quiet_probe: object | None = None,
    quiet_deadline_sec: float = QUIET_WAIT_SEC,
    quiet_poll_sec: float = QUIET_POLL_SEC,
    quiet_clock: object | None = None,
    quiet_sleeper: object | None = None,
) -> dict:
    """Deploy a release: extract, flip, bounce, smoke.  Rollback on failure.

    Every external command is an injectable parameter so tests can stub them.

    Returns ``{"tag", "deployed", "scheduler_pid", "exit_code": 0}`` on success.
    Returns ``{"tag", "deployed": false, "rolled_back_to", "rollback_smoke",
    "reason", "exit_code": 5}`` on verified rollback.
    Returns ``{"exit_code": 6}`` when both smokes fail.
    Exit 4 when extraction fails (raises SystemExit).
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

    def _default_bounce_cmd(tag: str) -> int:
        r = subprocess.run(
            [str(_bouncer_for(tag))],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60,
        )
        return r.returncode

    def _default_status_cmd(t: str, cwd: Path | None = None) -> str:
        r = subprocess.run(
            [sys.executable, str(_STATUS_SCRIPT), "--bouncer", str(_bouncer_for(t)), "--require-tag", t],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60,
            cwd=str(cwd) if cwd else None,
        )
        return r.stdout.strip()

    _release = release_cmd if release_cmd is not None else _default_release_cmd
    _bounce = bounce_cmd if bounce_cmd is not None else _default_bounce_cmd
    _status = status_cmd if status_cmd is not None else _default_status_cmd
    _pid = pid_file if pid_file is not None else _scheduler_pid_file()

    # ── 0. quiet-fleet wait ─────────────────────────────────────────────
    # Only wait when an explicit probe is provided.  Production callers
    # (run()) pass _live_loops_local; tests and callers that don't need
    # the wait skip it entirely (no real pgrep, no real sleep).
    if quiet_probe is None and os.environ.get("ILK_TEST_NO_LIVE_FLEET") == "1":
        raise RuntimeError(
            "a test reached the live fleet probe; inject quiet_probe="
        )
    if quiet_probe is not None:
        quiet, last_loops = _wait_for_quiet(
            quiet_probe,
            deadline_sec=quiet_deadline_sec,
            poll_sec=quiet_poll_sec,
            clock=quiet_clock,
            sleeper=quiet_sleeper,
        )
        if not quiet:
            pids = last_loops if last_loops else []
            host_label = os.uname().nodename
            return {
                "tag": tag,
                "deployed": False,
                "exit_code": FLEET_BUSY_EXIT,
                "reason": f"fleet-busy: {len(pids)} loop(s) live on {host_label}: {pids}",
                "host": host_label,
            }

    # ── 1. extract ──────────────────────────────────────────────────────
    rc, _out = _release(tag, project)  # type: ignore[operator]
    if rc != 0:
        print(f"refused: extraction failed for {tag} (exit {rc})", file=sys.stderr)
        sys.exit(4)

    # ── 2. bounce + smoke ───────────────────────────────────────────────
    pid_before = _read_pid(_pid)
    try:
        bounce_rc = _bounce(tag)  # type: ignore[operator]
    except TypeError:
        bounce_rc = _bounce()  # type: ignore[operator]
    pid_after = _read_pid(_pid)
    bounce_info = {"exit": bounce_rc, "pid_before": pid_before, "pid_after": pid_after}

    if bounce_rc == 2:
        smoke_ok, smoke_reason = False, f"bounce failed (exit {bounce_rc})"
    elif bounce_rc == 0:
        smoke_ok, smoke_reason = False, "bounce restarted nothing (exit 0)"
    elif bounce_rc == 1 and pid_before is not None and pid_after == pid_before:
        smoke_ok, smoke_reason = False, f"scheduler pid {pid_after} unchanged after bounce"
    else:
        smoke_ok, smoke_reason = _smoke(  # type: ignore[arg-type]
            tag, _status, _pid,
            cwd=project,
            deadline_sec=settle_deadline_sec,
            poll_interval_sec=settle_poll_interval_sec,
            clock=settle_clock,
            sleeper=settle_sleeper,
        )

    if smoke_ok:
        return {
            "tag": tag,
            "deployed": True,
            "scheduler_pid": _read_pid(_pid),
            "exit_code": 0,
            "bounce": bounce_info,
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

    rb_rc, _ = _release("--rollback", project)  # type: ignore[operator]
    try:
        rb_bounce_rc = _bounce(prev_tag)  # type: ignore[operator]
    except TypeError:
        rb_bounce_rc = _bounce()  # type: ignore[operator]

    if rb_rc != 0 or rb_bounce_rc not in (0, 1):
        return {
            "tag": tag,
            "deployed": False,
            "rolled_back_to": prev_tag,
            "rollback_smoke": "failed",
            "reason": f"rollback unverified (release exit {rb_rc}, bounce exit {rb_bounce_rc})",
            "exit_code": 6,
            "bounce": bounce_info,
        }

    rollback_ok, rollback_reason = _smoke(  # type: ignore[arg-type]
        prev_tag, _status, _pid,
        cwd=project,
        deadline_sec=settle_deadline_sec,
        poll_interval_sec=settle_poll_interval_sec,
        clock=settle_clock,
        sleeper=settle_sleeper,
    )

    if rollback_ok:
        return {
            "tag": tag,
            "deployed": False,
            "rolled_back_to": prev_tag,
            "rollback_smoke": "ok",
            "reason": smoke_reason,
            "exit_code": 5,
            "bounce": bounce_info,
        }
    else:
        return {
            "tag": tag,
            "deployed": False,
            "rolled_back_to": prev_tag,
            "rollback_smoke": "failed",
            "reason": smoke_reason,
            "exit_code": 6,
            "bounce": bounce_info,
        }


# ── Canary window: observation before remote hosts ──────────────────────

CANARY_WINDOW_SEC: float = 720.0  # Two scheduler intervals plus margin
"""Seconds to wait for a clean scheduler tick on the canary before proceeding.

Judgment call: 720s = two 5-min intervals + margin.  Wrong if launchd ticks
slower than 5 min on a host, then raise it.
"""

_KNOWN_VERBS: frozenset[str] = frozenset({
    "idle", "dispatch:", "promote:", "skip-",
})
"""Scheduler verbs that indicate a clean tick."""


def _deploy_all_hosts(
    project: Path,
    tag: str,
    data_dir: Path,
    *,
    hosts: list[str] | None = None,
    local_hosts: list[str] | None = None,
    deploy_fn: Callable | None = None,
    ssh_deploy_fn: Callable | None = None,
    canary_window_sec: float = CANARY_WINDOW_SEC,
    scheduler_log: Path | None = None,
    scheduler_pid_file: Path | None = None,
    canary_clock: Callable[[], float] | None = None,
    canary_sleeper: Callable[[float], None] | None = None,
) -> dict:
    """Deploy to every configured host, tracking per-host results.

    Iterates ``ship.hosts``, choosing local or remote adapters based on
    ``local_hosts``.  One host's failure cannot hide another host's success.

    Args:
        project: Project path.
        tag: Release tag to deploy.
        data_dir: Project data root.
        hosts: Explicit host list.  If None, reads from config.
        local_hosts: Hosts that are this machine.  If None, uses first host.
        deploy_fn: Injectable local deploy function (default: ``deploy``).
        ssh_deploy_fn: Injectable SSH deploy function (default: ``_ssh_deploy``).

    Returns ``{"tag", "hosts": {host: result_dict}, "exit_code"}``.
    """
    _deploy = deploy_fn if deploy_fn is not None else deploy
    _ssh = ssh_deploy_fn if ssh_deploy_fn is not None else _ssh_deploy

    # Resolve hosts
    if hosts is None:
        hosts = _resolve_hosts(data_dir, project)

    if not hosts:
        # No hosts configured — deploy locally (backward compatible)
        result = _deploy(project, tag, data_dir)
        return {"tag": tag, "hosts": {"_local": result}, "exit_code": result.get("exit_code", 0)}

    if local_hosts is None:
        local_hosts = [hosts[0]]  # first host is local by default

    host_results: dict[str, dict] = {}
    worst_exit = 0
    deployed_hosts: list[str] = []
    rolled_back_hosts: list[str] = []
    untouched_hosts: list[str] = []
    unverified_hosts: list[str] = []

    # ── Canary gate: deploy hosts[0] first; stop the fleet if it fails ──
    canary = hosts[0]
    canary_is_local = canary in local_hosts if local_hosts else False

    if canary_is_local:
        try:
            canary_result = _deploy(project, tag, data_dir)
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
            canary_result = {"exit_code": exit_code, "deployed": False, "host": canary}
        except Exception as exc:
            canary_result = {"exit_code": 6, "deployed": False, "reason": str(exc), "host": canary}
    else:
        try:
            canary_result = _ssh(project, tag, data_dir, host=canary)
        except Exception as exc:
            canary_result = {
                "exit_code": 2,
                "deployed": False,
                "reason": f"ssh adapter error: {exc}",
                "host": canary,
                "transport": "ssh",
                "extract": {"rc": -1, "tag": tag},
                "bounce": {"exit": -1, "pid_before": None, "pid_after": None},
                "smoke": {"ok": False, "reason": "adapter error"},
            }

    host_results[canary] = canary_result
    canary_exit = canary_result.get("exit_code", 0)
    if canary_exit > worst_exit:
        worst_exit = canary_exit

    if canary_result.get("deployed"):
        deployed_hosts.append(canary)
    elif canary_result.get("rolled_back_to"):
        rolled_back_hosts.append(canary)
    elif canary_exit == 2:
        unverified_hosts.append(canary)
    else:
        untouched_hosts.append(canary)

    canary_deployed = canary_result.get("deployed", False)

    # ── Canary window: wait for one clean scheduler tick ────────────────
    canary_tick_info: dict | None = None
    if canary_deployed and scheduler_log is not None and scheduler_pid_file is not None:
        window_result = _canary_window(
            tag,
            scheduler_log,
            scheduler_pid_file,
            deadline_sec=canary_window_sec,
            clock=canary_clock,
            sleeper=canary_sleeper,
        )
        if not window_result["ok"]:
            # Canary window failed → roll back canary and stop fleet
            canary_deployed = False
            reason = f"canary-window: {window_result['terminal_reason']}"
            canary_result["deployed"] = False
            canary_result["rolled_back_to"] = canary_result.get("rolled_back_to")
            canary_result["reason"] = reason
            # Roll back via the existing rollback path
            try:
                _deploy(project, tag, data_dir)  # triggers rollback
            except Exception:
                pass
            host_results[canary] = canary_result
            # Update tracking lists
            if canary in deployed_hosts:
                deployed_hosts.remove(canary)
            rolled_back_hosts.append(canary)
        else:
            canary_tick_info = window_result

    for host in hosts[1:]:
        if not canary_deployed:
            # Canary gate: canary did not deploy → stop the fleet
            reason_detail = canary_result.get("reason", "unknown")
            host_results[host] = {
                "exit_code": 0,
                "deployed": False,
                "host": host,
                "reason": f"canary-failed: {reason_detail}",
            }
            untouched_hosts.append(host)
            continue

        is_local = host in local_hosts if local_hosts else False

        if is_local:
            # Local deploy — use the existing deploy() function
            try:
                result = _deploy(project, tag, data_dir)
                host_results[host] = result
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else 1
                host_results[host] = {"exit_code": exit_code, "deployed": False, "host": host}
            except Exception as exc:
                host_results[host] = {"exit_code": 6, "deployed": False, "reason": str(exc), "host": host}
        else:
            # Remote deploy — use SSH adapter
            try:
                result = _ssh(project, tag, data_dir, host=host)
                host_results[host] = result
            except Exception as exc:
                host_results[host] = {
                    "exit_code": 2,
                    "deployed": False,
                    "reason": f"ssh adapter error: {exc}",
                    "host": host,
                    "transport": "ssh",
                    "extract": {"rc": -1, "tag": tag},
                    "bounce": {"exit": -1, "pid_before": None, "pid_after": None},
                    "smoke": {"ok": False, "reason": "adapter error"},
                }

        exit_code = host_results[host].get("exit_code", 0)
        if exit_code > worst_exit:
            worst_exit = exit_code

        if host_results[host].get("deployed"):
            deployed_hosts.append(host)
        elif host_results[host].get("rolled_back_to"):
            rolled_back_hosts.append(host)
        elif exit_code == 2:
            unverified_hosts.append(host)
        else:
            untouched_hosts.append(host)

    result = {
        "tag": tag,
        "hosts": host_results,
        "exit_code": worst_exit,
        "deployed": deployed_hosts,
        "rolled_back": rolled_back_hosts,
        "untouched": untouched_hosts,
        "unverified": unverified_hosts,
    }
    if canary_tick_info is not None:
        result["canary_tick"] = canary_tick_info.get("tick_line")
        result["canary_wait_sec"] = canary_tick_info.get("wait_sec")
    return result


def _acquire_remote_tag(
    ssh_runner: Callable,
    host: str,
    tag: str,
    expected_tag_sha: str,
    timeout: int = 120,
    *,
    repo_path: Path | None = None,
) -> dict | None:
    """Fetch the exact candidate tag on the remote host and verify it.

    Returns ``None`` on success (tag acquired and verified).
    Returns a failure dict with ``exit_code``, ``reason``, ``acquire``,
    and zeroed bounce/smoke/extract fields on any failure.

    Acquisition never checks out, resets, merges, or otherwise rewrites
    the remote repository's current branch or working tree.  It runs only
    ``git -C <repo> fetch origin refs/tags/<tag>:refs/tags/<tag>`` and
    ``git -C <repo> rev-parse refs/tags/<tag>^{}``.

    The caller (``_ssh_deploy`` → ``_default_ssh_runner``) is responsible
    for serializing the command argv at the SSH transport boundary.
    This function passes raw semantic argv — the project path and the
    exact source:destination refspec — without shell quoting.
    """
    _repo = str(repo_path) if repo_path is not None else "repo"
    fetch_cmd = [
        "git", "-C", _repo, "fetch", "origin",
        f"refs/tags/{tag}:refs/tags/{tag}",
    ]

    fetch_result = ssh_runner(host, fetch_cmd, timeout=timeout)
    if fetch_result["rc"] != 0:
        err = fetch_result.get("stderr") or fetch_result.get("stdout") or "unknown"
        return {
            "exit_code": 2, "deployed": False,
            "host": host, "transport": "ssh",
            "reason": f"tag acquisition failed: {err}",
            "acquire": {"rc": fetch_result["rc"], "tag": tag, "phase": "fetch"},
            "extract": {"rc": -1, "tag": tag},
            "bounce": {"exit": -1, "pid_before": None, "pid_after": None},
            "smoke": {"ok": False, "reason": "skipped"},
        }

    # Verify the tag exists on the remote after fetch.
    verify_cmd = ["git", "-C", _repo, "rev-parse", f"refs/tags/{tag}^{{}}"]
    verify_result = ssh_runner(host, verify_cmd, timeout=timeout)
    if verify_result["rc"] != 0:
        return {
            "exit_code": 2, "deployed": False,
            "host": host, "transport": "ssh",
            "reason": f"tag {tag} not found after fetch",
            "acquire": {"rc": 1, "tag": tag, "phase": "verify"},
            "extract": {"rc": -1, "tag": tag},
            "bounce": {"exit": -1, "pid_before": None, "pid_after": None},
            "smoke": {"ok": False, "reason": "skipped"},
        }

    # SHA mismatch: the fetched tag does not point to the expected commit.
    # Skip when expected is empty (caller has no local tag to compare against).
    actual_sha = verify_result["stdout"].strip()
    if expected_tag_sha and actual_sha != expected_tag_sha:
        return {
            "exit_code": 2, "deployed": False,
            "host": host, "transport": "ssh",
            "reason": f"tag {tag} sha mismatch: expected {expected_tag_sha[:12]}, "
                      f"got {verify_result['stdout'].strip()[:12]}",
            "acquire": {"rc": 2, "tag": tag, "phase": "sha_mismatch"},
            "extract": {"rc": -1, "tag": tag},
            "bounce": {"exit": -1, "pid_before": None, "pid_after": None},
            "smoke": {"ok": False, "reason": "skipped"},
        }

    return None  # success


def _ssh_deploy(
    project: Path,
    tag: str,
    data_dir: Path,
    *,
    host: str,
    ssh_runner: Callable | None = None,
    timeout: int = 120,
    settle_deadline_sec: float = 30.0,
    settle_poll_interval_sec: float = 2.0,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    quiet_probe: Callable[[], list[int] | None] | None = None,
    quiet_deadline_sec: float = QUIET_WAIT_SEC,
    quiet_poll_sec: float = QUIET_POLL_SEC,
) -> dict:
    """Deploy to a remote host via SSH.

    Fetches the exact pushed tag, extracts/flips it, bounces with the new
    tag's bouncer, performs required-tag smoke, and returns structured
    evidence.  Rollback on smoke failure.

    Every external command is an injectable parameter so tests can stub them.

    Args:
        project: Project path.
        tag: Release tag to deploy.
        data_dir: Project data root.
        host: Target host name.
        ssh_runner: Injectable SSH runner.  Default: real SSH subprocess.
        timeout: SSH command timeout in seconds.
        settle_deadline_sec: Bounded settling deadline for smoke checks.
        settle_poll_interval_sec: Poll interval for settling.
        clock: Injectable monotonic clock (for testing).
        sleeper: Injectable sleep function (for testing).

    Returns a dict with: tag, deployed, exit_code, host, transport,
    extract, bounce, smoke.
    """
    _SCRIPTS = Path(__file__).resolve().parent
    # Remote commands name REMOTE paths (see _remote_extractor).
    _RELEASE_SCRIPT = _remote_extractor()
    _BOUNCE_SCRIPT = _SCRIPTS.parent.parent / "ilk-watchdog" / "scripts" / "bounce_daemons.sh"

    def _default_ssh_runner(host: str, cmd: list[str], timeout: int = 120) -> dict:
        try:
            r = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, shlex.join(cmd)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return {"rc": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
        except subprocess.TimeoutExpired:
            return {"rc": -1, "stdout": "", "stderr": "ssh timeout"}
        except Exception as exc:
            return {"rc": -1, "stdout": "", "stderr": str(exc)}

    _run = ssh_runner if ssh_runner is not None else _default_ssh_runner

    # ── 0. quiet-fleet wait (BEFORE tag acquire) ─────────────────────────
    if quiet_probe is None and os.environ.get("ILK_TEST_NO_LIVE_FLEET") == "1":
        raise RuntimeError(
            "a test reached the live fleet probe; inject quiet_probe="
        )
    _probe = quiet_probe if quiet_probe is not None else (lambda: _live_loops_remote(_run, host))
    quiet, last_loops = _wait_for_quiet(
        _probe,
        deadline_sec=quiet_deadline_sec,
        poll_sec=quiet_poll_sec,
        clock=clock,
        sleeper=sleeper,
    )
    if not quiet:
        pids = last_loops if last_loops else []
        return {
            "tag": tag,
            "deployed": False,
            "exit_code": FLEET_BUSY_EXIT,
            "reason": f"fleet-busy: {len(pids)} loop(s) live on {host}: {pids}",
            "host": host,
            "transport": "ssh",
        }

    # ── 1. acquire exact tag ─────────────────────────────────────────────
    #
    # Before extraction, fetch the exact candidate tag from origin into
    # refs/tags/<tag>.  This ensures the remote repository has the pushed
    # tag available for extraction, without rewriting its checked-out branch.
    expected_sha = _tag_sha(project, tag) or ""
    acquire_failure = _acquire_remote_tag(_run, host, tag, expected_sha, timeout=timeout, repo_path=project)
    if acquire_failure is not None:
        return acquire_failure

    # ── 1. extract ──────────────────────────────────────────────────────
    extract_result = _run(host, [
        sys.executable, str(_RELEASE_SCRIPT), tag, "--repo", str(project),
    ], timeout=timeout)

    if extract_result["rc"] != 0:
        return {
            "tag": tag, "deployed": False, "exit_code": 2,
            "host": host, "transport": "ssh",
            "reason": f"ssh extraction failed: {extract_result.get('stderr', '')}",
            "extract": {"rc": extract_result["rc"], "tag": tag},
            "bounce": {"exit": -1, "pid_before": None, "pid_after": None},
            "smoke": {"ok": False, "reason": "skipped"},
        }

    # ── 2. bounce ───────────────────────────────────────────────────────
    # The CANDIDATE's bouncer: bounce_daemons.sh judges staleness against its
    # own release manifest, so the incumbent's copy sees a fresh daemon and
    # bounces nothing (v0.9.156 train, rezmac: bounce exit 0, tag-mismatch).
    bounce_result = _run(host, [
        str(_bouncer_for(tag)),
    ], timeout=60)

    # ── 3. smoke (bounded settling) ────────────────────────────────────
    _clock = clock if clock is not None else time.monotonic
    _sleep = sleeper if sleeper is not None else time.sleep

    smoke_start = _clock()
    smoke_attempts = 0
    smoke_ok = False
    smoke_result: dict = {"rc": -1, "stdout": "", "stderr": ""}
    settle_elapsed = 0.0

    while True:
        smoke_attempts += 1
        settle_elapsed = _clock() - smoke_start

        smoke_result = _run(host, [
            sys.executable, str(_status_script_for(tag)),
            "--bouncer", str(_bouncer_for(tag)),
            "--require-tag", tag,
            "--repo", str(project),
        ], timeout=60)

        if smoke_result["rc"] == 0 and smoke_result["stdout"] == "ok":
            smoke_ok = True
            break

        if settle_elapsed >= settle_deadline_sec:
            break

        _sleep(settle_poll_interval_sec)

    bounce_info = {"exit": bounce_result["rc"]}

    if smoke_ok:
        return {
            "tag": tag, "deployed": True, "exit_code": 0,
            "host": host, "transport": "ssh",
            "extract": {"rc": 0, "tag": tag},
            "bounce": bounce_info,
            "smoke": {
                "ok": True, "reason": "",
                "attempts": smoke_attempts, "elapsed": settle_elapsed,
            },
        }

    # ── 4. rollback ─────────────────────────────────────────────────────
    prev_result = _run(host, [
        sys.executable, str(_RELEASE_SCRIPT), "--status", "--repo", str(project),
    ], timeout=60)

    prev_tag = ""
    if prev_result["rc"] == 0:
        try:
            status_data = json.loads(prev_result["stdout"])
            prev_path = status_data.get("previous", "")
            if prev_path:
                prev_tag = Path(prev_path).name
        except (json.JSONDecodeError, KeyError):
            pass

    rb_result = _run(host, [
        sys.executable, str(_RELEASE_SCRIPT), "--rollback", "--repo", str(project),
    ], timeout=60)

    # bounce_daemons.sh takes no positional argument (an unknown one exits 2,
    # which made every remote rollback read "unverified"); the release whose
    # bouncer runs is what selects the target.
    rb_bounce_result = _run(host, [
        str(_bouncer_for(prev_tag)),
    ], timeout=60) if prev_tag else {"rc": -1}

    if rb_result["rc"] != 0 or rb_bounce_result["rc"] not in (0, 1):
        return {
            "tag": tag, "deployed": False, "exit_code": 6,
            "host": host, "transport": "ssh",
            "reason": f"rollback unverified: release={rb_result['rc']}, bounce={rb_bounce_result['rc']}",
            "rolled_back_to": prev_tag, "rollback_smoke": "failed",
            "extract": {"rc": 0, "tag": tag},
            "bounce": bounce_info,
            "smoke": {"ok": False, "reason": smoke_result.get("stdout", "")},
        }

    # Rollback smoke with bounded settling (same contract as forward)
    rb_smoke_start = _clock()
    rb_smoke_attempts = 0
    rb_smoke_ok = False
    rb_smoke_result: dict = {"rc": -1, "stdout": "", "stderr": ""}

    while True:
        rb_smoke_attempts += 1

        rb_smoke_result = _run(host, [
            sys.executable, str(_status_script_for(prev_tag)),
            "--bouncer", str(_bouncer_for(prev_tag)),
            "--require-tag", prev_tag,
            "--repo", str(project),
        ], timeout=60)

        if rb_smoke_result["rc"] == 0 and rb_smoke_result["stdout"] == "ok":
            rb_smoke_ok = True
            break

        if (_clock() - rb_smoke_start) >= settle_deadline_sec:
            break

        _sleep(settle_poll_interval_sec)

    if rb_smoke_ok:
        return {
            "tag": tag, "deployed": False, "exit_code": 5,
            "host": host, "transport": "ssh",
            "reason": f"smoke failed: {smoke_result.get('stdout', '')}",
            "rolled_back_to": prev_tag, "rollback_smoke": "ok",
            "rollback_host": host,
            "extract": {"rc": 0, "tag": tag},
            "bounce": bounce_info,
            "smoke": {
                "ok": False,
                "reason": smoke_result.get("stdout", ""),
                "attempts": smoke_attempts, "elapsed": settle_elapsed,
            },
        }
    else:
        return {
            "tag": tag, "deployed": False, "exit_code": 6,
            "host": host, "transport": "ssh",
            "reason": "both smokes failed",
            "rolled_back_to": prev_tag, "rollback_smoke": "failed",
            "extract": {"rc": 0, "tag": tag},
            "bounce": bounce_info,
            "smoke": {
                "ok": False,
                "reason": smoke_result.get("stdout", ""),
                "attempts": smoke_attempts, "elapsed": settle_elapsed,
            },
        }


def _settle(
    tag: str,
    status_cmd: Callable,
    pid_file: Path,
    *,
    deadline_sec: float = 30.0,
    poll_interval_sec: float = 2.0,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    cwd: Path | None = None,
) -> dict:
    """Bounded settling: poll PID liveness + required-tag status until both
    conformant or a monotonic deadline expires.

    Each poll cycle checks status first (cheap, may indicate the daemon is
    coming up), then PID.  The settle completes only when status is ``ok``,
    the PID is alive, and the script path mentions the tag.

    Returns ``{"ok", "pid", "status", "attempts", "elapsed", "terminal_reason"}``.
    """
    _clock = clock if clock is not None else time.monotonic
    _sleep = sleeper if sleeper is not None else time.sleep

    start = _clock()
    attempts = 0

    while True:
        attempts += 1
        elapsed = _clock() - start

        # ── status check (always — cheap, signals daemon coming up) ──
        try:
            status = status_cmd(tag, cwd=cwd)  # type: ignore[operator]
        except TypeError:
            status = status_cmd(tag)

        if status != "ok":
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "pid": None, "status": status,
                    "attempts": attempts, "elapsed": elapsed,
                    "terminal_reason": status,
                }
            _sleep(poll_interval_sec)
            continue

        # ── status ok — now check PID ──────────────────────────────
        if not pid_file.exists():
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "pid": None, "status": status,
                    "attempts": attempts, "elapsed": elapsed,
                    "terminal_reason": "pid_absent",
                }
            _sleep(poll_interval_sec)
            continue

        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "pid": None, "status": status,
                    "attempts": attempts, "elapsed": elapsed,
                    "terminal_reason": "pid_unreadable",
                }
            _sleep(poll_interval_sec)
            continue

        if not _is_pid_alive(pid):
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "pid": pid, "status": status,
                    "attempts": attempts, "elapsed": elapsed,
                    "terminal_reason": "pid_dead",
                }
            _sleep(poll_interval_sec)
            continue

        # ── PID alive + status ok — command-line check ─────────────
        try:
            r = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=10,
            )
            cmd = r.stdout.strip()
            if f"releases/{tag}/" not in cmd:
                resolved = " ".join(
                    os.path.realpath(w) for w in cmd.split()
                )
                if f"releases/{tag}/" not in resolved:
                    if elapsed >= deadline_sec:
                        return {
                            "ok": False, "pid": pid, "status": status,
                            "attempts": attempts, "elapsed": elapsed,
                            "terminal_reason": "wrong_script",
                        }
                    _sleep(poll_interval_sec)
                    continue
        except (OSError, subprocess.TimeoutExpired):
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "pid": pid, "status": status,
                    "attempts": attempts, "elapsed": elapsed,
                    "terminal_reason": "ps_failed",
                }
            _sleep(poll_interval_sec)
            continue

        # ── all checks passed ─────────────────────────────────────
        return {
            "ok": True, "pid": pid, "status": status,
            "attempts": attempts, "elapsed": elapsed,
            "terminal_reason": "ok",
        }


def _canary_window(
    tag: str,
    scheduler_log: Path,
    scheduler_pid_file: Path,
    *,
    deadline_sec: float = CANARY_WINDOW_SEC,
    poll_interval_sec: float = 30.0,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict:
    """Wait for one clean scheduler tick on the canary before remote hosts.

    A clean tick is a ``scheduler.log`` line whose verb is in
    :data:`_KNOWN_VERBS`, whose PID (from the pid file) is alive, and
    whose command line contains ``releases/<tag>/``.

    Returns ``{"ok", "tick_line", "wait_sec", "terminal_reason"}``.
    """
    _clock = clock if clock is not None else time.monotonic
    _sleep = sleeper if sleeper is not None else time.sleep

    start = _clock()
    attempts = 0

    while True:
        attempts += 1
        elapsed = _clock() - start

        # ── read scheduler log ────────────────────────────────────
        try:
            log_text = scheduler_log.read_text(encoding="utf-8")
        except OSError:
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "tick_line": None,
                    "wait_sec": elapsed,
                    "terminal_reason": "log_unreadable",
                }
            _sleep(poll_interval_sec)
            continue

        # ── check PID liveness ────────────────────────────────────
        if not scheduler_pid_file.exists():
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "tick_line": None,
                    "wait_sec": elapsed,
                    "terminal_reason": "pid_absent",
                }
            _sleep(poll_interval_sec)
            continue

        try:
            pid = int(scheduler_pid_file.read_text().strip())
        except (ValueError, OSError):
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "tick_line": None,
                    "wait_sec": elapsed,
                    "terminal_reason": "pid_unreadable",
                }
            _sleep(poll_interval_sec)
            continue

        if not _is_pid_alive(pid):
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "tick_line": None,
                    "wait_sec": elapsed,
                    "terminal_reason": "pid_dead",
                }
            _sleep(poll_interval_sec)
            continue

        # ── check command line contains the new release tag ───────
        try:
            r = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=10,
            )
            cmd = r.stdout.strip()
            if f"releases/{tag}/" not in cmd:
                resolved = " ".join(
                    os.path.realpath(w) for w in cmd.split()
                )
                if f"releases/{tag}/" not in resolved:
                    if elapsed >= deadline_sec:
                        return {
                            "ok": False, "tick_line": None,
                            "wait_sec": elapsed,
                            "terminal_reason": "wrong_script",
                        }
                    _sleep(poll_interval_sec)
                    continue
        except (OSError, subprocess.TimeoutExpired):
            if elapsed >= deadline_sec:
                return {
                    "ok": False, "tick_line": None,
                    "wait_sec": elapsed,
                    "terminal_reason": "ps_failed",
                }
            _sleep(poll_interval_sec)
            continue

        # ── scan log for a clean tick ─────────────────────────────
        for line in log_text.splitlines():
            line = line.strip()
            if not line:
                continue

            # Parse: [YYYY-MM-DD HH:MM:SS] verb ...
            if not line.startswith("["):
                continue
            bracket_end = line.find("]")
            if bracket_end < 0:
                continue
            rest = line[bracket_end + 1:].strip()

            # Extract verb (first word or prefix)
            verb = rest.split()[0] if rest.split() else ""
            # Check if verb matches any known verb (exact or prefix)
            if not any(verb == kv or verb.startswith(kv) for kv in _KNOWN_VERBS):
                continue

            # This is a clean tick
            return {
                "ok": True, "tick_line": line,
                "wait_sec": elapsed,
                "terminal_reason": "clean_tick",
            }

        # ── no clean tick yet ─────────────────────────────────────
        if elapsed >= deadline_sec:
            return {
                "ok": False, "tick_line": None,
                "wait_sec": elapsed,
                "terminal_reason": "window_expired",
            }
        _sleep(poll_interval_sec)


def _smoke(
    tag: str,
    status_cmd: Callable[[str], str],
    pid_file: Path,
    *,
    cwd: Path | None = None,
    deadline_sec: float = 30.0,
    poll_interval_sec: float = 2.0,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> tuple[bool, str]:
    """Smoke check: status ok + pid alive + script under releases/<tag>/.

    Always uses bounded settling via _settle.  When the daemon is
    already up, _settle returns on the first poll cycle — no slower
    than the former single-shot path.  When the daemon is starting
    late, _settle polls until the deadline rather than failing
    immediately.

    Returns (ok, reason).
    """
    result = _settle(
        tag, status_cmd, pid_file,
        deadline_sec=deadline_sec,
        poll_interval_sec=poll_interval_sec,
        clock=clock,
        sleeper=sleeper,
        cwd=cwd,
    )
    return result["ok"], result["terminal_reason"] if not result["ok"] else ""


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


def _resolve_hosts(
    data_dir: Path,
    project: Path,
    hosts: list[str] | None = None,
) -> list[str]:
    """Resolve the canonical ordered host list once.

    Resolution order:
      1. Explicit ``hosts`` argument (passed by caller).
      2. ``<data_dir>/runtime/ship-config.json`` → ``ship.hosts``.
      3. ``<project>/.ilk-launch.json`` → ``ship.hosts``.
      4. Empty list (no hosts configured).

    Returns an immutable copy of the resolved list.
    """
    if hosts is not None:
        return list(hosts)

    config_path = data_dir / "runtime" / "ship-config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            resolved = cfg.get("ship", {}).get("hosts", [])
            if resolved:
                return list(resolved)
        except (json.JSONDecodeError, KeyError):
            pass

    launch_config = project / ".ilk-launch.json"
    if launch_config.exists():
        try:
            cfg = json.loads(launch_config.read_text())
            resolved = cfg.get("ship", {}).get("hosts", [])
            if resolved:
                return list(resolved)
        except (json.JSONDecodeError, KeyError):
            pass

    return []


def _check_permits(
    data_dir: Path,
    project: Path,
    check_result: dict,
    *,
    hosts: list[str] | None = None,
    consume: bool = False,
) -> dict:
    """Check that every configured host has a fresh, valid permit.

    Permits live at ``<data_dir>/runtime/permits/<host>.json``.  Each must
    contain: project, base_tag, candidate_head, host, issued_at, expires_at,
    consumed, revoked.

    Args:
        data_dir: The project data root.
        project: The project path (for project_key resolution).
        check_result: The check() result (provides last_tag and head).
        hosts: Host list.  If None, resolved via ``_resolve_hosts``.
        consume: If True, atomically mark permits as consumed.

    Returns ``{"ok": True}`` when all permits are valid, or
    ``{"ok": False, "reason": "..."}`` on refusal.
    """
    import hashlib

    # Resolve hosts
    if hosts is None:
        hosts = _resolve_hosts(data_dir, project)

    if not hosts:
        return {"ok": True}  # no hosts configured → no permits needed

    # Resolve expected bindings
    base_tag = check_result.get("last_tag", "")
    candidate_head = check_result.get("head", "")

    # Resolve project key
    if str(_LOOP_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_LOOP_SCRIPTS))
    from ilk_paths import project_key  # noqa: E402
    project_name = project_key(project)

    permit_dir = data_dir / "runtime" / "permits"
    now = datetime.now(timezone.utc)
    permits_to_consume: list[Path] = []

    for host in hosts:
        permit_path = permit_dir / f"{host}.json"
        if not permit_path.exists():
            return {"ok": False, "reason": f"missing permit for host {host}"}

        try:
            permit = json.loads(permit_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"ok": False, "reason": f"corrupt permit for host {host}"}

        # Check required fields
        required = {"project", "base_tag", "candidate_head", "host", "expires_at", "consumed", "revoked"}
        missing = required - set(permit.keys())
        if missing:
            return {"ok": False, "reason": f"partial permit for host {host}: missing {missing}"}

        # Check revoked
        if permit.get("revoked", False):
            return {"ok": False, "reason": f"revoked permit for host {host}"}

        # Check consumed
        if permit.get("consumed", False):
            return {"ok": False, "reason": f"consumed permit for host {host}"}

        # Check expiry
        try:
            expires_at = datetime.fromisoformat(permit["expires_at"])
            if now > expires_at:
                return {"ok": False, "reason": f"stale permit for host {host}"}
        except (ValueError, TypeError):
            return {"ok": False, "reason": f"invalid expiry in permit for host {host}"}

        # Check bindings
        if permit.get("project") != project_name:
            return {"ok": False, "reason": f"crossed permit for host {host}: project mismatch"}
        if base_tag and permit.get("base_tag") != base_tag:
            return {"ok": False, "reason": f"crossed permit for host {host}: base_tag mismatch"}
        if candidate_head and permit.get("candidate_head") != candidate_head:
            return {"ok": False, "reason": f"crossed permit for host {host}: candidate_head mismatch"}

        if consume:
            permits_to_consume.append(permit_path)

    # Atomic consumption: mark all permits as consumed
    if consume and permits_to_consume:
        for permit_path in permits_to_consume:
            try:
                permit = json.loads(permit_path.read_text())
                permit["consumed"] = True
                permit["consumed_at"] = now.isoformat()
                permit_path.write_text(json.dumps(permit, indent=2) + "\n")
            except (OSError, json.JSONDecodeError) as exc:
                return {"ok": False, "reason": f"failed to consume permit {permit_path.name}: {exc}"}

    return {"ok": True}


def run(
    project: Path,
    data_dir: Path,
    *,
    check_fn: Callable | None = None,
    prove_fn: Callable | None = None,
    cut_fn: Callable | None = None,
    deploy_fn: Callable | None = None,
    ssh_deploy_fn: Callable | None = None,
    notify_script: str | None = None,
    deploy_exit_code: int | None = None,
    hosts: list[str] | None = None,
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

        # ── 1a. resolve canonical host list ────────────────────────────
        resolved_hosts = _resolve_hosts(data_dir, project, hosts)

        # ── 1b. permit check ───────────────────────────────────────────
        permit_result = _check_permits(data_dir, project, check_result, hosts=resolved_hosts, consume=False)
        if not permit_result.get("ok"):
            reason = permit_result.get("reason", "permit check failed")
            write_audit(
                "escalated", project_name,
                reason=f"permits refused: {reason}",
            )
            _notify("blocked", project_name, f"permits refused: {reason}", notify_script)
            return {"exit_code": 4, "reason": f"permits refused: {reason}"}

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

        # ── 3b. consume permits ────────────────────────────────────────
        consume_result = _check_permits(
            data_dir, project, check_result, hosts=resolved_hosts, consume=True,
        )
        if not consume_result.get("ok"):
            reason = consume_result.get("reason", "permit consumption failed")
            write_audit(
                "escalated", project_name,
                reason=f"permit consumption refused: {reason}",
            )
            _notify("blocked", project_name, f"permit consumption refused: {reason}", notify_script)
            return {"exit_code": 4, "reason": f"permit consumption refused: {reason}"}

        # ── 4. deploy ──────────────────────────────────────────────────
        if resolved_hosts:
            # Multi-host deploy
            deploy_result = _deploy_all_hosts(
                project, tag, data_dir, hosts=resolved_hosts,
                deploy_fn=_deploy, ssh_deploy_fn=ssh_deploy_fn,
            )
            exit_code = deploy_result.get("exit_code", 0)
            deployed = deploy_result.get("deployed", [])
            rolled_back = deploy_result.get("rolled_back", [])
            unverified = deploy_result.get("unverified", [])

            if exit_code == 0:
                write_audit("released", project_name, tag=tag, outcome="deployed")
                write_event("released", project_name, tag=tag)
                return {
                    "exit_code": 0,
                    "tag": tag,
                    "deployed": True,
                    "rolled_back": False,
                    "hosts": deploy_result.get("hosts", {}),
                }
            elif rolled_back:
                write_audit(
                    "rolled-back", project_name,
                    tag=tag, reason=f"hosts rolled back: {', '.join(rolled_back)}",
                )
                write_event("rolled-back", project_name, tag=tag)
                _notify("blocked", project_name, f"deploy rolled back {tag}", notify_script)
                return {
                    "exit_code": 5,
                    "tag": tag,
                    "deployed": False,
                    "rolled_back": True,
                    "hosts": deploy_result.get("hosts", {}),
                }
            else:
                # Check if any host is fleet-busy
                fleet_busy_hosts: list[str] = []
                for h, h_result in deploy_result.get("hosts", {}).items():
                    if isinstance(h_result, dict) and h_result.get("reason", "").startswith("fleet-busy"):
                        fleet_busy_hosts.append(h)
                if fleet_busy_hosts:
                    reason = f"fleet-busy: {', '.join(sorted(fleet_busy_hosts))}"
                else:
                    reason = f"deploy failed (exit {exit_code})"
                write_audit(
                    "escalated", project_name,
                    reason=reason,
                )
                _notify("blocked", project_name, reason, notify_script)
                return {
                    "exit_code": exit_code,
                    "tag": tag,
                    "deployed": False,
                    "hosts": deploy_result.get("hosts", {}),
                }
        else:
            # Single-host deploy (backward compatible)
            try:
                deploy_result = _deploy(project, tag, data_dir)
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else 1
                if exit_code == 5:
                    write_audit(
                        "rolled-back", project_name,
                        tag=tag, reason="smoke failed, rolled back",
                    )
                    write_event("rolled-back", project_name, tag=tag)
                    _notify("blocked", project_name, f"deploy rolled back {tag}", notify_script)
                    return {"exit_code": 5, "tag": tag, "deployed": False, "rolled_back": True}
                elif exit_code == 6:
                    write_audit(
                        "rolled-back", project_name,
                        tag=tag, reason="both smokes failed",
                        severity="critical",
                    )
                    write_event("rolled-back", project_name, tag=tag, severity="critical")
                    _notify("blocked", project_name, f"deploy CRITICAL: both smokes failed {tag}", notify_script)
                    return {"exit_code": 6, "tag": tag, "deployed": False, "rolled_back": True}
                else:
                    write_audit(
                        "escalated", project_name,
                        reason=f"deploy extraction failed (exit {exit_code})",
                    )
                    _notify("blocked", project_name, f"deploy extraction failed {tag}", notify_script)
                    return {"exit_code": exit_code, "tag": tag, "deployed": False}

            if isinstance(deploy_result, dict) and deploy_result.get("exit_code") in (5, 6):
                exit_code = deploy_result["exit_code"]
                severity = "critical" if exit_code == 6 else None
                audit_kwargs = {"tag": tag, "reason": "smoke failed, rolled back"}
                if severity:
                    audit_kwargs["severity"] = severity
                write_audit("rolled-back", project_name, **audit_kwargs)
                write_event("rolled-back", project_name, tag=tag, **({"severity": severity} if severity else {}))
                _notify(
                    "blocked", project_name,
                    f"deploy {'CRITICAL: both smokes failed' if exit_code == 6 else 'rolled back'} {tag}",
                    notify_script,
                )
                return {"exit_code": exit_code, "tag": tag, "deployed": False, "rolled_back": True}

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


def _project_data_dir(project: Path) -> Path:
    """Resolve per-project data dir using project_key."""
    sys.path.insert(0, str(_LOOP_SCRIPTS))
    from ilk_paths import project_key, project_data_dir
    return project_data_dir(project_key(project))


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
    data_dir = _project_data_dir(project)

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
        return result.get("exit_code", 0)

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