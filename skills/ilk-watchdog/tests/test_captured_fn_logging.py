"""A captured function owns its stdout.

Sub-plan `2026-08-29c-a-failed-classification-cannot-be-the-classification`,
step 2 (AC-5, as widened -- see the MASTER's "Scope review of judgment call 1").

When a caller writes ``x=$(fn ...)``, the function's stdout *is* its return
value. Anything else printed there is silently concatenated into the result.
Bash gives no warning, and the corrupted value usually still passes an ``-n``
guard, so the failure surfaces far from its cause.

Two field instances, both bash, both shipped and both found the expensive way:

  1. ``watchdog.sh`` -- ``invoke_postmortem_collect`` called ``write_log`` on
     its failure paths.  ``write_log`` ends with a bare ``echo``, so the log
     line became the classification.  Three unbounded relaunches on rezmac,
     2026-08-29.  Fixed in this sub-plan's step 1.
  2. ``run_ilk_loop_claude.sh`` -- ``preserve_dirty_tree_on_timeout`` ends with
     ``echo "$wip_count"``, and its ``git commit`` redirected only stderr.  On a
     *successful* commit, git's "[main abc1234] WIP: ..." joined the return
     value, ``int()`` raised in the JSONL builder, and the whole iteration
     record was lost.  Fixed in ``f5674c6``.

Both are now green, which makes them this detector's **positive controls**: if
the parser stops seeing them, the test has become decorative and says so.

Scope note. A third instance is often grouped with these -- ``emit_jsonl_record``'s
spaced JSON versus the runner's greps (F2).  That one is a *format* contract in
a **Python** file, not a stdout-channel violation, and it is already pinned by
``skills/ilk-loop/tests/test_gate_record_format_contract.py``.  This test does
not attempt to cover it; a stdout-channel detector structurally cannot.

Precision over coverage, per the sub-plan: a meta-test that false-positives
gets deleted by whoever it blocks.  The predicate is therefore two concrete
things a captured function must not do, not "looks like it might print".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

#: The bash scripts that define value-returning functions.  Widened from
#: watchdog.sh alone: instance 2 lived in run_ilk_loop_claude.sh, so a
#: watchdog-only test would have missed a member of the very family it exists
#: to catch.
_SCRIPTS = (
    "skills/ilk-watchdog/scripts/watchdog.sh",
    "skills/ilk-watchdog/scripts/scheduler.sh",
    "skills/ilk-loop/scripts/run_ilk_loop_claude.sh",
)

#: git subcommands that print to stdout on SUCCESS.  Failure output goes to
#: stderr and is not the hazard here -- instance 2 was a successful commit.
_PRINTING_GIT_SUBCOMMANDS = (
    "commit", "checkout", "merge", "pull", "push", "clone", "init", "tag",
    "stash", "switch", "restore", "revert", "cherry-pick",
)

_FUNC_OPEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{\s*$")
_FUNC_ONELINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{.*\}\s*$")
#: `x=$(fn ...)`, `local x=$(fn ...)`, `x="$(fn ...)"` -- the assignment
#: capture forms.  This is the CHECKED set: both field instances had this
#: exact shape, and keeping the checked set narrow keeps the test precise.
_CAPTURE = re.compile(r"=\s*\"?\$\(\s*([A-Za-z_][A-Za-z0-9_]*)\b")

#: Any `$(fn ...)`, assignment or not -- e.g. inline in a string, as in
#: `write_banner "DONE — $(to_upper "$x")"`.  Used ONLY to exclude functions
#: from the stdout-logger set: a function whose stdout is consumed as a value
#: anywhere is a value-returning function, not a logger.  Without this,
#: `to_upper` reads as a "logger" and any captured function calling it would
#: be a false positive.
_VALUE_USED = re.compile(r"\$\(\s*([A-Za-z_][A-Za-z0-9_]*)\b")
#: A redirect that takes the command's stdout away from the return channel.
#
#: The fd number matters and is the whole point.  `2>/dev/null` silences
#: *stderr* and leaves stdout on the return channel -- that was precisely the
#: shape of the run_ilk_loop_claude.sh instance (f5674c6): the WIP commit
#: redirected stderr only, and git's success line on stdout joined the return
#: value.  A pattern that accepts a bare `>/dev/null` substring therefore reads
#: the real defect as already-fixed.  Each alternative below pins fd 1
#: explicitly, or requires the `>` to have no digit in front of it.
_STDOUT_REDIRECTED = re.compile(
    r"(?:^|[^0-9])>\s*&\s*2"           # >&2
    r"|(?:^|[^0-9])>>?\s*/dev/null"     # >/dev/null, >>/dev/null
    r"|\b1>>?\s*(?:&\s*2|/dev/null)"   # 1>&2, 1>/dev/null
    r"|&>\s*/dev/null"                  # &>/dev/null (both channels)
    r"|(?:^|[^0-9])>>?\s*\"?\$"         # > "$somefile"
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_functions(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Map function name -> (first body line index, closing-brace index).

    Brace-block functions only, closing on a column-0 ``}``.  That is the
    house style in all three scripts; one-liners are recorded with an empty
    body since they cannot contain a multi-statement hazard.
    """
    funcs: dict[str, tuple[int, int]] = {}
    i = 0
    while i < len(lines):
        one = _FUNC_ONELINE.match(lines[i])
        if one:
            funcs[one.group(1)] = (i, i)
            i += 1
            continue
        m = _FUNC_OPEN.match(lines[i])
        if m:
            j = i + 1
            while j < len(lines) and lines[j] != "}":
                j += 1
            funcs[m.group(1)] = (i + 1, j)
            i = j
        i += 1
    return funcs


