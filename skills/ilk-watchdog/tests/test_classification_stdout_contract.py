"""A failed classification cannot become the classification.

Sub-plan `2026-08-29c-a-failed-classification-cannot-be-the-classification`.

Field record (rezmac, 2026-08-29). Three relaunches at 12:01 / 12:37 / 13:12,
no plan progress, nothing declining to relaunch. The watchdog's own log:

    [13:12:06] sentinel terminal state: timeout (iters=1) - classifying.
    [13:12:06] running collect.py to classify the run...
    [13:12:07] collect.py produced no valid report path: ''
    [13:12:07] classification: [13:12:07] collect.py produced no valid report path: ''

The last line is the tell: the error string became the classification,
`write_log`'s timestamp and all.

Mechanism, read at HEAD:

  * ``watchdog.sh:575-584``  -- ``write_log()`` ends with ``echo "$line"``,
    i.e. **stdout**.
  * ``watchdog.sh:929``      -- ``classification=$(invoke_postmortem_collect ...)``
    captures that stdout.
  * ``watchdog.sh:396-414``  -- the two failure paths call ``write_log`` and
    *then* ``echo ""``, so the captured value is the log line plus a blank
    line: **non-empty**.

The ``-n`` guard at ``:930`` therefore passes and the fallback at ``:933-934``
-- which would have yielded the raw sentinel state -- is unreachable by
construction. The logic was written correctly and defeated by the logging.

These tests drive the *real* functions, extracted from ``watchdog.sh`` with
``sed`` and ``eval``, the same way ``test_watchdog_empty_classification.sh``
does. Nothing here reimplements the shell.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_WATCHDOG_SH = _REPO_ROOT / "skills" / "ilk-watchdog" / "scripts" / "watchdog.sh"

# Functions the collect wrapper depends on, in dependency order.  Extracted
# rather than sourced: sourcing watchdog.sh runs its CLI arg parsing.
_HARNESS_PRELUDE = r"""
set -uo pipefail
WATCHDOG_SH="{watchdog}"
export ACTIVITY_LOG="{activity_log}"
export PYTHON="{python}"
export COLLECT_PY="{collect_py}"

# _py_path is a one-liner, not a brace block -- extract by line, not by range.
eval "$(sed -n '/^_py_path()/p' "$WATCHDOG_SH")"
eval "$(sed -n '/^write_log()/,/^}}/p' "$WATCHDOG_SH")"
eval "$(sed -n '/^invoke_postmortem_collect()/,/^}}/p' "$WATCHDOG_SH")"
"""


def _run_harness(body: str, *, tmp_path: Path, collect_py: str) -> subprocess.CompletedProcess:
    """Run a bash snippet with the real collect-wrapper functions in scope."""
    activity_log = tmp_path / "activity.log"
    script = _HARNESS_PRELUDE.format(
        watchdog=_WATCHDOG_SH,
        activity_log=activity_log,
        python=sys.executable,
        collect_py=collect_py,
    ) + body
    proc = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    proc.activity_log = activity_log.read_text() if activity_log.exists() else ""  # type: ignore[attr-defined]
    return proc


def _capture_body(project: str = "/tmp/does-not-matter", run_id: str = "run-1") -> str:
    """Model watchdog.sh:929 exactly -- capture the function's stdout."""
    return (
        f'out=$(invoke_postmortem_collect "{project}" "{run_id}")\n'
        "printf '%s' \"$out\"\n"
    )


# --------------------------------------------------------------------------
# Failure-mode fixtures: the three ways invoke_postmortem_collect can fail.
# --------------------------------------------------------------------------

def _mode_missing_collect(tmp_path: Path) -> str:
    """COLLECT_PY does not exist -> watchdog.sh:400-403."""
    return str(tmp_path / "definitely-absent-collect.py")


def _mode_no_report_path(tmp_path: Path) -> str:
    """collect.py runs but emits no path -> watchdog.sh:410-414."""
    stub = tmp_path / "collect_silent.py"
    stub.write_text("import sys\nsys.exit(1)\n")
    return str(stub)


