"""Lark → per-project tracker PULL adapter (dual-path).

Pulls triaged (可执行) records from a Lark Bitable via an injectable client
and upserts each into the project tracker with ``source="lark"`` and
``source_id=<record_id>``.

Design decisions (batch 2 convergence):
  - **Additive / dual-path.** Does NOT touch existing ``list``/``pull-new``
    verbs or the direct Lark→/ilk-plan flow.
  - **Field ownership: Lark owns content.** On pull, the Lark record's
    title/description/priority OVERWRITE the tracker entry's content fields.
  - ``source_id`` = Lark ``record_id`` so re-syncs upsert one entry per
    record (relies on batch-1's ``(source, source_id)`` upsert).

Usage (programmatic — the CLI verb lives in ``cli.py``)::

    from lark_to_tracker import sync
    sync(client, project="/path/to/project")
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Protocol


# ── Import project_tracker from sibling skill ────────────────────────────────

_HERE = Path(__file__).resolve()
_FEEDBACK_SCRIPTS = _HERE.parent.parent.parent / "ilk-feedback" / "scripts"
if str(_FEEDBACK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_FEEDBACK_SCRIPTS))

import project_tracker  # noqa: E402


# ── Injectable client protocol ───────────────────────────────────────────────

class LarkClient(Protocol):
    """Minimal interface the sync and writeback functions need from a Lark client."""

    def list_records(
        self,
        *,
        filter_expr: dict | None = None,
        max_records: int | None = None,
    ) -> list[dict]:
        """Return records matching *filter_expr*."""
        ...

    def get_record(self, record_id: str) -> dict:
        """Return a single record by *record_id*."""
        ...

    def update_record(self, record_id: str, fields: dict) -> dict:
        """Update *fields* on the record identified by *record_id*."""
        ...


# ── Field mapping helpers ────────────────────────────────────────────────────

def _flatten_text(value: Any) -> str:
    """Bitable text fields come back as list of segment dicts; flatten."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for seg in value:
            if isinstance(seg, dict):
                parts.append(seg.get("text") or seg.get("name") or "")
            else:
                parts.append(str(seg))
        return "".join(parts)
    if isinstance(value, dict):
        return value.get("text") or value.get("name") or ""
    return str(value)


# ── Core sync function ───────────────────────────────────────────────────────

def sync(
    client: LarkClient,
    *,
    project: str | Path | None = None,
    key: str | None = None,
    status: str = "可执行",
) -> int:
    """Pull *status* records from *client* and upsert into the project tracker.

    Returns the number of records processed.
    """
    filter_expr = {
        "conjunction": "and",
        "conditions": [
            {"field_name": "状态", "operator": "is", "value": [status]},
        ],
    }
    records = client.list_records(filter_expr=filter_expr)

    for rec in records:
        fields = rec.get("fields") or {}
        record_id = rec.get("record_id", "")
        title = _flatten_text(fields.get("标题"))
        description = _flatten_text(fields.get("原文描述") or fields.get("描述") or "")
        priority = fields.get("紧急度") or fields.get("AI 优先级建议") or ""

        lark_type = fields.get("类型") or ""

        project_tracker.add(
            title=title or f"lark:{record_id}",
            source="lark",
            source_id=record_id,
            kind=_map_kind(str(lark_type)),
            gap=description or title,
            project=project,
            key=key,
            severity=_map_priority(str(priority)),
        )

    return len(records)


def _map_priority(lark_priority: str) -> str:
    """Map Lark 优先级/severity to tracker severity (default ``medium``)."""
    p = lark_priority.strip().lower()
    if p in ("紧急", "p0", "critical", "high"):
        return "high"
    if p in ("高", "p1", "medium-high"):
        return "high"
    if p in ("中", "p2", "medium"):
        return "medium"
    if p in ("低", "p3", "low"):
        return "low"
    return "medium"


def _map_kind(lark_type: str) -> str:
    """Map Lark 类型 to tracker kind (default ``feature``).

    Known mappings: bug→bug, 需求/feature→feature, toolkit→toolkit.
    Unknown types default to ``feature``.
    """
    t = lark_type.strip().lower()
    if t in ("bug", "缺陷", "问题"):
        return "bug"
    if t in ("需求", "feature", "功能"):
        return "feature"
    if t in ("toolkit", "工具"):
        return "toolkit"
    if t in ("gap", "差距"):
        return "gap"
    return "feature"


# ── Status mapping (tracker → Lark) ──────────────────────────────────────────

#: Maps tracker ``status`` values to Lark ``状态`` column values.
#: ``"shipped"`` → ``"待验证"`` (the next workflow state the loop already uses
#: on ship).  Unmapped statuses are passed through as-is.
_STATUS_MAP: dict[str, str] = {
    "shipped": "待验证",
}


# ── Write-back function (tracker → Lark) ─────────────────────────────────────

def writeback_status(
    client: LarkClient,
    *,
    project: str | Path | None = None,
    key: str | None = None,
    status_map: dict[str, str] | None = None,
) -> int:
    """Push tracker statuses back to Lark for ``source="lark"`` entries.

    For each tracker entry whose ``source == "lark"`` and whose status
    differs from the Lark record's current ``状态``, calls the client's
    ``update_record`` with the mapped ``状态`` value.

    Returns the number of records updated.
    """
    if status_map is None:
        status_map = _STATUS_MAP

    entries = project_tracker.load(project=project, key=key)
    updated = 0

    for entry in entries:
        if entry.source != "lark":
            continue
        if not entry.source_id:
            continue

        record = client.get_record(entry.source_id)
        record_fields = (record.get("record") or record).get("fields") or {}
        current_lark_status = record_fields.get("状态") or ""

        mapped_status = status_map.get(entry.status, entry.status)

        if current_lark_status == mapped_status:
            continue

        client.update_record(entry.source_id, {"状态": mapped_status})
        updated += 1

    return updated
