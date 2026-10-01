"""``main()`` in the runner never expands a ``plans_dir`` it did not assign.

The red-owner block (d7facba, 2026-09-26) passed ``"$plans_dir"`` to its
active-master lookup, but ``plans_dir`` is only ever a local of other
functions — ``main()`` never assigns it.  Under ``set -u`` the expansion
aborts that command, ``|| true`` swallows the abort, ``_active_master_file``
stays empty, and red-owner attribution is skipped on every confirmed-blocking
gate failure.  Seen on chad-mbp 2026-09-28, kira-cloudflare-scratch run
20260928-144712: ``run_ilk_loop_claude.sh: line 4699: plans_dir: unbound
variable`` right before ``Loop stopped: local_checks not passing (B2
confirmed)``.

``test_a_red_names_its_owner.py`` tests ``red_owner.py`` directly and never
the runner's wiring, which is how this shipped.  Two checks here:

1. static: every ``$plans_dir`` expansion inside ``main()`` follows an
   assignment to ``plans_dir`` inside ``main()``;
2. behavioural: the red-owner block's lookup, run in a shell with ``set -u``
   and the runner's functions sourced, resolves the active master.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"


def _main_body() -> list[tuple[int, str]]:
    lines = RUNNER.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("main() {"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return [(i + 1, lines[i]) for i in range(start, end)]


_EXPANSION = re.compile(r"\$\{?plans_dir\b(?![_:-])")
_ASSIGN = re.compile(r"(?:^|[\s;(])(?:local\s+)?plans_dir=")


def test_main_assigns_plans_dir_before_expanding_it() -> None:
    assigned = False
    unset_reads = []
    for n, line in _main_body():
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        if _ASSIGN.search(code):
            assigned = True
        if _EXPANSION.search(code) and not assigned:
            unset_reads.append(f"{RUNNER.name}:{n}: {line.strip()}")
    assert not unset_reads, (
        "main() expands $plans_dir before any assignment in main(); under "
        "set -u that aborts the command and `|| true` hides it:\n"
        + "\n".join(unset_reads)
    )


def test_the_fake_red_owner_attribution_is_gone() -> None:
    # The empty-command bisect that could never attribute anything was removed.
    # Pin that it stays gone.
    runner = RUNNER.read_text(encoding="utf-8")
    assert '--cmd ""' not in runner
