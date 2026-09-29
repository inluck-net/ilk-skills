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
) -> None:
    """Append a revert row to ``ship-reverts.jsonl``.

    The file is append-only.  Each row records one revert event.
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


def assemble_revert_notice(
    revert_rows: list[dict],
    shipped_slugs: set[str],
) -> str:
    """Assemble the revert notice for the worker prompt.

    Returns one line per revert row whose slug is still not shipped.
    Returns empty string when there are no applicable reverts.
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
    return "\n".join(lines)
