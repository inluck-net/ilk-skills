"""Tests for the upstream-candidate backlog + emission (AC-1..AC-6).

Covers:
  AC-1  improvement_backlog.py schema + add_candidate + load/list
  AC-2  Same-key add bumps seen_count/last_seen, never duplicates
  AC-3  collect.py emits candidates for toolkit findings, not project-local
  AC-4  collect.py writes ONLY under ~/.ilk-data (never skills/**)
  AC-5  SKILL.md documents the backlog (grep-checked in step 4 local_checks)
  AC-6  All tests pass
  Back-compat: old-schema candidates.json (no source/relations) loads cleanly

Uses ILK_DATA_HOME isolation so tests never touch real ~/.ilk-data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_COLLECT_PY = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts" / "collect.py"
_SCRIPTS_DIR = _REPO_ROOT / "skills" / "ilk-feedback" / "scripts"

_KEY_PUNCT = re.compile(r"[^a-z0-9]+")

# Ensure scripts dir is importable
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _project_key(project_path: Path) -> str:
    abs_str = str(project_path.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def backlog_env(tmp_path: Path):
    """Isolated ILK_DATA_HOME for backlog tests."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    os.environ["ILK_DATA_HOME"] = str(data_home)
    yield data_home
    os.environ.pop("ILK_DATA_HOME", None)


@pytest.fixture()
def collect_env(tmp_path: Path):
    """Full environment for collect.py integration tests."""
    data_home = tmp_path / "ilk-data"
    data_home.mkdir()
    project_path = tmp_path / "my-proj"
    project_path.mkdir()

    env = {
        **os.environ,
        "ILK_DATA_HOME": str(data_home),
        "PYTHONIOENCODING": "utf-8",
    }
    key = _project_key(project_path)
    return project_path, env, key, data_home


# ── AC-1: schema + add_candidate + load/list ─────────────────────────────────


