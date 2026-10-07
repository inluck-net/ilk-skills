"""Pin: a vitest fixture repo resolves a runner.

Part of sub-plan ``the-vitest-mention-test-has-a-vitest`` (step 0 of 2).

AC-1: ``_make_vitest_repo`` produces a repo where
      ``run_local_checks._resolve_vitest_runner`` resolves a non-empty
      runner.  At base the fixture lacks ``node_modules/.bin/vitest``,
      so the runner is ``None``; the marker is removed in step 1 when the
      stub is added.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Import the fixture from the existing test module.
from test_a_line_number_pin_is_gated import _make_vitest_repo

# Import the function under test.
import importlib.util, sys as _sys
_scripts = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location("run_local_checks", _scripts / "run_local_checks.py")
_rlc = importlib.util.module_from_spec(_spec)
_sys.modules["run_local_checks"] = _rlc
_spec.loader.exec_module(_rlc)


@pytest.mark.xfail(
    strict=True,
    reason="base 4b80aaa1: _make_vitest_repo does not create node_modules/.bin/vitest, so _resolve_vitest_runner returns None",
)
def test_vitest_fixture_resolves_runner(tmp_path: Path) -> None:
    """A vitest fixture repo resolves a runner (not None)."""
    repo = _make_vitest_repo(tmp_path)
    runner = _rlc._resolve_vitest_runner(repo)
    assert runner is not None, f"expected a runner, got {runner!r}"