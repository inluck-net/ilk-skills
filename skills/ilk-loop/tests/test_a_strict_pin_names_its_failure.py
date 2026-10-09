"""Pin that a red-first pin names its expected failure (backlog b212f859).

A bare @pytest.mark.xfail(strict=True) accepts ANY exception as the expected
failure. gh-resolve 09b (2026-10-09): the step-0 pin test_returns_vendored_output
JSON-parsed plain text and raised JSONDecodeError, step 0 counted that as "not
implemented yet", and step 1's pin-diff gate then forbade fixing the pin
(70 min stall, D-521).

AC-1: a step 0 that instructs xfail(strict=True, reason=...) without raises=
      gets a (non-HARD) plan_lint finding naming raises=.
AC-2 (control): a step 0 that instructs xfail(strict=True, raises=(...)) gets
      none, and the existing red-first exemption still applies to it.
AC-3: the sub-plan template and decomposition-principles both show raises=
      in the recommended pin form.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "skills" / "ilk-loop" / "scripts"))

PLAN = """---
plan: p
status: pending
current_step: 0
estimated_steps: 2
---

## Steps

### Step 0 — red-first pins

```yaml
local_checks:
  - command: "python3 -m pytest tests/test_p.py -q"
    timeout: 120
```

- Write tests/test_p.py. Mark every test `@pytest.mark.xfail({args})`.
- Commit: `test(p): pin it [plan:p#step-0]`

### Step 1 — implement

- Commit: `feat(p): do it [plan:p#step-1]`
"""


def test_ac1_a_bare_strict_pin_is_flagged():
    import plan_lint
    text = PLAN.format(args='strict=True, reason="not built"')
    found = plan_lint.lint_strict_pin_names_its_failure(text, "p")
    assert len(found) == 1
    assert "raises=" in found[0] and not found[0].startswith("HARD")


def test_ac2_a_pin_that_names_its_failure_passes():
    import plan_lint
    text = PLAN.format(args='strict=True, raises=(AssertionError, ImportError), reason="x"')
    assert plan_lint.lint_strict_pin_names_its_failure(text, "p") == []
    assert plan_lint.lint_redfirst_step0_per_step_gate_demands_green(text, "p") == []


def test_ac3_the_template_and_principles_show_raises():
    tpl = (REPO / "skills/ilk-loop/templates/subplan-template.md").read_text()
    dp = (REPO / "skills/ilk-loop/references/decomposition-principles.md").read_text()
    assert "xfail(strict=True, raises=" in tpl
    assert "xfail(strict=True, raises=" in dp
