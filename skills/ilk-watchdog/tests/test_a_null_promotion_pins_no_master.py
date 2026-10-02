"""A promotion of null pins no master.

promote_next_master.py reports ``"promoted": null`` when nothing was promoted
(e.g. a merge-pending dispatch). scheduler.sh parsed it with
``print(d.get('promoted',''))``, which prints the STRING "None" for null. So
dispatchmaster became "None", the runner was launched with --master 'None',
pinned to a master that doesn't exist, and ended blocked-no-runnable
("No runnable master: held"). Seen: ilk-skills run 20261002-135739, after the
scheduler logged "promote: … -> None".

This test runs each promoted-name snippet exactly as it appears in
scheduler.sh.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_SCHED = Path(__file__).resolve().parent.parent / "scripts" / "scheduler.sh"


def _snippets() -> list[str]:
    text = _SCHED.read_text(encoding="utf-8")
    found = re.findall(r'promoted_name=\$\(\$PYTHON -c "([^"]+)"', text)
    assert found, "no promoted_name parse found in scheduler.sh"
    return found


def _run(snippet: str, payload: str) -> str:
    return subprocess.run([sys.executable, "-c", snippet], input=payload,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_null_promotion_yields_empty_name():
    for s in _snippets():
        assert _run(s, '{"promoted": null, "demoted": null}') == "", s


def test_missing_promotion_yields_empty_name():
    for s in _snippets():
        assert _run(s, '{}') == "", s


def test_a_real_promotion_yields_its_name():
    for s in _snippets():
        assert _run(s, '{"promoted": "MASTER-x.md"}') == "MASTER-x.md", s