def _mode_unparseable_frontmatter(tmp_path: Path) -> str:
    """collect.py emits a real file with no `classification:` -> :419-436."""
    report = tmp_path / "report.md"
    report.write_text("no frontmatter here at all\njust prose\n")
    stub = tmp_path / "collect_bad_fm.py"
    stub.write_text(f"print({str(report)!r})\n")
    return str(stub)


_ALL_MODES = (
    ("missing-collect.py", _mode_missing_collect),
    ("no-report-path", _mode_no_report_path),
    ("unparseable-frontmatter", _mode_unparseable_frontmatter),
)


# --------------------------------------------------------------------------
# AC-1
# --------------------------------------------------------------------------

def test_collect_wrapper_stdout_is_empty_on_every_failure_path(tmp_path: Path) -> None:
    """AC-1: the return channel carries the result or nothing -- never a log line.

    All three failure modes asserted in one test on purpose: the sub-plan's
    step-0 gate declares exactly four failing tests, and these three are one
    acceptance criterion, not three.
    """
    offenders = []
    for name, make in _ALL_MODES:
        mode_dir = tmp_path / name
        mode_dir.mkdir()
        proc = _run_harness(
            _capture_body(), tmp_path=mode_dir, collect_py=make(mode_dir)
        )
        if proc.stdout != "":
            offenders.append(f"  [{name}] stdout={proc.stdout!r}")

    assert not offenders, (
        "invoke_postmortem_collect leaked non-result bytes onto stdout, which "
        "watchdog.sh:929 captures as the classification:\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------
# AC-2
# --------------------------------------------------------------------------

def test_collect_wrapper_diagnostics_survive_off_the_return_channel(
    tmp_path: Path,
) -> None:
    """AC-2: moving the message off stdout must not lose it.

    The observed failure was found *only* by reading activity.log, so the
    diagnostic has to keep reaching both the log file and the operator's
    console.  Console now means stderr, since stdout is the return channel.
    """
    mode_dir = tmp_path / "no-report-path"
    mode_dir.mkdir()
    proc = _run_harness(
        _capture_body(), tmp_path=mode_dir, collect_py=_mode_no_report_path(mode_dir)
    )

    needle = "collect.py produced no valid report path"

    assert needle in proc.activity_log, (  # type: ignore[attr-defined]
        "diagnostic vanished from ACTIVITY_LOG; the 2026-08-29 failure was "
        f"diagnosable only because it was there. log={proc.activity_log!r}"  # type: ignore[attr-defined]
    )
    assert needle in proc.stderr, (
        "diagnostic did not reach the console on stderr. It must not ride "
        f"stdout -- that is what became the classification. stderr={proc.stderr!r}"
    )
    assert proc.stdout == "", (
        f"stdout must stay clean while diagnostics flow; got {proc.stdout!r}"
    )


# --------------------------------------------------------------------------
# AC-3
# --------------------------------------------------------------------------

def _extract_classification_block() -> str:
    """Lift watchdog.sh's real classification guard out of run_watchdog_loop.

    Driving the actual source lines, not a paraphrase: a paraphrased guard
    would keep passing after somebody edits the real one.
    """
    lines = _WATCHDOG_SH.read_text().splitlines()
    start = next(
        i
        for i, ln in enumerate(lines)
        if "classification=$(invoke_postmortem_collect" in ln
    )
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
    return "\n".join(ln.strip() for ln in lines[start : end + 1])


def test_a_failed_collect_falls_back_to_the_raw_sentinel_state(
    tmp_path: Path,
) -> None:
    """AC-3: the :933-934 fallback is reachable and yields the sentinel state."""
    block = _extract_classification_block()
    assert 'classification="$sentinel_state"' in block, (
        "structural pin: the raw-state fallback disappeared from watchdog.sh"
    )

    mode_dir = tmp_path / "no-report-path"
    mode_dir.mkdir()
    body = (
        'project="/tmp/does-not-matter"\n'
        'sentinel_run_id="run-1"\n'
        'sentinel_state="timeout"\n'
        "classification=\n"
        f"{block}\n"
        "printf 'CLASSIFICATION=%s' \"$classification\"\n"
    )
    proc = _run_harness(
        body, tmp_path=mode_dir, collect_py=_mode_no_report_path(mode_dir)
    )

    marker = "CLASSIFICATION="
    assert marker in proc.stdout, f"harness produced no marker: {proc.stdout!r}"
    got = proc.stdout.rsplit(marker, 1)[1]

    assert got == "timeout", (
        "the fallback did not fire: collect.py produced no report, so the "
        "classification must be the raw sentinel state 'timeout'. Got "
        f"{got!r} -- if that contains a '[' timestamp it is write_log's "
        "signature and the return channel is still polluted."
    )


# --------------------------------------------------------------------------
# AC-4
# --------------------------------------------------------------------------

def _classify_action_case_labels() -> set[str]:
    """Every label explicitly enumerated in classify_action's case arms."""
    lines = _WATCHDOG_SH.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("classify_action()"))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "esac")

    labels: set[str] = set()
    for ln in lines[start : end + 1]:
        stripped = ln.strip()
        if not stripped.endswith(")") or stripped.startswith("#"):
            continue
        if stripped in ("classify_action() {", "*)"):
            continue
        pattern = stripped[:-1]
        if pattern.startswith('case ') or pattern == "*":
            continue
        for token in pattern.split("|"):
            token = token.strip().strip('"').strip("'")
            if token and token != "*":
                labels.add(token)
    return labels


