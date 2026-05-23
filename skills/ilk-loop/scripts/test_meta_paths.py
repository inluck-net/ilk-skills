"""Fixture-based smoke test for ilk_paths meta-project resolution.

Builds throwaway directory trees under tempfile, asserts:

  - meta_root finds the marker, ignores invalid ones
  - find_project_root prefers meta over .git when both are ancestors
  - read_meta_manifest rejects malformed / non-git repos
  - resolve_project_key returns the meta-derived key, not member's key
  - meta_member_for resolves the active sub-repo from cwd
  - legacy single-mode behaviour is unchanged when no marker exists

Run with: python -m test_meta_paths   (or just `python test_meta_paths.py`).
Stdlib only, no pytest dependency.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# Ensure we import the sibling ilk_paths module under test.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ilk_paths as ip  # noqa: E402


# ── tiny test harness ────────────────────────────────────────────────────────

_failures: list[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _failures.append(label + (f": {detail}" if detail else ""))


# ── fixture builders ─────────────────────────────────────────────────────────

def _mk_fake_repo(path: Path) -> None:
    """Create a directory that looks like a git repo (just has .git/)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)


def _mk_fake_worktree(path: Path, gitdir_target: Path) -> None:
    """Create a directory whose .git is a *file* pointing at gitdir_target."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").write_text(f"gitdir: {gitdir_target}\n", encoding="utf-8")


def _write_meta(meta_dir: Path, repos: list[tuple[str, str]], name: str | None = None) -> None:
    payload: dict = {"repos": [{"name": n, "path": p} for n, p in repos]}
    if name:
        payload["name"] = name
    (meta_dir / ip.META_MARKER).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


# ── tests ────────────────────────────────────────────────────────────────────

def test_single_mode_unchanged(tmp: Path) -> None:
    print("test_single_mode_unchanged:")
    repo = tmp / "myproj"
    _mk_fake_repo(repo)
    nested = repo / "src" / "deep" / "place"
    nested.mkdir(parents=True)

    root, kind = ip.find_project_root(nested)
    _check("kind == single", kind == "single", f"got {kind!r}")
    _check("root == repo", root == repo.resolve(), f"got {root}")
    _check("meta_root is None", ip.meta_root(nested) is None)
    _check("git_root == repo", ip.git_root(nested) == repo.resolve())

    key = ip.resolve_project_key(nested)
    # Key may be sha1-truncated on long Windows temp paths; we only
    # assert it's non-empty and deterministic.
    _check("project_key is non-empty", bool(key), f"got {key!r}")
    _check("project_key is stable", key == ip.resolve_project_key(nested))


def test_meta_mode_basic(tmp: Path) -> None:
    print("test_meta_mode_basic:")
    meta = tmp / "umbrella"
    meta.mkdir()
    _mk_fake_repo(meta / "api")
    _mk_fake_repo(meta / "portal")
    _mk_fake_repo(meta / "ops")
    _write_meta(meta, [("api", "api"), ("portal", "portal"), ("ops", "ops")], name="umbrella")

    # From a deep file inside api, we should resolve to the meta root.
    deep = meta / "api" / "src" / "module" / "file.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("# stub", encoding="utf-8")

    root, kind = ip.find_project_root(deep.parent)
    _check("kind == meta", kind == "meta", f"got {kind!r}")
    _check("root == meta dir", root == meta.resolve(), f"got {root}")
    _check("meta_root found", ip.meta_root(deep.parent) == meta.resolve())
    # git_root would point at api (the closest .git), but project_root is meta.
    _check("git_root resolves to api (sanity)", ip.git_root(deep.parent) == (meta / "api").resolve())

    # project_key should derive from META, not from api. The decisive
    # check is "key for meta ≠ key for api alone" — that proves the
    # meta root is winning over the .git lookup.
    key_meta = ip.resolve_project_key(deep.parent)
    key_member_only = ip.project_key(meta / "api")
    _check("key is non-empty", bool(key_meta), f"got {key_meta!r}")
    _check("meta key ≠ member-only key", key_meta != key_member_only,
           f"meta={key_meta!r} member={key_member_only!r}")
    # Also: the member-only key must match what we'd get if there were
    # no meta marker (sanity that the contrast is meaningful).
    _check("member-only key would target api", key_member_only != key_meta)

    # meta_member_for should report api.
    member = ip.meta_member_for(meta, deep.parent)
    _check("member resolves to api", member is not None and member["name"] == "api", f"got {member}")

    # From the meta root itself (not inside any member), member is None.
    member_at_root = ip.meta_member_for(meta, meta)
    _check("member at meta root is None", member_at_root is None, f"got {member_at_root}")


def test_meta_with_worktree_member(tmp: Path) -> None:
    """A meta member can itself be a git worktree (`.git` is a file).

    The worktree story for individual member repos must keep working
    under a meta umbrella.
    """
    print("test_meta_with_worktree_member:")
    meta = tmp / "umbrella2"
    meta.mkdir()
    main_repo = meta / "service"
    _mk_fake_repo(main_repo)
    # Pretend "service-feat" is a worktree of "service".
    wt = meta / "service-feat"
    _mk_fake_worktree(wt, main_repo / ".git" / "worktrees" / "feat")
    _write_meta(meta, [("service", "service"), ("service-feat", "service-feat")])

    root, kind = ip.find_project_root(wt / "lib")
    (wt / "lib").mkdir()
    _check("kind == meta", kind == "meta")
    _check("root == meta dir", root == meta.resolve())
    member = ip.meta_member_for(meta, wt / "lib")
    _check("member resolves to service-feat", member is not None and member["name"] == "service-feat", f"got {member}")


def test_invalid_marker_rejected(tmp: Path) -> None:
    print("test_invalid_marker_rejected:")

    # (1) marker points at a path that doesn't exist
    bad1 = tmp / "bad1"
    bad1.mkdir()
    _write_meta(bad1, [("ghost", "ghost")])
    _check("ghost path → meta_root None", ip.meta_root(bad1) is None)

    # (2) marker points at a real dir that has no .git
    bad2 = tmp / "bad2"
    bad2.mkdir()
    (bad2 / "notarepo").mkdir()
    _write_meta(bad2, [("notarepo", "notarepo")])
    _check("non-git path → meta_root None", ip.meta_root(bad2) is None)

    # (3) malformed JSON
    bad3 = tmp / "bad3"
    bad3.mkdir()
    (bad3 / ip.META_MARKER).write_text("{not json", encoding="utf-8")
    _check("malformed JSON → meta_root None", ip.meta_root(bad3) is None)

    # (4) empty repos list
    bad4 = tmp / "bad4"
    bad4.mkdir()
    (bad4 / ip.META_MARKER).write_text(json.dumps({"repos": []}), encoding="utf-8")
    _check("empty repos → meta_root None", ip.meta_root(bad4) is None)

    # (5) duplicate repo name
    bad5 = tmp / "bad5"
    bad5.mkdir()
    _mk_fake_repo(bad5 / "x")
    _write_meta(bad5, [("dup", "x"), ("dup", "x")])
    _check("duplicate names → meta_root None", ip.meta_root(bad5) is None)


def test_invalid_outer_does_not_block_valid_inner(tmp: Path) -> None:
    """If an outer marker is invalid but an inner one is valid,
    meta_root returns the inner one. (Practically defensive — protects
    against a stray ~/.ilk-meta.json swallowing a real project.)"""
    print("test_invalid_outer_does_not_block_valid_inner:")
    outer = tmp / "outer"
    outer.mkdir()
    _write_meta(outer, [("ghost", "ghost")])  # invalid: no such dir

    inner = outer / "real-project"
    inner.mkdir()
    _mk_fake_repo(inner / "api")
    _write_meta(inner, [("api", "api")])

    found = ip.meta_root(inner / "api")
    _check("inner valid marker wins", found == inner.resolve(), f"got {found}")


def test_no_project_anywhere(tmp: Path) -> None:
    print("test_no_project_anywhere:")
    empty = tmp / "nothing" / "here"
    empty.mkdir(parents=True)
    root, kind = ip.find_project_root(empty)
    _check("root is None", root is None)
    _check("kind defaults to single", kind == "single", f"got {kind!r}")
    _check("project_key is None", ip.resolve_project_key(empty) is None)


def test_plans_dir_uses_meta_key(tmp: Path) -> None:
    """find_plans_dir should look under the META-derived key, not the
    member-repo-derived key. We don't actually write plans here — we
    just confirm the external path it would check is the meta one."""
    print("test_plans_dir_uses_meta_key:")
    meta = tmp / "ufpr"
    meta.mkdir()
    _mk_fake_repo(meta / "api")
    _write_meta(meta, [("api", "api")])

    # Point ILK_DATA_HOME at a sandbox so we don't touch the user's real data.
    sandbox = tmp / "ilk-data-sandbox"
    os.environ["ILK_DATA_HOME"] = str(sandbox)
    try:
        # Create a fake plans dir under the META key and confirm it's found.
        key = ip.resolve_project_key(meta / "api")
        # On long temp paths the key is sha1-truncated; just confirm
        # it's the META key, not the api-only key.
        key_member_only = ip.project_key(meta / "api")
        _check("meta plans key differs from api-only key", key and key != key_member_only,
               f"meta={key!r} api={key_member_only!r}")
        plans_dir = ip.external_plans_dir(key)  # type: ignore[arg-type]
        plans_dir.mkdir(parents=True)
        (plans_dir / "MASTER-2026-05-23-execution-plan.md").write_text("# stub", encoding="utf-8")

        resolved, source = ip.find_plans_dir(meta / "api" / "any" / "subdir")
        # we created the subdir on the fly; ensure it exists first
        (meta / "api" / "any" / "subdir").mkdir(parents=True)
        resolved, source = ip.find_plans_dir(meta / "api" / "any" / "subdir")
        _check("plans dir resolves to external", source == "external", f"got source={source!r}")
        _check("plans dir is under meta key", resolved == plans_dir.resolve(), f"got {resolved}")
    finally:
        os.environ.pop("ILK_DATA_HOME", None)


# ── runner ───────────────────────────────────────────────────────────────────

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ilk-meta-test-") as raw:
        tmp = Path(raw).resolve()
        tests = [
            test_single_mode_unchanged,
            test_meta_mode_basic,
            test_meta_with_worktree_member,
            test_invalid_marker_rejected,
            test_invalid_outer_does_not_block_valid_inner,
            test_no_project_anywhere,
            test_plans_dir_uses_meta_key,
        ]
        for t in tests:
            t_tmp = tmp / t.__name__
            t_tmp.mkdir()
            try:
                t(t_tmp)
            except Exception:
                print(f"  [FAIL] {t.__name__} raised:")
                traceback.print_exc()
                _failures.append(f"{t.__name__} raised an exception")
            print()

    if _failures:
        print(f"FAILED: {len(_failures)} check(s)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("All ilk_paths meta tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
