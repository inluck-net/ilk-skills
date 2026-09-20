"""The re-keying migration must move state once, and refuse rather than merge.

``migrate_project_keys.py`` renames ``<data_root>/projects/<old_key>`` to
``<new_key>`` for every registered project after the C1 ``project_key`` fix.
Every project's plans, runtime and logs live in that one directory, so a wrong
move is a silent loss of a project's whole history and a wrong *merge* is two
projects' histories interleaved.

What these tests pin, in the order the sub-plan asks for them:

* every source directory has exactly one destination, and none is overwritten;
* the collision case — two old paths sharing one legacy directory — is
  **refused**, not silently merged;
* the migration is idempotent: a second pass plans nothing;
* it refuses to apply while a loop is live, and refuses equally when the
  liveness probe itself fails (a broken probe must not read as zero).

**Why the colliding pair is not built under ``tmp_path``.** The legacy key
hashed only above 80 characters. pytest's ``tmp_path`` on macOS resolves to
``/private/var/folders/…``, already over the cap, so a colliding pair built
there takes the legacy *hash* path and comes out with two distinct keys — the
refusal never fires and the test passes for the wrong reason. Measured while
writing step 2. The ``short_root`` fixture roots those cases under ``/tmp``
(``/private/tmp`` resolved, ~12 chars) so the pair really does collide.

Sub-plan: a-project-key-that-cannot-collide, step 3.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import migrate_project_keys as mpk  # noqa: E402
from ilk_paths import project_key  # noqa: E402

_SCRIPT = _SCRIPTS / "migrate_project_keys.py"


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def short_root(tmp_path_factory) -> Path:
    """A scratch root short enough that sub-paths stay under the legacy cap.

    ``tmp_path`` cannot be used: see the module docstring. The directory is
    created under ``/tmp`` with a unique name and removed afterwards; nothing
    outside it is touched, so this does not sweep a shared directory.
    """
    root = Path("/tmp") / f"ilkmig-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True)
    try:
        yield root.resolve()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _seed(data_root: Path, project_path: Path, marker: str) -> Path:
    """Create the legacy state directory for *project_path* with a marker file."""
    state = data_root / "projects" / mpk.legacy_project_key(project_path)
    (state / "plans").mkdir(parents=True, exist_ok=True)
    (state / "plans" / "MASTER-x.md").write_text(marker, encoding="utf-8")
    (state / "runtime").mkdir(exist_ok=True)
    (state / "logs").mkdir(exist_ok=True)
    return state


def _registry(paths: list[Path]) -> list[dict]:
    return [{"name": p.name, "path": str(p)} for p in paths]


# ── AC-1: every source has exactly one destination, none overwritten ────────


def test_a_mixed_tree_maps_each_source_to_exactly_one_destination(short_root) -> None:
    """The sub-plan's required shape: an over-80 key, an under-80 key, and a
    colliding pair, in one tree."""
    under = short_root / "p" / "app-one"
    over = short_root / "p" / ("D" * 95) / "deep"
    coll_a = short_root / "p" / "app_two"
    coll_b = short_root / "p" / "app-two"
    for p in (under, over, coll_a, coll_b):
        p.mkdir(parents=True)

    data = short_root / "data"
    for p in (under, over, coll_a, coll_b):
        _seed(data, p, str(p))

    plan = mpk.plan_migration(_registry([under, over, coll_a, coll_b]), data)

    # No two entries share a destination.
    dests = [e.new_key for e in plan.entries]
    assert len(dests) == len(set(dests)), dests

    # Every destination is the key the production function computes.
    for e in plan.entries:
        assert e.new_key == project_key(Path(e.path))

    # The colliding pair is refused; the other two are actionable.
    by_name = {e.name: e for e in plan.entries}
    assert by_name["app_two"].action == mpk.ACTION_REFUSE
    assert by_name["app-two"].action == mpk.ACTION_REFUSE
    assert by_name["app-one"].action == mpk.ACTION_MOVE
    assert by_name["deep"].action in (mpk.ACTION_MOVE, mpk.ACTION_UNCHANGED_KEY)


def test_an_over_cap_uppercase_path_still_moves(short_root) -> None:
    """Over the cap the constructions differ only by lowercasing the hash input,
    so a path with an uppercase character gets a new key and must move."""
    over = short_root / "P" / ("D" * 95) / "deep"
    over.mkdir(parents=True)
    data = short_root / "data"
    _seed(data, over, "over")

    plan = mpk.plan_migration(_registry([over]), data)
    entry = plan.entries[0]
    assert entry.old_key != entry.new_key
    assert entry.action == mpk.ACTION_MOVE


def test_an_all_lowercase_over_cap_path_keeps_its_key(short_root) -> None:
    """The other half of the same fact: no uppercase, no change, and the
    migration must NOT try to rename the directory onto itself."""
    over = short_root / "p" / ("d" * 95) / "deep"
    over.mkdir(parents=True)
    data = short_root / "data"
    _seed(data, over, "over")

    plan = mpk.plan_migration(_registry([over]), data)
    entry = plan.entries[0]
    assert entry.old_key == entry.new_key
    assert entry.action == mpk.ACTION_UNCHANGED_KEY
    assert plan.moves == []


def test_the_move_carries_the_state_and_leaves_the_source_gone(short_root) -> None:
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    src = _seed(data, under, "the-only-copy")

    plan = mpk.plan_migration(_registry([under]), data)
    assert mpk.apply_migration(plan) == []

    dst = data / "projects" / plan.entries[0].new_key
    assert (dst / "plans" / "MASTER-x.md").read_text(encoding="utf-8") == "the-only-copy"
    assert (dst / "runtime").is_dir() and (dst / "logs").is_dir()
    assert not src.exists()


# ── AC-2: the collision is refused, never merged ────────────────────────────


def test_two_paths_sharing_one_legacy_directory_are_refused_by_name(short_root) -> None:
    """The case that matters. One directory holds two projects' state and no
    rule can split it, so BOTH sides refuse and each names the other."""
    a = short_root / "p" / "app_two"
    b = short_root / "p" / "app-two"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert mpk.legacy_project_key(a) == mpk.legacy_project_key(b), (
        "fixture is not exercising the collision — is the root too long?")

    data = short_root / "data"
    _seed(data, a, "two-projects-one-dir")

    plan = mpk.plan_migration(_registry([a, b]), data)
    assert len(plan.refusals) == 2
    for e in plan.refusals:
        assert e.reason == mpk.REFUSE_SHARED_LEGACY_DIR
    other = {e.name: e.detail for e in plan.refusals}
    assert str(b) in other["app_two"]
    assert str(a) in other["app-two"]


def test_a_refused_collision_moves_nothing(short_root) -> None:
    a = short_root / "p" / "app_two"
    b = short_root / "p" / "app-two"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    data = short_root / "data"
    src = _seed(data, a, "untouched")

    plan = mpk.plan_migration(_registry([a, b]), data)
    assert mpk.apply_migration(plan) == []          # no moves to make
    assert (src / "plans" / "MASTER-x.md").read_text(encoding="utf-8") == "untouched"


def test_a_destination_that_already_exists_is_refused_not_merged(short_root) -> None:
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    src = _seed(data, under, "source-copy")
    dst = data / "projects" / project_key(under)
    (dst / "plans").mkdir(parents=True)
    (dst / "plans" / "MASTER-x.md").write_text("destination-copy", encoding="utf-8")

    plan = mpk.plan_migration(_registry([under]), data)
    assert [e.reason for e in plan.refusals] == [mpk.REFUSE_DESTINATION_EXISTS]
    assert mpk.apply_migration(plan) == []
    assert (src / "plans" / "MASTER-x.md").read_text(encoding="utf-8") == "source-copy"
    assert (dst / "plans" / "MASTER-x.md").read_text(encoding="utf-8") == "destination-copy"


def test_apply_rechecks_the_destination_immediately_before_moving(short_root) -> None:
    """A plan computed against a tree that changed underneath must error, not
    merge. Exercised by planting the destination after planning."""
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    _seed(data, under, "source-copy")

    plan = mpk.plan_migration(_registry([under]), data)
    assert len(plan.moves) == 1
    (data / "projects" / plan.moves[0].new_key).mkdir(parents=True)

    errors = mpk.apply_migration(plan)
    assert len(errors) == 1 and "destination appeared" in errors[0]
    assert not plan.moves[0].applied


# ── AC-3: idempotency ───────────────────────────────────────────────────────


def test_a_second_pass_after_a_successful_apply_plans_nothing(short_root) -> None:
    under = short_root / "p" / "app-one"
    over = short_root / "P" / ("D" * 95) / "deep"
    under.mkdir(parents=True)
    over.mkdir(parents=True)
    data = short_root / "data"
    for p in (under, over):
        _seed(data, p, str(p))

    registry = _registry([under, over])
    first = mpk.plan_migration(registry, data)
    assert mpk.apply_migration(first) == []
    assert all(e.applied for e in first.moves)

    second = mpk.plan_migration(registry, data)
    assert second.moves == []
    assert second.refusals == []
    assert {e.action for e in second.entries} == {mpk.ACTION_ALREADY_MIGRATED}

    # And a third apply is a no-op rather than an error.
    assert mpk.apply_migration(second) == []


def test_a_project_with_no_state_on_disk_is_not_an_error(short_root) -> None:
    under = short_root / "p" / "never-run"
    under.mkdir(parents=True)
    data = short_root / "data"
    (data / "projects").mkdir(parents=True)

    plan = mpk.plan_migration(_registry([under]), data)
    assert [e.action for e in plan.entries] == [mpk.ACTION_NO_STATE]
    assert plan.moves == [] and plan.refusals == []


# ── AC-4: only registered projects are touched ──────────────────────────────


def test_unregistered_directories_are_reported_and_left_alone(short_root) -> None:
    """``projects/`` also holds pytest tmp_path debris. Moving by pattern would
    move those too, so the migration is registry-driven and merely counts them."""
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    _seed(data, under, "real")
    debris = data / "projects" / "private-var-folders-99-pytest-of-chad-p-0a721da"
    (debris / "plans").mkdir(parents=True)

    plan = mpk.plan_migration(_registry([under]), data)
    assert plan.unclaimed == [debris.name]
    assert mpk.apply_migration(plan) == []
    assert debris.is_dir(), "an unregistered directory must never be moved"


# ── AC-5: liveness and the CLI contract ─────────────────────────────────────


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_dry_run_is_the_default_and_moves_nothing(short_root) -> None:
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    src = _seed(data, under, "still-here")
    reg = short_root / "projects.json"
    reg.write_text(json.dumps({"projects": _registry([under])}), encoding="utf-8")

    result = _run_cli("--registry", str(reg), "--data-root", str(data), "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["applied"] is False
    assert payload["move_count"] == 1
    assert src.exists(), "a dry run must not move anything"


def test_apply_refuses_while_a_loop_is_live(short_root, monkeypatch) -> None:
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    src = _seed(data, under, "still-here")

    monkeypatch.setattr(mpk, "live_loop_pids", lambda: [4242])
    rc = mpk.main(["--apply", "--registry", str(_write_reg(short_root, [under])),
                   "--data-root", str(data)])
    assert rc == 2
    assert src.exists()


def test_apply_refuses_when_the_liveness_probe_itself_fails(short_root, monkeypatch) -> None:
    """A broken probe and a true zero must not be byte-identical."""
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    src = _seed(data, under, "still-here")

    def _boom():
        raise RuntimeError("pgrep exited with code 2: permission denied")

    monkeypatch.setattr(mpk, "live_loop_pids", _boom)
    rc = mpk.main(["--apply", "--registry", str(_write_reg(short_root, [under])),
                   "--data-root", str(data)])
    assert rc == 2
    assert src.exists()


def test_apply_with_no_live_loop_moves_and_reports_zero(short_root, monkeypatch, capsys) -> None:
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    src = _seed(data, under, "moved")

    monkeypatch.setattr(mpk, "live_loop_pids", lambda: [])
    rc = mpk.main(["--apply", "--registry", str(_write_reg(short_root, [under])),
                   "--data-root", str(data), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is True
    assert payload["errors"] == []
    assert not src.exists()
    assert (data / "projects" / project_key(under) / "plans" / "MASTER-x.md").exists()


def test_apply_declines_when_any_project_is_refused(short_root, monkeypatch) -> None:
    """One unsafe project blocks the whole apply: a partial migration leaves the
    operator with no single command that describes the tree's state."""
    a = short_root / "p" / "app_two"
    b = short_root / "p" / "app-two"
    movable = short_root / "p" / "app-one"
    for p in (a, b, movable):
        p.mkdir(parents=True)
    data = short_root / "data"
    shared = _seed(data, a, "shared")
    src = _seed(data, movable, "movable")

    monkeypatch.setattr(mpk, "live_loop_pids", lambda: [])
    rc = mpk.main(["--apply", "--registry", str(_write_reg(short_root, [a, b, movable])),
                   "--data-root", str(data)])
    assert rc == 1
    assert shared.exists() and src.exists(), "nothing moves while a refusal stands"


