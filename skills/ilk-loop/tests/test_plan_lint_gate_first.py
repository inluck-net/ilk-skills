"""PLACEHOLDER — step 0 of `the-verification-template-declares-gate-first` replaces this file.

What step 0 must pin: a `gate_first: true` step with no local_checks is a hard plan_lint finding.

Scaffolded 2026-09-23 so that `plan_preflight.py`'s declared-test-path check
passes at release time. The preflight (added 2026-09-22, 32c7e9c) requires
every `unit_test_targets` entry and every pytest path named in a gate to exist
before the batch is released, while red-first authoring has step 0 create the
file — the two rules had not met until this batch.

**A gate that passes while `test_placeholder_replaced_by_step_0` is still
present is vacuous.** Step 0 must DELETE this placeholder, not add tests
beside it.
"""
from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="step 0 must replace this placeholder with the real red-first pin")
def test_placeholder_replaced_by_step_0() -> None:
    raise AssertionError("not yet written — see the sub-plan's step 0")
