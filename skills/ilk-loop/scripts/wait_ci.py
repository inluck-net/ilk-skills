#!/usr/bin/env python3
"""Gate 2 v0: poll Gitee commit status until green, red, or timeout.

Exit codes:
  0 = CI green
  1 = CI red/failure
  2 = timeout
  3 = missing Gitee token / non-Gitee remote

Stdout: JSON {state, ci_run_url, summary, elapsed_seconds}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GITEE_API = "https://gitee.com/api/v5"
PROGRESS_INTERVAL_SEC = 300
INITIAL_SLEEP_SEC = 30
NOT_FOUND_STREAK_LIMIT = 3


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def resolve_gitee_token(project: Path) -> str | None:
    token = os.environ.get("GITEE_TOKEN", "").strip()
    if token:
        return token

    config_path = Path.home() / ".cursor" / "ilk-loop" / "config.json"
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            token = (data.get("gitee") or {}).get("token", "").strip()
            if token:
                return token
        except (json.JSONDecodeError, OSError):
            pass

    try:
        remote = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return None

    host = _gitee_host_from_remote(remote)
    if not host:
        return None

    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost={host}\n\n",
            capture_output=True,
            text=True,
            check=True,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                pwd = line.split("=", 1)[1].strip()
                if pwd:
                    return pwd
    except subprocess.CalledProcessError:
        pass

    return None


def _gitee_host_from_remote(remote: str) -> str | None:
    if "gitee.com" not in remote.lower():
        return None
    return "gitee.com"


def parse_owner_repo(project: Path) -> tuple[str, str] | None:
    try:
        remote = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return None

    patterns = [
        r"gitee\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)",
        r"gitee\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)",
    ]
    for pat in patterns:
        m = re.search(pat, remote, re.IGNORECASE)
        if m:
            return m.group("owner"), m.group("repo")
    return None


def fetch_statuses(owner: str, repo: str, commit: str, token: str) -> list[dict]:
    qs = urllib.parse.urlencode({"access_token": token})
    url = f"{GITEE_API}/repos/{owner}/{repo}/commits/{commit}/statuses?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "message" in data:
        raise RuntimeError(str(data.get("message")))
    return []


def summarize_statuses(statuses: list[dict]) -> tuple[str, str, str]:
    """Return (aggregate_state, ci_run_url, summary)."""
    if not statuses:
        return "pending", "", "No CI statuses reported yet"

    states = [str(s.get("state", "")).lower() for s in statuses]
    target_urls = [s.get("target_url") or s.get("url") or "" for s in statuses]
    ci_url = next((u for u in target_urls if u), "")

    if any(s in ("failure", "error", "failed") for s in states):
        failed = [
            f"{s.get('context', 'ci')}: {s.get('description', s.get('state', 'failure'))}"
            for s in statuses
            if str(s.get("state", "")).lower() in ("failure", "error", "failed")
        ]
        return "failure", ci_url, "; ".join(failed[:5]) or "CI failed"

    if all(s in ("success", "successful") for s in states if s):
        return "success", ci_url, "All CI checks passed"

    pending = [s for s in states if s in ("pending", "")]
    if pending or any(s not in ("success", "successful") for s in states):
        return "pending", ci_url, f"{len(statuses)} status(es), still pending"

    return "pending", ci_url, "CI pending"


def emit_result(state: str, ci_run_url: str, summary: str, elapsed: float, exit_code: int) -> int:
    payload = {
        "state": state,
        "ci_run_url": ci_run_url,
        "summary": summary,
        "elapsed_seconds": round(elapsed, 1),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for Gitee CI on a commit")
    parser.add_argument("--project", required=True, help="Project root with git repo")
    parser.add_argument("--commit", required=True, help="Commit SHA to poll")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in minutes")
    parser.add_argument("--poll", type=int, default=30, help="Poll interval in seconds")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not (project / ".git").exists() and not (project / ".git").is_file():
        eprint(f"[wait_ci] not a git repo: {project}")
        return emit_result("skipped", "", "not a git repo", 0, 3)

    owner_repo = parse_owner_repo(project)
    if not owner_repo:
        eprint("[wait_ci] origin is not a Gitee remote; skipping gate 2")
        return emit_result("skipped", "", "non-Gitee remote", 0, 3)

    token = resolve_gitee_token(project)
    if not token:
        eprint("[wait_ci] GITEE_TOKEN missing (env, config.json, git credential)")
        return emit_result("skipped", "", "missing Gitee token", 0, 3)

    owner, repo = owner_repo
    timeout_sec = max(1, args.timeout) * 60
    poll_sec = max(5, args.poll)
    start = time.monotonic()
    last_progress = start

    eprint(f"[wait_ci] waiting for Gitee CI on {owner}/{repo}@{args.commit[:8]}...")
    time.sleep(INITIAL_SLEEP_SEC)

    not_found_streak = 0
    while True:
        elapsed = time.monotonic() - start
        try:
            statuses = fetch_statuses(owner, repo, args.commit, token)
            state, ci_url, summary = summarize_statuses(statuses)
            not_found_streak = 0
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:200]
            eprint(f"[wait_ci] HTTP {exc.code}: {body}")
            if exc.code in (401, 403):
                return emit_result("failure", "", f"auth error HTTP {exc.code}", elapsed, 3)
            if exc.code == 404:
                # Commit not on remote yet (or never pushed). Distinct from
                # "CI failed" -- treat as skipped after a short streak so the
                # loop does not waste timeout budget on a missing ref.
                not_found_streak += 1
                if not_found_streak >= NOT_FOUND_STREAK_LIMIT:
                    return emit_result(
                        "skipped",
                        "",
                        f"commit not on remote after {not_found_streak} polls (404); did you push?",
                        elapsed,
                        3,
                    )
                state, ci_url, summary = "pending", "", "HTTP 404 (commit not on remote yet)"
            else:
                state, ci_url, summary = "pending", "", f"HTTP {exc.code}, retrying"
        except Exception as exc:  # noqa: BLE001
            eprint(f"[wait_ci] poll error: {exc}")
            state, ci_url, summary = "pending", "", str(exc)

        if state == "success":
            return emit_result("success", ci_url, summary, elapsed, 0)
        if state == "failure":
            return emit_result("failure", ci_url, summary, elapsed, 1)

        if elapsed >= timeout_sec:
            return emit_result("timeout", ci_url, summary or "CI timeout", elapsed, 2)

        now = time.monotonic()
        if now - last_progress >= PROGRESS_INTERVAL_SEC:
            mins = int(elapsed // 60)
            eprint(
                f"[wait_ci] still pending… elapsed={mins}m, ci_url={ci_url or 'n/a'}"
            )
            last_progress = now

        time.sleep(poll_sec)


if __name__ == "__main__":
    raise SystemExit(main())
