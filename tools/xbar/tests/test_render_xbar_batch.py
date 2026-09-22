"""The row names its batch: "<batch> M/N" ahead of the sub-plan name.

Operator request 2026-09-20: M = registry position of the rendered
sub-plan (shipped ones counted), N = sub-plans in the batch, plus a
"(+K batches)" suffix when the project owes more than the current one.

Mirrors the heartbeat test's stale-checkout concern: the renderer and
status_all ship in the same repo but install via symlinked plugin, so a
payload WITHOUT the batch fields must render exactly as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from render_xbar import render_xbar  # noqa: E402


def _entry(**extra) -> dict:
    e = {
        "project_key": "proj",
        "path": "/fake/proj",
        "repo_path": None,
        "orphaned": False,
        "active_master": "MASTER-x.md",
        "next_subplan": "some-subplan",
        "step": "2/6",
        "sentinel": {"pid": 1, "state": "running", "alive": True},
        "last_class": None,
        "model": "",
        "runnable": True,
        "parked": False,
        "manually_runnable": True,
        "blocked": False,
        "run_id": None,
        "iteration": None,
        "iteration_elapsed_s": None,
        "heartbeat_s": None,
    }
    e.update(extra)
    return e


def _row(entries: list[dict]) -> str:
    out = render_xbar(entries)
    lines = [
        l.split("|", 1)[0].strip()
        for l in out.splitlines()
        if l.startswith("* ") or l.startswith("- ")
    ]
    return lines[0]


def test_batch_position_precedes_subplan_name() -> None:
    row = _row([_entry(
        batch="pv5-rereview", subplan_index=3, subplan_count=7,
        pending_batches=1,
    )])
    # "pv5-rereview 3/7" BEFORE the sub-plan name; the sub-plan's own
    # "2/6" step count must not be readable as the batch position.
    assert "pv5-rereview 3/7  some-subplan  2/6" in row


def test_pending_badge_ahead_of_name_only_above_one() -> None:
    # Operator spec 2026-09-20: "+N" AHEAD of the project name, rendered
    # only when N > 1; N counts total owed incl. the current batch.
    multi = _row([_entry(
        batch="b", subplan_index=1, subplan_count=2, pending_batches=3,
    )])
    assert multi.startswith("* +3 proj")
    assert "batches" not in multi
    lone = _row([_entry(
        batch="b", subplan_index=1, subplan_count=2, pending_batches=1,
    )])
    assert lone.startswith("* proj")
    assert "+1" not in lone


def test_stale_payload_without_batch_fields_renders_unchanged() -> None:
    row = _row([_entry()])
    assert "some-subplan" in row and "2/6" in row
    assert "/" not in row.split("some-subplan")[0].replace("* proj", "")


# ── row action + Copy reference (operator request, re-added 2026-09-20) ──


def _raw_line(entries: list[dict], prefix: str) -> str:
    for l in render_xbar(entries).splitlines():
        if l.startswith(prefix):
            return l
    return ""


def test_every_row_carries_refresh_action() -> None:
    # Actionless rows are disabled by AppKit and a disabled item does not
    # open its submenu — the "running rows' sub-panels won't open" defect.
    # `refresh=true` keeps rows enabled; the submenu still wins the click.
    line = _raw_line([_entry()], "* ")
    assert line.endswith("| refresh=true")


def test_copy_reference_child_mirrors_start_now_quoting() -> None:
    out = render_xbar([_entry(next_subplan_file="2026-09-19-pv5-audio.md")])
    copy_lines = [l for l in out.splitlines() if l.startswith("--Copy reference")]
    assert len(copy_lines) == 1
    l = copy_lines[0]
    # Same invocation shape as the proven-working Start-now action.
    assert "bash='/bin/bash'" in l
    assert "terminal=false refresh=false" in l
    ref = l.split("param2='", 1)[1].split("'", 1)[0]
    assert ref == "ilk-ref:proj/MASTER-x.md/2026-09-19-pv5-audio.md"
    # Space-free and pipe-free by grammar: the parser ends unquoted values
    # at spaces and splits the params blob on pipes.
    assert " " not in ref and "|" not in ref


def test_copy_reference_absent_without_subplan_file() -> None:
    out = render_xbar([_entry()])
    assert not [l for l in out.splitlines() if l.startswith("--Copy reference")]


def test_blocked_row_owing_nothing_is_hidden() -> None:
    # Finished-project residue (all masters shipped, a stale verification
    # run holding a blocked flag): hidden (operator, 2026-09-20).
    out = render_xbar([_entry(blocked=True, pending_batches=0)])
    assert "proj" not in out


def test_blocked_row_owing_work_stays_visible() -> None:
    # The same filter must not swallow a genuinely stuck project that
    # still owes a batch.
    line = _raw_line([_entry(blocked=True, pending_batches=1)], "! ")
    assert line.startswith("! proj")


# ── g2-parked-batches-stay-visible AC-2: the parked batch row ────────


def _parked_entry() -> dict:
    """The payload shape status_all emits for a violation-parked master
    (blocked + parked_reason + owed sub-plans) — the kira pv6 shape the
    panel dropped entirely before the fix."""
    return _entry(
        blocked=True,
        blocked_reason="parked:ship_integrity_violation:",
        parked=True,
        parked_reason=(
            "ship_integrity_violation: run 20260920-162655 slugs=[pv6-seam-pass]"
        ),
        batch="pv6",
        subplan_index=1,
        subplan_count=2,
        next_subplan="pv6-seam-pass (in-progress)",
        next_subplan_file="2026-09-20-pv6-seam-pass.md",
        step="3/3",
        pending_batches=1,
        manually_runnable=False,
        active_master="MASTER-2026-09-20-pv6.md",
    )


def test_violation_parked_batch_row_visible_with_context() -> None:
    # Icon "!", batch name + M/N, first owed sub-plan with its step — the
    # loudest row in the panel, not an invisible one.
    line = _raw_line([_parked_entry()], "! ")
    assert line.startswith("! proj")
    assert "pv6 1/2" in line
    assert "pv6-seam-pass (in-progress)  3/3" in line


def test_violation_parked_batch_row_names_its_reason() -> None:
    # The submenu carries the full park sentence; the row line has no room.
    out = render_xbar([_parked_entry()])
    reason = [l for l in out.splitlines() if l.startswith("--parked:")]
    assert len(reason) == 1
    assert "ship_integrity_violation: run 20260920-162655" in reason[0]


def test_violation_parked_batch_row_offers_resume() -> None:
    # Parked work is human-held: the Resume action lives in this row.
    out = render_xbar([_parked_entry()])
    assert [l for l in out.splitlines() if l.startswith("--Resume ")]


# ── tray-names-what-it-names AC-1: worktree-keyed project naming ─────


def test_worktree_keyed_row_shows_repo_name_plus_bracketed_worktree() -> None:
    """A project whose repo_path sits under a ``worktrees/`` parent must
    render as ``<repo-name> [<worktree>]`` — e.g. ``kira-cloudflare [pv-5611]``.

    Today's code (``render_xbar.py:193``) does ``rp.rsplit("/", 1)[-1]``,
    which yields the bare worktree dir name ``pv-5611`` — the defect
    measured by the operator on 2026-09-21.
    """
    row = _row([_entry(
        repo_path="/Users/chad/Projects/keyreply/kira-cloudflare/worktrees/pv-5611",
    )])
    assert "kira-cloudflare [pv-5611]" in row, f"got: {row}"


def test_scratch_worktree_keyed_row_shows_repo_name_plus_bracketed() -> None:
    """Same fix for ``scratch-worktrees/`` — e.g. ``kira-cloudflare [resolver]``."""
    row = _row([_entry(
        repo_path="/Users/chad/Projects/keyreply/kira-cloudflare/scratch-worktrees/resolver",
    )])
    assert "kira-cloudflare [resolver]" in row, f"got: {row}"


def test_plain_repo_path_unchanged() -> None:
    """A plain repo path (no worktrees parent) renders exactly as today."""
    row = _row([_entry(
        repo_path="/Users/chad/Projects/keyreply/kira-cloudflare",
    )])
    assert "kira-cloudflare" in row
    # Must NOT contain brackets — that's the worktree grammar.
    assert "[" not in row, f"plain path got worktree format: {row}"


def test_hyphenated_worktree_composite_renders_correctly() -> None:
    """The resolver creates hyphenated composites on disk:
    ``…/kira-cloudflare-worktrees/pv-5611``.  Strip the suffix to recover
    the repo name.
    """
    row = _row([_entry(
        repo_path="/Users/chad/Projects/keyreply/kira-cloudflare-worktrees/pv-5611",
    )])
    assert "kira-cloudflare [pv-5611]" in row, f"got: {row}"


def test_hyphenated_scratch_worktree_composite_renders_correctly() -> None:
    """Same for ``scratch-worktrees`` composites."""
    row = _row([_entry(
        repo_path="/Users/chad/Projects/keyreply/kira-cloudflare-scratch-worktrees/resolver",
    )])
    assert "kira-cloudflare [resolver]" in row, f"got: {row}"


# ── tray-shows-and-switches step-0: Models section (RED) ─────────────


def _entry_with_roles(**extra) -> dict:
    """Entry carrying a roles block — the payload shape step 1 produces."""
    e = _entry()
    # Field names MUST match what status_all._roles_block() actually emits
    # (name / home / model / provider_host / auth) — the status_all→renderer
    # boundary is a contract surface, and a fixture that invents its own
    # names tests nothing the pipeline does. Verified against
    # skills/ilk-loop/scripts/status_all.py:111-148 on 2026-09-22.
    e["roles"] = [
        {
            "name": "coder",
            "home": "~/.claude-worker",
            "model": "mimo-v2.5-pro",
            "provider_host": "token-plan-cn.xiaomimimo.com",
            "auth": "custom",
            "provider": "Xiaomi MiMo V2.5 - Pro",
        },
    ]
    # The submenu is built from the providers block status_all publishes
    # alongside roles (status_all._providers_block()).
    e["providers"] = [
        {"id": "66f41c76", "name": "Xiaomi MiMo V2.5 - Pro", "model": "mimo-v2.5-pro"},
        {"id": "0d101427", "name": "Zhipu GLM", "model": "glm-5.3"},
    ]
    e.update(extra)
    return e


def test_models_section_renders_one_row_per_role() -> None:
    """AC-2: the renderer emits one Models row per role.

    This is the red test for tray-shows-and-switches step 0.  Step 2
    adds the Models section; today the output does not contain per-role
    model rows with provider submenus — this test FAILS.
    """
    out = render_xbar([_entry_with_roles()])
    model_rows = [
        l for l in out.splitlines()
        if l.strip().startswith("-") and "Models" in l
    ]
    assert model_rows, (
        "expected a 'Models' separator row in the submenu; got none"
    )


def test_provider_submenu_invokes_use_command() -> None:
    """AC-2: each provider submenu item invokes
    ``ilk-worker-model use <role> <provider>`` with no ``--now``.

    This is the red test for tray-shows-and-switches step 0.  Step 2
    adds the provider submenu; today no such action exists — this test
    FAILS.
    """
    out = render_xbar([_entry_with_roles()])
    # SwiftBar actions are param-separated: `bash=<cmd> param1=.. param2=..`,
    # never a contiguous command string (see the "Start now" action in
    # render_xbar.py). Matching the literal "ilk-worker-model use" asserts a
    # shape SwiftBar cannot express.
    provider_actions = [
        l for l in out.splitlines()
        if "bash=ilk-worker-model" in l and "param1=use" in l
    ]
    assert provider_actions, (
        "expected submenu items invoking 'ilk-worker-model use <role> <provider>'; "
        "got none"
    )
    # No --now: the action is config-only, applies from next iteration.
    for action in provider_actions:
        assert "--now" not in action, (
            f"provider switch must not carry --now: {action}"
        )


def test_divergence_renders_both_models() -> None:
    """AC-3: when configured model ≠ last observed model, both are shown.

    The existing ``worker model:`` line shows what iterations ACTUALLY ran.
    The new per-role row shows what is configured.  When they differ, the
    renderer renders the divergence — neither value is dropped.

    This is the red test for step 0.  Step 2 adds this rendering; today
    the configured model does not appear — this test FAILS.
    """
    entry = _entry_with_roles(model="claude-sonnet-4-20250514")
    out = render_xbar([entry])
    # The live model line still shows the iteration model.
    assert "claude-sonnet-4-20250514" in out, "live model must stay in output"
    # The configured model must ALSO appear (per-role row).
    assert "mimo-v2.5-pro" in out, (
        "configured model 'mimo-v2.5-pro' must appear in the Models section"
    )