def _classify_action(label: str) -> str:
    script = (
        f'eval "$(sed -n \'/^classify_action()/,/^}}/p\' "{_WATCHDOG_SH}")"\n'
        f'classify_action "{label}"\n'
    )
    proc = subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, timeout=30
    )
    return proc.stdout.strip()


def test_timeout_is_an_enumerated_label_and_unknown_still_blocks() -> None:
    """AC-4: this change must not silently alter relaunch policy.

    Two halves, and only the first is red today.

    The sub-plan drafted this as "classify_action 'timeout' maps to relaunch
    per :329".  Measured at HEAD, that is wrong: :329 enumerates
    ``timeout-bound``, while the *raw sentinel state* the fallback yields is
    ``timeout`` (run_ilk_loop_claude.sh:2184 sets ``iter_stop_reason=timeout``,
    :2504 promotes it to the sentinel's terminal state).  ``timeout`` matches
    no arm, so it reaches the ``*`` fail-safe.

    ``block`` is arguably the right *action* for a run that left nothing to
    classify -- but it is reached by the wrong *route*.  Nothing downstream can
    tell "we deliberately block bare timeouts" from "we have never heard of
    this label", and the master's success criterion requires the fallback to
    reach **a real taxonomy label**.  ``timeout`` is not one: it appears in
    neither classify_action's arms nor collect.py's _SENTINEL_FAILURE_MAP nor
    the state vocabulary in detached-component-contracts.md.
    """
    labels = _classify_action_case_labels()
    assert "timeout-bound" in labels, (
        f"harness sanity: parsed arms look wrong, got {sorted(labels)}"
    )

    # Half 2 first -- the fail-safe must stay intact whatever else changes.
    assert _classify_action("some-label-nobody-has-ever-emitted") == "block", (
        "the * fail-safe stopped blocking unknown labels"
    )
    assert _classify_action("") == "block", "the empty-string case stopped blocking"

    # Half 1 -- red at HEAD.
    assert "timeout" in labels, (
        "the raw sentinel state 'timeout' is not an enumerated label in "
        "classify_action, so the fallback AC-3 makes reachable lands on the "
        "'*' unknown-label fail-safe. Enumerate it explicitly so its action is "
        f"a decision rather than a default. Enumerated arms: {sorted(labels)}"
    )