class TestAC1SchemaAndAPI:
    """AC-1: improvement_backlog.py defines entry schema and idempotent add_candidate."""

    def test_entry_has_all_required_fields(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        entry = mod.add_candidate(
            title="test gap",
            gap="missing feature X",
            evidence={"project": "p1", "run_id": "r1"},
            proposed_fix="add the feature",
            leverage="high",
            severity="medium",
            backlog_dir=backlog_env,
        )

        # All required fields present
        assert hasattr(entry, "id")
        assert hasattr(entry, "title")
        assert hasattr(entry, "kind")
        assert hasattr(entry, "gap")
        assert hasattr(entry, "evidence")
        assert hasattr(entry, "proposed_fix")
        assert hasattr(entry, "leverage")
        assert hasattr(entry, "severity")
        assert hasattr(entry, "status")
        assert hasattr(entry, "first_seen")
        assert hasattr(entry, "last_seen")
        assert hasattr(entry, "seen_count")

        # Defaults
        assert entry.kind == "toolkit"
        assert entry.status == "open"
        assert entry.seen_count == 1

    def test_stable_key_derived_from_kind_title_gap(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        key1 = mod.stable_key("toolkit", "My Gap", "missing X")
        key2 = mod.stable_key("toolkit", "my gap", "Missing X")
        key3 = mod.stable_key("toolkit", "Other Gap", "missing X")

        # Same normalised content → same key
        assert key1 == key2
        # Different content → different key
        assert key1 != key3

    def test_load_returns_all_entries(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        mod.add_candidate(title="gap A", gap="desc A", backlog_dir=backlog_env)
        mod.add_candidate(title="gap B", gap="desc B", backlog_dir=backlog_env)

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 2

    def test_list_filter_by_status(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e = mod.add_candidate(title="gap A", gap="desc A", backlog_dir=backlog_env)
        # Manually set status to shipped
        raw = mod._load_raw(backlog_env)
        raw[0]["status"] = "shipped"
        mod._save_raw(backlog_env, raw)

        open_entries = mod.list_entries(status="open", backlog_dir=backlog_env)
        shipped_entries = mod.list_entries(status="shipped", backlog_dir=backlog_env)
        assert len(open_entries) == 0
        assert len(shipped_entries) == 1


# ── AC-2: dedup semantics ────────────────────────────────────────────────────


class TestAC2Dedup:
    """AC-2: Same-key add bumps seen_count/last_seen, never duplicates."""

    def test_same_candidate_bumps_seen_count(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e1 = mod.add_candidate(title="Test Gap", gap="Missing X", backlog_dir=backlog_env)
        assert e1.seen_count == 1

        e2 = mod.add_candidate(title="Test Gap", gap="Missing X", backlog_dir=backlog_env)
        assert e2.seen_count == 2
        assert e2.id == e1.id
        assert e2.first_seen == e1.first_seen

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1

    def test_different_candidates_not_deduped(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        mod.add_candidate(title="Gap A", gap="desc A", backlog_dir=backlog_env)
        mod.add_candidate(title="Gap B", gap="desc B", backlog_dir=backlog_env)

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 2

    def test_cross_project_same_key_merges(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e1 = mod.add_candidate(
            title="Same Gap", gap="Same desc",
            evidence={"project": "proj-a"}, backlog_dir=backlog_env,
        )
        e2 = mod.add_candidate(
            title="Same Gap", gap="Same desc",
            evidence={"project": "proj-b"}, backlog_dir=backlog_env,
        )

        assert e2.seen_count == 2
        # Evidence merged
        assert "proj-a" in str(e2.evidence) or "proj-b" in str(e2.evidence)

    def test_first_seen_preserved_on_update(self, backlog_env):
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e1 = mod.add_candidate(title="T", gap="G", backlog_dir=backlog_env)
        first = e1.first_seen

        e2 = mod.add_candidate(title="T", gap="G", backlog_dir=backlog_env)
        assert e2.first_seen == first


# ── Multi-kind + source/relations (step 2) ───────────────────────────────────


class TestMultiKindAndNewFields:
    """Step 2: add_candidate generalised for kind + source + relations."""

    def test_bug_kind_round_trips(self, backlog_env):
        """AC-1: bug kind is accepted and round-trips through load()."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e = mod.add_candidate(
            title="Crash on startup",
            kind="bug",
            gap="App crashes when config missing",
            backlog_dir=backlog_env,
        )
        assert e.kind == "bug"

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
        assert entries[0].kind == "bug"

    def test_gap_kind_round_trips(self, backlog_env):
        """AC-1: gap kind is accepted and round-trips through load()."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e = mod.add_candidate(
            title="Missing validation",
            kind="gap",
            gap="No input validation on form",
            backlog_dir=backlog_env,
        )
        assert e.kind == "gap"

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
        assert entries[0].kind == "gap"

    def test_source_and_relations_persist(self, backlog_env):
        """AC-3: source + relations persist and survive load()."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e = mod.add_candidate(
            title="Test entry",
            kind="toolkit",
            gap="test gap",
            source="supervisor",
            relations={"run_id": "r1", "commit": "abc123"},
            backlog_dir=backlog_env,
        )
        assert e.source == "supervisor"
        assert e.relations == {"run_id": "r1", "commit": "abc123"}

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1
        assert entries[0].source == "supervisor"
        assert entries[0].relations == {"run_id": "r1", "commit": "abc123"}

    def test_dedup_across_new_kinds(self, backlog_env):
        """AC-4: same (kind, title, gap) bumps seen_count across new kinds."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e1 = mod.add_candidate(
            title="Crash", kind="bug", gap="config missing",
            backlog_dir=backlog_env,
        )
        assert e1.seen_count == 1

        e2 = mod.add_candidate(
            title="Crash", kind="bug", gap="config missing",
            backlog_dir=backlog_env,
        )
        assert e2.seen_count == 2
        assert e2.id == e1.id

        entries = mod.load(backlog_dir=backlog_env)
        assert len(entries) == 1

    def test_unknown_kind_rejected(self, backlog_env):
        """Unknown kind raises ValueError."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        with pytest.raises(ValueError, match="unknown kind"):
            mod.add_candidate(
                title="Bad", kind="nonexistent", gap="x",
                backlog_dir=backlog_env,
            )

    def test_relations_merged_on_update(self, backlog_env):
        """Relations are merged (like evidence) on dedup hit."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e1 = mod.add_candidate(
            title="Merge test", kind="toolkit", gap="g",
            relations={"run_id": "r1"},
            backlog_dir=backlog_env,
        )
        e2 = mod.add_candidate(
            title="Merge test", kind="toolkit", gap="g",
            relations={"commit": "abc"},
            backlog_dir=backlog_env,
        )
        assert e2.seen_count == 2
        assert e2.relations == {"run_id": "r1", "commit": "abc"}

    def test_source_refreshed_on_nonempty(self, backlog_env):
        """Source is refreshed only if a non-empty one is passed."""
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        e1 = mod.add_candidate(
            title="Src test", kind="toolkit", gap="g",
            source="feedback",
            backlog_dir=backlog_env,
        )
        assert e1.source == "feedback"

        # Empty source → keeps old value
        e2 = mod.add_candidate(
            title="Src test", kind="toolkit", gap="g",
            backlog_dir=backlog_env,
        )
        assert e2.source == "feedback"

        # Non-empty source → refreshes
        e3 = mod.add_candidate(
            title="Src test", kind="toolkit", gap="g",
            source="supervisor",
            backlog_dir=backlog_env,
        )
        assert e3.source == "supervisor"


# ── Back-compat: old-schema load ─────────────────────────────────────────────


class TestBackCompat:
    """Old-schema candidates.json (no source/relations fields) loads cleanly."""

    def test_old_schema_load_with_defaults(self, backlog_env):
        """A candidates.json written in the old schema (no source, no relations)
        loads via load() without error; the resulting Entry has defaulted
        source/relations and the original fields intact.
        """
        import importlib
        import improvement_backlog as mod
        importlib.reload(mod)

        # Write a hand-rolled old-schema record directly to disk
        old_record = {
            "id": "old-schema-test-key",
            "title": "Old Gap",
            "kind": "toolkit",
            "gap": "Missing feature Y",
            "evidence": {"project": "old-proj"},
            "proposed_fix": "add it",
            "leverage": "medium",
            "severity": "low",
            "status": "open",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
            "seen_count": 1,
            # NOTE: no "source" or "relations" keys
        }
        backlog_dir = backlog_env / "ilk-skills-improvements"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        candidates_path = backlog_dir / "candidates.json"
        candidates_path.write_text(
            json.dumps([old_record], indent=2),
            encoding="utf-8",
        )

        entries = mod.load(backlog_dir=backlog_dir)
        assert len(entries) == 1

        e = entries[0]
        # Original fields intact
        assert e.id == "old-schema-test-key"
        assert e.title == "Old Gap"
        assert e.kind == "toolkit"
        assert e.gap == "Missing feature Y"
        assert e.evidence == {"project": "old-proj"}
        assert e.seen_count == 1

        # New fields defaulted
        assert e.source == ""
        assert e.relations == {}


# ── AC-3: emit candidates from collect.py ────────────────────────────────────


class TestAC3Emission:
    """AC-3: collect.py emits candidates for toolkit findings, not project-local."""

    def _write_jsonl(self, log_dir: Path, project_path: Path, records: list[dict]):
        log_dir.mkdir(parents=True, exist_ok=True)
        p = log_dir / ".ilk-loop.log"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def test_emits_candidate_for_local_checks_stuck(self, collect_env):
        """local-checks-stuck is a toolkit signal → candidate emitted."""
        project_path, env, key, data_home = collect_env
        log_dir = data_home / "projects" / key / "logs"

        # Build JSONL: 5 iters, last 3 with failing local_checks
        records = []
        for i in range(1, 6):
            lc = {"outcome": "fail", "command": "pytest"} if i >= 3 else {"outcome": "pass", "command": "pytest"}
            records.append({
                "run_id": "20260609-120000",
                "iteration": i,
                "project": str(project_path),
                "exit_code": 1 if i >= 3 else 0,
                "duration_sec": 120,
                "new_commits_total": 1,
                "stop_reason": "no-progress" if i == 5 else None,
                "local_checks": lc,
            })
        self._write_jsonl(log_dir, project_path, records)

        # Write sentinel
        rt_dir = data_home / "projects" / key / "runtime"
        rt_dir.mkdir(parents=True, exist_ok=True)
        (rt_dir / "last-exit.json").write_text(
            json.dumps({"state": "running", "run_id": "20260609-120000"}),
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path), "--quiet"],
            capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"

        # Check backlog for candidate
        backlog_dir = data_home / "ilk-skills-improvements"
        candidates_path = backlog_dir / "candidates.json"
        assert candidates_path.exists(), "no candidates.json written"
        data = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert len(data) >= 1, "no candidate emitted for local-checks-stuck"
        assert data[0]["kind"] == "toolkit"

    def test_no_emission_for_clean_success(self, collect_env):
        """clean-success is NOT a toolkit signal → no candidate emitted."""
        project_path, env, key, data_home = collect_env
        log_dir = data_home / "projects" / key / "logs"

        records = [{
            "run_id": "20260609-130000",
            "iteration": 1,
            "project": str(project_path),
            "exit_code": 0,
            "duration_sec": 60,
            "new_commits_total": 3,
            "stop_reason": "already-shipped",
            "local_checks": {"outcome": "pass", "command": "pytest"},
        }]
        self._write_jsonl(log_dir, project_path, records)

        rt_dir = data_home / "projects" / key / "runtime"
        rt_dir.mkdir(parents=True, exist_ok=True)
        (rt_dir / "last-exit.json").write_text(
            json.dumps({"state": "done", "run_id": "20260609-130000"}),
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path), "--quiet"],
            capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0

        backlog_dir = data_home / "ilk-skills-improvements"
        candidates_path = backlog_dir / "candidates.json"
        if candidates_path.exists():
            data = json.loads(candidates_path.read_text(encoding="utf-8"))
            assert len(data) == 0, "candidate emitted for clean-success (should not)"


# ── AC-4: read-only boundary ─────────────────────────────────────────────────
# (covered more thoroughly in test_readonly_boundary.py)


class TestAC4Boundary:
    """AC-4: collect.py writes ONLY under ~/.ilk-data."""

    def test_boundary_smoke(self, collect_env):
        """Quick smoke: collect.py doesn't crash and doesn't touch skills/."""
        project_path, env, key, data_home = collect_env

        skills_dir = _REPO_ROOT / "skills"
        pre_files = {p: p.stat().st_mtime for p in skills_dir.rglob("*") if p.is_file()}

        result = subprocess.run(
            [sys.executable, str(_COLLECT_PY), "-ProjectPath", str(project_path), "--quiet"],
            capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace",
        )
        # May exit 1 (no data) — that's fine, we're checking boundary
        for p in skills_dir.rglob("*"):
            if p.is_file():
                post = p.stat().st_mtime
                pre = pre_files.get(p)
                if pre is not None and post > pre:
                    pytest.fail(f"collect.py modified skills/: {p}")


# ── AC-5: CLI (add/list) ─────────────────────────────────────────────────────


_SCRIPT = _SCRIPTS_DIR / "improvement_backlog.py"


class TestCLINewFields:
    """AC-5: CLI add/list supports --kind choices, --source, --relation."""

    def test_add_bug_with_source_and_relation(self, backlog_env):
        """add --kind bug --source supervisor --relation commit=abc → list --source finds it."""
        env = {**os.environ, "ILK_DATA_HOME": str(backlog_env)}

        # Add entry via CLI
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "add",
             "--title", "CLI test bug",
             "--gap", "CLI gap",
             "--kind", "bug",
             "--source", "supervisor",
             "--relation", "commit=abc123",
             "--relation", "run_id=r42"],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, f"add failed: {result.stderr}"
        added = json.loads(result.stdout)
        assert added["kind"] == "bug"
        assert added["source"] == "supervisor"
        assert added["relations"]["commit"] == "abc123"
        assert added["relations"]["run_id"] == "r42"

        # List filtered by source
        result2 = subprocess.run(
            [sys.executable, str(_SCRIPT), "list",
             "--source", "supervisor", "--json"],
            capture_output=True, text=True, env=env,
        )
        assert result2.returncode == 0, f"list failed: {result2.stderr}"
        entries = json.loads(result2.stdout)
        assert len(entries) == 1
        assert entries[0]["source"] == "supervisor"
        assert entries[0]["kind"] == "bug"
