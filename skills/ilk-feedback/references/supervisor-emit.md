# Supervisor emit contract (decision #12)

This document defines the **contract** for how supervisor findings enter the
ilk-skills improvement pipeline.

## Core rule

Supervisor output = **backlog entries**, NOT `scheduler-findings-*.md`.

The improvement backlog (`~/.ilk-data/ilk-skills-improvements/candidates.json`)
is the single source of truth for all upstream candidates. Supervisors emit
structured entries via `supervisor_emit.py`; the `/ilk-self-improve` adapter
consumes them through `build_task.load_open_candidates()`.

## Why backlog entries, not .md files?

1. **Machine-readable.** Entries have typed fields (title, gap, severity,
   relations) — no parsing prose.
2. **Dedup-safe.** Same finding twice → `seen_count` bumps, no duplicate work.
3. **Pipeline-integrated.** Entries flow directly to `/ilk-plan` for human
   approval. No manual scraping of markdown files.
4. **Cross-project.** One backlog serves all projects; `.md` files are
   project-scoped.

## Emit path

```python
from supervisor_emit import emit

entry = emit(
    title="Short description",
    gap="What's missing or broken",
    proposed_fix="How to fix it",       # optional
    severity="high",                    # high / medium / low
    leverage="medium",                  # high / medium / low
    kind="bug",                         # one of KINDS (default: bug)
    project="my-proj",                  # optional, stored in relations
    run_id="20260621-120000",           # optional, stored in relations
    source_id="ext-123",                # optional, for PULL-upsert dedup
)
```

CLI equivalent:

```bash
python supervisor_emit.py \
  --title "Short description" \
  --gap "What's missing or broken" \
  --severity high \
  --project my-proj
```

## Dedup semantics

- **Content-based** (default): same `(kind, title, gap)` → upsert.
- **Source-ID-based** (when `source_id` provided): same `(source, source_id)`
  → upsert, even if title/gap changed between syncs.

## Recurring judgments graduate to code

When a supervisor finding recurs across multiple runs (high `seen_count`), it
signals a structural gap that should be fixed in the toolkit itself — not
repeatedly emitted. The graduation path:

1. **Low frequency** (seen 1-2×): stays as a backlog entry.
2. **Medium frequency** (seen 3-5×): flag for human review — is this a real
   recurring issue or a noisy heuristic?
3. **High frequency** (seen 5×+): the finding should be **hardcoded** into the
   watchdog/scheduler/collect.py logic, not re-emitted by the supervisor.

This is future work. For now, supervisors emit freely and the backlog tracks
frequency.

## Relationship to existing code

| Component | Role |
|---|---|
| `supervisor_emit.py` | Thin CLI wrapper; calls `add_candidate(source="supervisor")` |
| `improvement_backlog.py` | Storage layer (atomic writes, dedup, schema) |
| `build_task.py` | Reads open entries for `/ilk-plan` |
| `collect.py` | Postmortem emitter (source="feedback"); same backlog |
| Scheduler / watchdog | Future: graduate high-frequency findings into code |

## See also

- `skills/ilk-feedback/scripts/supervisor_emit.py` — the emit helper
- `skills/ilk-feedback/scripts/improvement_backlog.py` — the storage layer
- `skills/ilk-self-improve/scripts/build_task.py` — the consumer