def _captured_function_names(lines: list[str], funcs: dict) -> set[str]:
    """Functions whose stdout some caller captures with ``$( )``."""
    found = set()
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        for name in _CAPTURE.findall(ln):
            if name in funcs:
                found.add(name)
    return found


def _value_used_function_names(lines: list[str], funcs: dict) -> set[str]:
    """Functions whose stdout is consumed as a value ANYWHERE, inline included."""
    found = set()
    for ln in lines:
        if ln.strip().startswith("#"):
            continue
        for name in _VALUE_USED.findall(ln):
            if name in funcs:
                found.add(name)
    return found


def _logger_functions(lines: list[str], funcs: dict, value_used: set[str]) -> set[str]:
    """Functions that exist to print to stdout as a side effect.

    Definition used: defined in this script, its stdout never consumed as a
    value anywhere in it, and its body contains an ``echo``/``printf`` whose
    stdout is not redirected.  ``write_log`` is the canonical member;
    ``write_log_quiet`` is excluded because its console copy goes to stderr,
    and ``to_upper`` is excluded because callers read its stdout inline.
    """
    loggers = set()
    for name, (start, end) in funcs.items():
        if name in value_used:
            continue
        for ln in lines[start:end]:
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            if re.match(r"^(echo|printf)\b", stripped) and not _STDOUT_REDIRECTED.search(stripped):
                loggers.add(name)
                break
    return loggers


def _join_logical_command(lines: list[str], idx: int, end: int) -> str:
    """Join a command that spans lines until its quotes balance.

    Needed because a redirect lands on the *last* physical line: the WIP
    commit in ``preserve_dirty_tree_on_timeout`` carries a multi-line ``-m``
    string and closes with ``>/dev/null 2>&1``.  Checking only the first line
    would report a false positive on already-correct code.
    """
    joined = ""
    for k in range(idx, min(end, idx + 40)):
        joined += lines[k] + " "
        if joined.count('"') % 2 == 0 and joined.count("'") % 2 == 0:
            break
    return joined


def _violations_in(text: str, path_label: str) -> list[str]:
    """Every captured function in `text` that writes to stdout off-channel."""
    lines = text.splitlines()
    funcs = _parse_functions(lines)
    captured = _captured_function_names(lines, funcs)
    loggers = _logger_functions(lines, funcs, _value_used_function_names(lines, funcs))

    problems: list[str] = []
    for name in sorted(captured):
        start, end = funcs[name]
        for i in range(start, end):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith("#"):
                continue

            # (a) a call to a stdout-writing logger
            for lg in loggers:
                if re.match(rf"^{re.escape(lg)}\b", stripped) and not _STDOUT_REDIRECTED.search(stripped):
                    problems.append(
                        f"{path_label}:{i + 1}: {name}() calls stdout-logger "
                        f"{lg} -- its output joins the return value. "
                        f"| {stripped[:80]}"
                    )

            # (b) an unredirected git subcommand that prints on success
            m = re.search(r"\bgit\s+(?:-C\s+\S+\s+)?([a-z-]+)\b", stripped)
            if m and m.group(1) in _PRINTING_GIT_SUBCOMMANDS:
                joined = _join_logical_command(lines, i, end)
                if not _STDOUT_REDIRECTED.search(joined):
                    problems.append(
                        f"{path_label}:{i + 1}: {name}() runs `git {m.group(1)}` "
                        f"without redirecting stdout -- git prints on success. "
                        f"| {stripped[:80]}"
                    )
    return problems


