"""Provider policy — fallback, recovery, and guardrails.

Design: docs/architecture/provider-switching-and-quota-fallback.md §8.2-8.5.

The policy decides when a role's provider should be switched automatically,
based on:
  - An ordered preference list per role (from the registry)
  - Observed provider state (from provider-state.json)
  - Guardrails: judging-role pinning, cooldown, probe-gated recovery
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class LedgerEntry:
    """One row in the automatic-switch ledger."""
    role: str
    from_provider: str
    to_provider: str
    trigger: str
    evidence: str
    at: str  # ISO-8601 with offset


@dataclass
class PolicyDecision:
    """The outcome of a decide() call."""
    role: str
    old_provider: Optional[str]
    new_provider: Optional[str]
    trigger: Optional[str]
    evidence: Optional[str]
    reason: str  # human-readable explanation


class ProviderPolicy:
    """Decides provider switches based on preference lists and observed state.

    Args:
        registry: The role registry dict (version 1, with roles.providers lists).
        state: The provider-state dict (version 1, with homes entries).
        ledger: Existing ledger entries (for cooldown checks).
    """

    def __init__(
        self,
        registry: Dict[str, Any],
        state: Dict[str, Any],
        ledger: Optional[List[LedgerEntry]] = None,
    ) -> None:
        self._registry = registry
        self._state = state
        self._ledger = ledger or []

    def decide(self, role: str) -> PolicyDecision:
        """Decide whether *role* should switch providers.

        Returns a PolicyDecision.  If new_provider is None, no switch is needed.
        """
        roles = self._registry.get("roles", {})
        role_cfg = roles.get(role)
        if role_cfg is None:
            return PolicyDecision(
                role=role,
                old_provider=None,
                new_provider=None,
                trigger=None,
                evidence=None,
                reason=f"role {role!r} not in registry",
            )

        current_provider = role_cfg.get("provider")
        preference_list: List[str] = role_cfg.get("providers", [])
        tier = role_cfg.get("tier", "worker")
        auto_switch = role_cfg.get("auto_switch")

        # Guardrail: judging roles (manager tier) are pinned by default.
        if tier == "manager" and not auto_switch:
            return PolicyDecision(
                role=role,
                old_provider=current_provider,
                new_provider=None,
                trigger=None,
                evidence=None,
                reason="judging role is pinned; set auto_switch=true to opt in",
            )

        # Find the home for this role.
        home = self._home_for_role(role_cfg)
        home_state = self._state.get("homes", {}).get(home, {})

        # Is the current provider healthy?
        if self._provider_is_healthy(current_provider, home_state):
            return PolicyDecision(
                role=role,
                old_provider=current_provider,
                new_provider=None,
                trigger=None,
                evidence=None,
                reason="current provider is healthy",
            )

        # Current provider is exhausted — find the next healthy candidate.
        # Collect providers already switched away from in this reset window
        # (cooldown: skip them).
        cooldown_set = self._cooldown_providers(role)

        for candidate in preference_list:
            if candidate == current_provider:
                continue
            if candidate in cooldown_set:
                continue
            # Check candidate health.  If the candidate has no state entry
            # at all, it is considered healthy (never observed exhausted).
            candidate_state = self._candidate_home_state(candidate)
            if self._provider_is_healthy(candidate, candidate_state):
                evidence = self._evidence_pointer(home_state)
                return PolicyDecision(
                    role=role,
                    old_provider=current_provider,
                    new_provider=candidate,
                    trigger="exhaustion",
                    evidence=evidence,
                    reason=f"{current_provider} exhausted; switching to {candidate}",
                )

        # No healthy candidate.
        return PolicyDecision(
            role=role,
            old_provider=current_provider,
            new_provider=None,
            trigger="exhaustion",
            evidence=self._evidence_pointer(home_state),
            reason=f"no healthy candidate; {current_provider} exhausted and all alternatives unavailable",
        )

    # ── helpers ────────────────────────────────────────────────────────────────

    def _home_for_role(self, role_cfg: Dict[str, Any]) -> str:
        """Resolve the home path for a role (expand ~ to /home/chad for matching)."""
        home = role_cfg.get("home", "")
        # The state file uses /home/chad/... while the registry uses ~/...
        if home.startswith("~/"):
            return home.replace("~", "/home/chad", 1)
        return home

    def _provider_is_healthy(
        self, provider: str, home_state: Dict[str, Any]
    ) -> bool:
        """A provider is healthy if:
        - It has no state entry (never observed), OR
        - Its state is 'ok' AND live_probe.ok is True.
        """
        if not home_state:
            return True  # never observed → assume healthy
        state_val = home_state.get("state", "unknown")
        live_probe = home_state.get("live_probe", {})
        live_ok = live_probe.get("ok", False)
        return state_val == "ok" and live_ok

    def _candidate_home_state(self, candidate_provider: str) -> Dict[str, Any]:
        """Find the home-state dict for a candidate provider.

        Looks through all homes to find one whose provider matches.
        Returns empty dict if no match.
        """
        for _home, hstate in self._state.get("homes", {}).items():
            if hstate.get("provider") == candidate_provider:
                return hstate
        return {}

    def _cooldown_providers(self, role: str) -> set:
        """Providers already switched away from for this role in the current window."""
        return {e.from_provider for e in self._ledger if e.role == role}

    def _evidence_pointer(self, home_state: Dict[str, Any]) -> str:
        """Build an evidence string from the home state."""
        evidence = home_state.get("evidence", {})
        log = evidence.get("log", "")
        run_id = evidence.get("run_id", "")
        if log or run_id:
            return f"log={log} run_id={run_id}"
        live_probe = home_state.get("live_probe", {})
        detail = live_probe.get("detail", "")
        return detail or "no evidence"