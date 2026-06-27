"""Regression: run_local_checks.py must emit valid JSON even when a gate's
output contains non-ASCII (e.g. eslint/vitest '✓' U+2713) and stdout is a
narrow codec.

Field incident (gridlock, 2026-06-28): on a zh-CN console Python defaults
stdout to GBK (cp936). `main()` did `print(json.dumps(out, ensure_ascii=False))`
where `out["results"][*]["stdout_tail"]` carried a '✓' from the gate output.
The print died with UnicodeEncodeError → EMPTY stdout, no JSON → the runner
(`run_ilk_loop_claude.ps1` Invoke-LocalChecks) recorded
outcome=error/exit_code=null/raw=null → a FALSE `local_checks_failed`
(classified `local-checks-stuck`) even though the gate passed. Several
gridlock sub-plans false-stopped this way (full-taxonomy#6, ...), draining
only because the scheduler kept relaunching.

The fix reconfigures stdout/stderr to UTF-8 in main(). This test forces the
crash condition portably via PYTHONIOENCODING=ascii.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "run_local_checks.py"

SUBPLAN = """\
---
plan: uni-test
status: pending
current_step: 0
---

# Sub-plan: unicode stdout

## Steps

### Step 0 — emit a non-ASCII checkmark and pass
```yaml
local_checks:
  - command: printf '\\xe2\\x9c\\x93 ok\\n'
    timeout: 30
```
- Emits U+2713 then exits 0.
- Commit: `test(x): unicode [plan:uni-test#step-0]`
"""


def test_json_survives_nonascii_gate_output_on_narrow_stdout(tmp_path: Path) -> None:
    # Hermetic in-tree project: .git marks the single-repo root; the resolver
    # recognizes docs/plans as the plans dir only when it has a MASTER-*.md.
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    plans = proj / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "MASTER-2026-01-01-execution-plan.md").write_text(
        "---\nslug: uni\nstatus: active\n---\n# master\n", encoding="utf-8")
    (plans / "2026-01-01-uni.md").write_text(SUBPLAN, encoding="utf-8")

    env = dict(os.environ)
    # Force the crash condition on ANY OS: a stdout codec that can't encode U+2713.
    env["PYTHONIOENCODING"] = "ascii"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(proj),
         "--slug", "uni-test", "--step", "0"],
        capture_output=True, env=env,
    )

    # Must NOT crash to empty stdout. Decode tolerantly and parse the JSON.
    out = proc.stdout.decode("utf-8", "replace")
    assert out.strip(), (
        f"empty stdout (the UnicodeEncodeError crash). rc={proc.returncode} "
        f"stderr={proc.stderr.decode('utf-8', 'replace')[-400:]}"
    )
    data = json.loads(out)  # raises if the JSON was truncated/garbled
    assert data["all_passed"] is True
    assert proc.returncode == 0