# ---------------------------------------------------------------------------
# The detector must actually fire
# ---------------------------------------------------------------------------

_SYNTHETIC_BAD = '''\
write_log() {
  local msg="$1"
  echo "[ts] $msg"
}

leaky_logger() {
  local x="$1"
  if [[ -z "$x" ]]; then
    write_log "nothing to do"
    echo ""
    return
  fi
  echo "$x"
}

leaky_git() {
  local n=0
  git commit -m "wip" 2>/dev/null
  echo "$n"
}

caller() {
  local a b
  a=$(leaky_logger "q")
  b=$(leaky_git)
}
'''

_SYNTHETIC_GOOD = '''\
write_log() {
  local msg="$1"
  echo "[ts] $msg"
}

write_log_quiet() {
  local msg="$1"
  echo "[ts] $msg" >&2
}

clean_fn() {
  local x="$1"
  if [[ -z "$x" ]]; then
    write_log_quiet "nothing to do"
    echo ""
    return
  fi
  git commit -m "wip
spanning lines" >/dev/null 2>&1
  echo "$x"
}

caller() {
  local a
  a=$(clean_fn "q")
}
'''


def test_detector_fires_on_a_known_bad_pattern() -> None:
    """A meta-test that cannot fail is decorative. Prove it fires."""
    problems = _violations_in(_SYNTHETIC_BAD, "<synthetic>")
    joined = "\n".join(problems)

    assert any("leaky_logger" in p and "write_log" in p for p in problems), (
        f"detector missed the logger-in-captured-function pattern:\n{joined}"
    )
    assert any("leaky_git" in p and "git commit" in p for p in problems), (
        f"detector missed the unredirected-git pattern:\n{joined}"
    )


def test_detector_does_not_fire_on_the_corrected_forms() -> None:
    """Precision: the two fixed shapes must read as clean.

    `write_log_quiet` (console copy on stderr) and a multi-line `git commit`
    closing with `>/dev/null 2>&1` are exactly how instances 1 and 2 were
    repaired. Flagging either would make the test a nuisance and get it
    deleted.
    """
    problems = _violations_in(_SYNTHETIC_GOOD, "<synthetic>")
    assert problems == [], "false positive on already-correct code:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# The real scripts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", _SCRIPTS)
def test_no_captured_function_writes_to_stdout_off_channel(rel: str) -> None:
    path = _REPO_ROOT / rel
    assert path.exists(), f"script not found: {rel}"
    problems = _violations_in(path.read_text(), rel)
    assert problems == [], (
        f"{len(problems)} captured function(s) write to stdout on a "
        f"value-returning path in {rel}:\n" + "\n".join(problems)
    )


def test_the_two_field_instances_are_visible_to_the_parser() -> None:
    """Positive controls, with their denominators.

    Both instances are fixed, so they pass. That is only meaningful if the
    parser still *sees* them -- otherwise a parser regression turns this whole
    file green while catching nothing.
    """
    seen: dict[str, set[str]] = {}
    for rel in _SCRIPTS:
        text = (_REPO_ROOT / rel).read_text()
        lines = text.splitlines()
        funcs = _parse_functions(lines)
        seen[rel] = _captured_function_names(lines, funcs)

    wd = "skills/ilk-watchdog/scripts/watchdog.sh"
    runner = "skills/ilk-loop/scripts/run_ilk_loop_claude.sh"

    assert "invoke_postmortem_collect" in seen[wd], (
        "lost sight of field instance 1 (watchdog.sh). Captured functions "
        f"found: {sorted(seen[wd])}"
    )
    assert "preserve_dirty_tree_on_timeout" in seen[runner], (
        "lost sight of field instance 2 (run_ilk_loop_claude.sh, f5674c6). "
        f"Captured functions found: {sorted(seen[runner])}"
    )

    # Denominator guard: a parser that finds almost nothing would also pass
    # every assertion above by accident.
    total = sum(len(v) for v in seen.values())
    assert total >= 12, (
        f"parser found only {total} captured functions across "
        f"{len(_SCRIPTS)} scripts; expected the real inventory. Per script: "
        + ", ".join(f"{k}={len(v)}" for k, v in seen.items())
    )
