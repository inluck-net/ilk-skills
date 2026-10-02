#!/usr/bin/env python3
"""ilk_release — extract a git tag into a read-only release directory.

Usage:
    ilk_release.py <tag> [--repo <clone>] [--no-flip]
    ilk_release.py --rollback
    ilk_release.py --status

Releases root: $ILK_RELEASES_ROOT (default ~/.ilk/releases)
Pointers: current and previous are symlinks in the releases root's parent.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KEEP_RELEASES = 5


def _die(msg: str, code: int = 2) -> None:
    print(f"ilk_release: {msg}", file=sys.stderr)
    sys.exit(code)


def _releases_root() -> Path:
    return Path(os.environ.get("ILK_RELEASES_ROOT", Path.home() / ".ilk" / "releases"))


def _parent(root: Path) -> Path:
    """Parent of the releases root (where current/previous live)."""
    return root.parent


def _git(repo: Path, *args: str) -> str:
    """Run a git command and return stdout stripped."""
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _resolve_tag(repo: Path, tag: str) -> str:
    """Resolve a tag to a commit sha, or return empty string."""
    return _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")


def _atomic_symlink(link: Path, target: str) -> None:
    """Create a symlink atomically via tmp + os.replace."""
    tmp = link.parent / f".{link.name}.tmp-{os.getpid()}"
    try:
        os.symlink(target, tmp)
        os.replace(tmp, link)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _flip_current(root: Path, new_target: Path) -> None:
    """Atomically flip current to new_target, setting previous to old current."""
    parent = _parent(root)
    current = parent / "current"
    previous = parent / "previous"

    if current.is_symlink():
        old_target = Path(os.readlink(current))
        if old_target != new_target:
            _atomic_symlink(previous, str(old_target))

    _atomic_symlink(current, str(new_target))


def _prune(root: Path) -> None:
    """Keep the last KEEP_RELEASES release dirs by extracted_at. Never prune current/previous targets."""
    parent = _parent(root)
    current_target = ""
    previous_target = ""
    if (parent / "current").is_symlink():
        current_target = os.readlink(parent / "current")
    if (parent / "previous").is_symlink():
        previous_target = os.readlink(parent / "previous")

    protected = {current_target, previous_target}

    releases = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        manifest = d / ".ilk-release.json"
        if not manifest.is_file():
            print(f"ilk_release: skipping {d.name} (no manifest)", file=sys.stderr)
            continue
        try:
            data = json.loads(manifest.read_text())
            ts = data.get("extracted_at", "")
            releases.append((ts, d, str(d)))
        except (json.JSONDecodeError, KeyError):
            print(f"ilk_release: skipping {d.name} (bad manifest)", file=sys.stderr)
            continue

    releases.sort(key=lambda x: x[0], reverse=True)

    for _, d, d_str in releases[KEEP_RELEASES:]:
        if d_str in protected:
            continue
        # Make writable before removing
        for dirpath, dirnames, filenames in os.walk(d):
            for fn in filenames:
                fp = Path(dirpath) / fn
                fp.chmod(fp.stat().st_mode | 0o200)
            Path(dirpath).chmod(Path(dirpath).stat().st_mode | 0o200)
        shutil.rmtree(d)


def _extract(repo: Path, tag: str, sha: str, root: Path) -> Path:
    """Extract a tag into a release dir. Returns the release dir path."""
    release_dir = root / tag
    tmp_dir = root / f".{tag}.tmp-{os.getpid()}"

    # Clean up any stale tmp dir
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    try:
        tmp_dir.mkdir(parents=True)

        # git archive | tar -x
        archive = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", sha],
            capture_output=True,
        )
        if archive.returncode != 0:
            _die(f"git archive failed for {sha}")

        tar = subprocess.run(
            ["tar", "-x", "-C", str(tmp_dir)],
            input=archive.stdout,
            capture_output=True,
        )
        if tar.returncode != 0:
            _die(f"tar extract failed")

        # Write manifest
        manifest = {
            "tag": tag,
            "sha": sha,
            "source_repo": str(repo.resolve()),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        (tmp_dir / ".ilk-release.json").write_text(json.dumps(manifest, indent=2) + "\n")

        # Make read-only
        for dirpath, dirnames, filenames in os.walk(tmp_dir):
            for fn in filenames:
                fp = Path(dirpath) / fn
                fp.chmod(fp.stat().st_mode & ~0o222)
            Path(dirpath).chmod(Path(dirpath).stat().st_mode & ~0o222)

        # Atomic rename
        os.rename(str(tmp_dir), str(release_dir))
        return release_dir

    except BaseException:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise


def _cmd_install(tag: str, repo: Path, no_flip: bool) -> None:
    """Install a tag into a release directory."""
    # Validate tag name
    if "/" in tag or ".." in tag or tag.startswith("."):
        _die(f"invalid tag name: {tag!r}")

    root = _releases_root()
    root.mkdir(parents=True, exist_ok=True)

    sha = _resolve_tag(repo, tag)
    if not sha:
        _die(f"no such tag: {tag}")

    release_dir = root / tag
    if release_dir.is_dir():
        manifest = release_dir / ".ilk-release.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text())
            if data.get("sha") == sha:
                # Same sha — reuse
                if not no_flip:
                    _flip_current(root, release_dir)
                    _prune(root)
                return
            else:
                _die(f"tag {tag} moved: {data.get('sha')} != {sha}", code=3)

    # Extract
    release_dir = _extract(repo, tag, sha, root)

    if not no_flip:
        _flip_current(root, release_dir)
        _prune(root)


def _cmd_rollback() -> None:
    """Rollback: swap current and previous."""
    root = _releases_root()
    parent = _parent(root)
    current = parent / "current"
    previous = parent / "previous"

    if not previous.is_symlink():
        _die("no previous release to rollback to")

    prev_target = Path(os.readlink(previous))
    if not prev_target.is_dir():
        _die(f"previous target does not exist: {prev_target}")

    if not current.is_symlink():
        _die("no current release")

    curr_target = Path(os.readlink(current))

    # Swap atomically
    _atomic_symlink(current, str(prev_target))
    _atomic_symlink(previous, str(curr_target))


def _cmd_status() -> None:
    """Print JSON status of releases."""
    root = _releases_root()
    parent = _parent(root)
    current = parent / "current"
    previous = parent / "previous"

    current_val = os.readlink(current) if current.is_symlink() else None
    previous_val = os.readlink(previous) if previous.is_symlink() else None

    releases = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            manifest = d / ".ilk-release.json"
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text())
                    releases.append(data)
                except json.JSONDecodeError:
                    releases.append({"tag": d.name, "error": "bad manifest"})
            else:
                releases.append({"tag": d.name, "error": "no manifest"})

    print(json.dumps({
        "current": current_val,
        "previous": previous_val,
        "releases": releases,
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="ilk_release: manage immutable release directories")
    parser.add_argument("tag", nargs="?", help="Tag to install")
    parser.add_argument("--repo", help="Git repo to extract from")
    parser.add_argument("--no-flip", action="store_true", help="Don't flip current pointer")
    parser.add_argument("--rollback", action="store_true", help="Rollback to previous release")
    parser.add_argument("--status", action="store_true", help="Print release status as JSON")

    args = parser.parse_args()

    if args.status:
        _cmd_status()
        return

    if args.rollback:
        _cmd_rollback()
        return

    if not args.tag:
        parser.error("tag is required (unless --rollback or --status)")

    # Resolve repo
    if args.repo:
        repo = Path(args.repo)
        if not repo.is_dir():
            _die(f"repo not found: {repo}")
        # Verify it's a git repo
        if not (repo / ".git").exists() and not _git(repo, "rev-parse", "--is-inside-git-dir"):
            # Check if it's a worktree
            if not _git(repo, "rev-parse", "--is-inside-work-tree"):
                _die(f"not a git repo: {repo}")
    else:
        # Default: git toplevel of script's own realpath
        script_dir = Path(__file__).resolve().parent
        toplevel = _git(script_dir, "rev-parse", "--show-toplevel")
        if not toplevel:
            _die("cannot determine repo (script is not in a git work tree); use --repo")
        repo = Path(toplevel)
        # Verify it's a work tree (not bare)
        if _git(repo, "rev-parse", "--is-bare-repository") == "true":
            _die("script is in a bare repo; use --repo")

    _cmd_install(args.tag, repo, args.no_flip)


if __name__ == "__main__":
    main()