def test_an_unreadable_registry_is_not_an_empty_migration(short_root) -> None:
    """Zero projects to migrate and "I could not read the registry" must differ."""
    data = short_root / "data"
    (data / "projects").mkdir(parents=True)

    missing = short_root / "nope.json"
    assert mpk.main(["--registry", str(missing), "--data-root", str(data)]) == 2

    garbage = short_root / "garbage.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert mpk.main(["--registry", str(garbage), "--data-root", str(data)]) == 2

    wrong_shape = short_root / "wrong.json"
    wrong_shape.write_text(json.dumps({"repos": []}), encoding="utf-8")
    assert mpk.main(["--registry", str(wrong_shape), "--data-root", str(data)]) == 2


def test_a_source_holding_git_worktrees_is_flagged(short_root) -> None:
    """git stores absolute paths on both sides of a worktree registration, so a
    rename invalidates it; the operator needs to be told before, not after."""
    under = short_root / "p" / "app-one"
    under.mkdir(parents=True)
    data = short_root / "data"
    state = _seed(data, under, "has-worktrees")
    (state / "runtime" / "launcher" / "worktrees" / "selfmod-batch").mkdir(parents=True)

    plan = mpk.plan_migration(_registry([under]), data)
    assert plan.entries[0].has_worktrees is True
    assert "worktree repair" in mpk.render(plan, applied=False, errors=[])


def _write_reg(root: Path, paths: list[Path]) -> Path:
    reg = root / f"projects-{uuid.uuid4().hex[:6]}.json"
    reg.write_text(json.dumps({"projects": _registry(paths)}), encoding="utf-8")
    return reg
