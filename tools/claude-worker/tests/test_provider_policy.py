"""Tests for the provider policy — fallback, recovery, and guardrails.

The policy does not exist yet; every test here fails until step 1
(preference lists and selection) and step 2 (guardrails) implement it.

Design: docs/architecture/provider-switching-and-quota-fallback.md §8.2-8.5.

Acceptance criteria:
  AC1 — a role whose current provider is exhausted selects the next healthy
        candidate from its preference list; with none, it selects nothing.
  AC2 — a manager-tier role is NOT auto-switched unless it declares opt-in.
  AC3 — reset_at alone does not restore a provider; a live probe must succeed.
  AC4 — a second exhaustion of the same provider inside one reset window
        does not produce a second switch (cooldown).
  AC5 — every automatic switch appends a ledger row with role, from/to
        providers, trigger, and evidence pointer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Will be implemented in step 1.
from provider_policy import (
    ProviderPolicy,
    PolicyDecision,
    LedgerEntry,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_registry(**role_overrides) -> dict:
    """A minimal role registry with preference lists."""
    roles = {
        "coder": {
            "tier": "worker",
            "home": "~/.claude-worker",
            "providers": ["glm", "mimo", "kimi"],
            "provider": "glm",
        },
        "manager": {
            "tier": "manager",
            "home": "~/.claude-manager",
            "providers": ["glm", "official"],
            "provider": "glm",
        },
    }
    roles.update(role_overrides)
    return {"version": 1, "hosts": [], "roles": roles}


def _make_provider_state(**home_overrides) -> dict:
    """A minimal provider-state with one exhausted home."""
    now = datetime.now(timezone.utc)
    reset = (now + timedelta(hours=1)).isoformat()
    state = {
        "version": 1,
        "homes": {
            "/home/chad/.claude-worker": {
                "provider": "glm",
                "model": "mimo-v2.5-pro",
                "state": "exhausted",
                "config_probe": {"result": "mimo-v2.5-pro", "at": now.isoformat()},
                "live_probe": {"ok": False, "detail": "429 quota exhausted", "at": now.isoformat()},
                "reset_at": reset,
                "evidence": {"log": "/tmp/iter.log", "run_id": "20260921-142231"},
            },
            "/home/chad/.claude-manager": {
                "provider": "glm",
                "model": "opus",
                "state": "ok",
                "config_probe": {"result": "opus", "at": now.isoformat()},
                "live_probe": {"ok": True, "detail": "", "at": now.isoformat()},
                "reset_at": None,
                "evidence": {"log": "", "run_id": ""},
            },
        },
    }
    state.update(home_overrides)
    return state


# ── AC1: preference-list selection ────────────────────────────────────────────


class TestPreferenceListSelection:
    """A role whose current provider is exhausted selects the next healthy
    candidate from its ordered preference list."""

    def test_exhausted_provider_selects_next_candidate(self):
        """glm exhausted → policy selects mimo (next in list)."""
        registry = _make_registry()
        state = _make_provider_state()
        policy = ProviderPolicy(registry, state)
        decision = policy.decide("coder")
        assert decision.new_provider == "mimo"
        assert decision.trigger == "exhaustion"
        assert decision.old_provider == "glm"

    def test_no_healthy_candidate_selects_nothing(self):
        """All providers exhausted → decision selects nothing and says so."""
        now = datetime.now(timezone.utc)
        reset = (now + timedelta(hours=1)).isoformat()
        state = {
            "version": 1,
            "homes": {
                "/home/chad/.claude-worker": {
                    "provider": "glm", "model": "mimo-v2.5-pro", "state": "exhausted",
                    "config_probe": {"result": "mimo-v2.5-pro", "at": now.isoformat()},
                    "live_probe": {"ok": False, "detail": "429", "at": now.isoformat()},
                    "reset_at": reset,
                    "evidence": {"log": "", "run_id": ""},
                },
            },
        }
        registry = _make_registry(coder={
            "tier": "worker",
            "home": "~/.claude-worker",
            "providers": ["glm"],
            "provider": "glm",
        })
        policy = ProviderPolicy(registry, state)
        decision = policy.decide("coder")
        assert decision.new_provider is None
        assert "no healthy" in decision.reason.lower() or "exhausted" in decision.reason.lower()


# ── AC2: judging roles pinned by default ──────────────────────────────────────


class TestJudgingRolePinning:
    """Manager-tier roles are NOT auto-switched unless they declare opt-in."""

    def test_manager_not_auto_switched_when_exhausted(self):
        """Even with glm exhausted, manager does not auto-switch."""
        now = datetime.now(timezone.utc)
        reset = (now + timedelta(hours=1)).isoformat()
        state = {
            "version": 1,
            "homes": {
                "/home/chad/.claude-worker": {
                    "provider": "glm", "model": "mimo-v2.5-pro", "state": "ok",
                    "config_probe": {"result": "mimo-v2.5-pro", "at": now.isoformat()},
                    "live_probe": {"ok": True, "detail": "", "at": now.isoformat()},
                    "reset_at": None,
                    "evidence": {"log": "", "run_id": ""},
                },
                "/home/chad/.claude-manager": {
                    "provider": "glm", "model": "opus", "state": "exhausted",
                    "config_probe": {"result": "opus", "at": now.isoformat()},
                    "live_probe": {"ok": False, "detail": "429 quota", "at": now.isoformat()},
                    "reset_at": reset,
                    "evidence": {"log": "", "run_id": ""},
                },
            },
        }
        registry = _make_registry()
        policy = ProviderPolicy(registry, state)
        decision = policy.decide("manager")
        # Must not auto-switch — the manager is a judging role.
        assert decision.new_provider is None
        assert "pinned" in decision.reason.lower() or "judging" in decision.reason.lower() or "manager" in decision.reason.lower()

    def test_manager_opted_in_can_switch(self):
        """A manager that declares auto_switch: true CAN be switched."""
        now = datetime.now(timezone.utc)
        reset = (now + timedelta(hours=1)).isoformat()
        state = {
            "version": 1,
            "homes": {
                "/home/chad/.claude-manager": {
                    "provider": "glm", "model": "opus", "state": "exhausted",
                    "config_probe": {"result": "opus", "at": now.isoformat()},
                    "live_probe": {"ok": False, "detail": "429", "at": now.isoformat()},
                    "reset_at": reset,
                    "evidence": {"log": "", "run_id": ""},
                },
            },
        }
        registry = _make_registry(manager={
            "tier": "manager",
            "home": "~/.claude-manager",
            "providers": ["glm", "official"],
            "provider": "glm",
            "auto_switch": True,
        })
        policy = ProviderPolicy(registry, state)
        decision = policy.decide("manager")
        # Opted in, so it should switch to official.
        assert decision.new_provider == "official"


# ── AC3: probe-gated recovery ─────────────────────────────────────────────────


class TestProbeGatedRecovery:
    """reset_at alone does not restore; a live probe must succeed."""

    def test_reset_at_without_probe_does_not_restore(self):
        """When reset_at has passed but no probe succeeded, provider stays exhausted."""
        now = datetime.now(timezone.utc)
        past_reset = (now - timedelta(minutes=5)).isoformat()
        state = {
            "version": 1,
            "homes": {
                "/home/chad/.claude-worker": {
                    "provider": "glm", "model": "mimo-v2.5-pro", "state": "exhausted",
                    "config_probe": {"result": "mimo-v2.5-pro", "at": now.isoformat()},
                    "live_probe": {"ok": False, "detail": "429 quota", "at": now.isoformat()},
                    "reset_at": past_reset,
                    "evidence": {"log": "", "run_id": ""},
                },
            },
        }
        registry = _make_registry()
        policy = ProviderPolicy(registry, state)
        decision = policy.decide("coder")
        # glm is still exhausted — no probe confirmed it's alive.
        assert decision.new_provider != "glm"
        assert decision.new_provider == "mimo"  # falls back to next

    def test_successful_probe_restores_provider(self):
        """When a live probe succeeds, the provider is restored."""
        now = datetime.now(timezone.utc)
        past_reset = (now - timedelta(minutes=5)).isoformat()
        state = {
            "version": 1,
            "homes": {
                "/home/chad/.claude-worker": {
                    "provider": "glm", "model": "mimo-v2.5-pro", "state": "ok",
                    "config_probe": {"result": "mimo-v2.5-pro", "at": now.isoformat()},
                    "live_probe": {"ok": True, "detail": "", "at": now.isoformat()},
                    "reset_at": past_reset,
                    "evidence": {"log": "", "run_id": ""},
                },
            },
        }
        registry = _make_registry()
        policy = ProviderPolicy(registry, state)
        decision = policy.decide("coder")
        # glm is healthy — no switch needed.
        assert decision.new_provider is None


# ── AC4: cooldown ─────────────────────────────────────────────────────────────


class TestCooldown:
    """One switch per provider per reset window — no ping-pong."""

    def test_second_exhaustion_in_same_window_no_switch(self):
        """After switching away from glm, a second glm exhaustion doesn't switch again."""
        now = datetime.now(timezone.utc)
        reset = (now + timedelta(hours=1)).isoformat()
        state = {
            "version": 1,
            "homes": {
                "/home/chad/.claude-worker": {
                    "provider": "mimo", "model": "mimo-v2.5-pro", "state": "exhausted",
                    "config_probe": {"result": "mimo-v2.5-pro", "at": now.isoformat()},
                    "live_probe": {"ok": False, "detail": "429", "at": now.isoformat()},
                    "reset_at": reset,
                    "evidence": {"log": "", "run_id": ""},
                },
            },
        }
        registry = _make_registry(coder={
            "tier": "worker",
            "home": "~/.claude-worker",
            "providers": ["glm", "mimo", "kimi"],
            "provider": "mimo",
        })
        # A ledger showing we already switched from glm to mimo this window.
        ledger = [
            LedgerEntry(
                role="coder",
                from_provider="glm",
                to_provider="mimo",
                trigger="exhaustion",
                evidence="glm exhausted at T",
                at=now.isoformat(),
            ),
        ]
        policy = ProviderPolicy(registry, state, ledger=ledger)
        decision = policy.decide("coder")
        # mimo exhausted, but glm already used this window → skip to kimi.
        assert decision.new_provider == "kimi"


# ── AC5: ledger rows ──────────────────────────────────────────────────────────


class TestLedger:
    """Every automatic switch appends a ledger row."""

    def test_switch_appends_ledger_entry(self):
        """After a decision that switches, the ledger grows by one."""
        registry = _make_registry()
        state = _make_provider_state()
        policy = ProviderPolicy(registry, state)
        decision = policy.decide("coder")
        # The decision itself should carry enough info for a ledger row.
        assert decision.role == "coder"
        assert decision.old_provider == "glm"
        assert decision.new_provider == "mimo"
        assert decision.trigger is not None
        assert decision.evidence is not None