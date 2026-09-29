"""Revert notice recording and prompt assembly.

Part of sub-plan ``a-reverted-ship-is-announced`` (MASTER-2026-09-29c).

When the runner reverts a shipped sub-plan (ship-integrity violation,
inconclusive gate, one-ship enforcement, or final-gate violation), it
appends a row to ``<runtime>/launcher/ship-reverts.jsonl``.  At the next
iteration, the prompt carries a notice line for each revert whose slug
is still not shipped.
"""
from __future__ import annotations

import json
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────────────

VALID_SITES = {"integrity", "inconclusive", "one-ship", "final-gate"}

REQUIRED_FIELDS = {
    "slug",
    "ship_commit",
    "reason",
    "from_status",
    "to_status",
    "from_step",
    "to_step",
    "run_id",
    "iteration",
    "timestamp",
    "site",
}


# ── Recording ────────────────────────────────────────────────────────────────


def append_revert_row(
    reverts_path: Path | str,
    *,
    slug: str,
    ship_commit: str | None,
    reason: str,
    from_status: str,
    to_status: str,
    from_step: int | None,
    to_step: int | None,
    run_id: str,
    iteration: int,
    timestamp: str,
    site: str,
    red_step: int | None = None,
    red_step_commits: list[str] | None = None,
    attribution: str | None = None,
) -> None:
    """Append a revert row to ``ship-reverts.jsonl``.

    The file is append-only.  Each row records one revert event.

    When the revert is because a step's gate was red, *red_step* names
    the failing step and *red_step_commits* lists the SHAs whose
    ``[plan:<slug>#step-<red_step>]`` trailer claims that step (or, on a
    shared remote, every commit in the iteration's range, marked with
    *attribution* ``"unfiltered-no-trailers"``).
    """
    if site not in VALID_SITES:
        raise ValueError(f"invalid site {site!r}; must be one of {VALID_SITES}")

    row = {
        "slug": slug,
        "ship_commit": ship_commit,
        "reason": reason,
        "from_status": from_status,
        "to_status": to_status,
        "from_step": from_step,
        "to_step": to_step,
        "run_id": run_id,
        "iteration": iteration,
        "timestamp": timestamp,
        "site": site,
    }
    if red_step is not None:
        row["red_step"] = red_step
    if red_step_commits is not None:
        row["red_step_commits"] = red_step_commits
    if attribution is not None:
        row["attribution"] = attribution

    path = Path(reverts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_revert_rows(reverts_path: Path | str) -> list[dict]:
    """Read all revert rows from ``ship-reverts.jsonl``.

    Returns an empty list if the file does not exist or is empty.
    Skips unparseable lines (fail-closed on unreadable records).
    """
    path = Path(reverts_path)
    if not path.exists():
        return []

    rows: list[dict] = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ── Prompt assembly ──────────────────────────────────────────────────────────

_REVERT_NOTICE_TEMPLATE = (
    "REVERTED BY THE RUNNER: {slug} — "
    "#ship {ship_commit} was undone ({reason}). "
    "This is not a desync: do not set it shipped by hand; "
    "make its final gate pass."
)

_RED_STEP_COMMIT_TEMPLATE = (
    "commit {sha7} claims {slug} step {red_step}, but that step's "
    "gate was red — its message is not a verdict; re-run the gate "
    "before relying on it."
)


def assemble_revert_notice(
    revert_rows: list[dict],
    shipped_slugs: set[str],
) -> str:
    """Assemble the revert notice for the worker prompt.

    Returns one line per revert row whose slug is still not shipped.
    Returns empty string when there are no applicable reverts.

    When a row carries ``red_step`` and ``red_step_commits``, an
    additional line names each commit that claimed the red step.
    """
    lines: list[str] = []
    for row in revert_rows:
        slug = row.get("slug", "")
        if slug in shipped_slugs:
            continue
        ship_commit = row.get("ship_commit") or "unknown"
        reason = row.get("reason") or "unknown"
        lines.append(
            _REVERT_NOTICE_TEMPLATE.format(
                slug=slug,
                ship_commit=ship_commit[:7] if ship_commit != "unknown" else ship_commit,
                reason=reason,
            )
        )
        # Red-step notice: name each commit that claimed the red step.
        red_step = row.get("red_step")
        red_step_commits = row.get("red_step_commits")
        if red_step is not None and red_step_commits:
            for sha in red_step_commits:
                lines.append(
                    _RED_STEP_COMMIT_TEMPLATE.format(
                        sha7=sha[:7],
                        slug=slug,
                        red_step=red_step,
                    )
                )
    return "\n".join(lines)
