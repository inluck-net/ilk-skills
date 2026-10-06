"""A deploy smoke resolves the release tag in the toolkit repo, not in its cwd.

Measured 2026-10-06 (v0.9.154 train): the train's rezmac smoke runs
``host_deploy_status.py --require-tag`` over ssh, whose cwd is ``~``. The tag
resolver ran ``git tag --points-at`` there, which is not a repo, so every
remote smoke read ``tag-mismatch`` and the train rolled rezmac back. Run from
the repo the same check printed ``ok``.
"""
from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"
STATUS = SHIP_SCRIPTS / "host_deploy_status.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _tagged_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "toolkit"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-qm", "c")
    _git(repo, "tag", "-a", "v9.9.9", "-m", "v9.9.9")
    return repo, _git(repo, "rev-parse", "HEAD")


def _fake_bouncer(tmp_path: Path, sha: str) -> Path:
    b = tmp_path / "bin" / "bounce_daemons.sh"
    b.parent.mkdir()
    b.write_text("#!/usr/bin/env bash\n"
                 f'echo "recorded_sha: {sha}"\n'
                 'echo "tree_state: clean"\n'
                 'echo "fresh: scheduler — fresh (toolkit_head matches HEAD)"\n'
                 "exit 0\n")
    b.chmod(b.stat().st_mode | stat.S_IEXEC)
    return b


def _status(tmp_path: Path, *args: str) -> str:
    cwd = tmp_path / "not-a-repo"
    cwd.mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, str(STATUS), *args], cwd=cwd,
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def test_repo_flag_resolves_the_tag_from_a_non_git_cwd(tmp_path: Path) -> None:
    repo, sha = _tagged_repo(tmp_path)
    b = _fake_bouncer(tmp_path, sha)
    assert _status(tmp_path, "--bouncer", str(b), "--require-tag", "v9.9.9",
                   "--repo", str(repo)) == "ok"


def test_without_repo_a_non_git_cwd_still_reads_mismatch(tmp_path: Path) -> None:
    # Documents the defect the flag exists for; no behaviour change without it.
    repo, sha = _tagged_repo(tmp_path)
    b = _fake_bouncer(tmp_path, sha)
    assert _status(tmp_path, "--bouncer", str(b), "--require-tag", "v9.9.9") == "tag-mismatch"


def test_repo_flag_still_refuses_a_wrong_tag(tmp_path: Path) -> None:
    repo, sha = _tagged_repo(tmp_path)
    b = _fake_bouncer(tmp_path, sha)
    assert _status(tmp_path, "--bouncer", str(b), "--require-tag", "v0.0.1",
                   "--repo", str(repo)) == "tag-mismatch"


def _ssh_deploy_calls(tmp_path: Path, smoke_stdout: str) -> tuple[Path, list[list[str]]]:
    sys.path.insert(0, str(SHIP_SCRIPTS))
    sys.path.insert(0, str(LOOP_SCRIPTS))
    import release_train

    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls: list[list[str]] = []

    def runner(host, cmd, timeout=120):
        calls.append([str(c) for c in cmd])
        s = " ".join(str(c) for c in cmd)
        if "fetch" in s:
            return {"rc": 0, "stdout": "", "stderr": ""}
        if "ilk_release.py" in s:
            return {"rc": 0, "stdout": "", "stderr": ""}
        if "bounce_daemons" in s:
            return {"rc": 1, "stdout": "", "stderr": ""}
        if "host_deploy_status" in s:
            return {"rc": 0 if smoke_stdout == "ok" else 1, "stdout": smoke_stdout, "stderr": ""}
        return {"rc": 0, "stdout": "", "stderr": ""}

    release_train._ssh_deploy(project, "v0.0.2", data_dir, host="rezmac", ssh_runner=runner,
                              settle_deadline_sec=0.1, settle_poll_interval_sec=0.05)
    return project, calls


def _smoke_calls(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if any("host_deploy_status" in a for a in c)]


def test_the_remote_smoke_passes_the_repo(tmp_path: Path) -> None:
    project, calls = _ssh_deploy_calls(tmp_path, "ok")
    smokes = _smoke_calls(calls)
    assert smokes, calls
    for c in smokes:
        assert "--repo" in c and c[c.index("--repo") + 1] == str(project), c


def test_the_rollback_smoke_passes_the_repo_too(tmp_path: Path) -> None:
    project, calls = _ssh_deploy_calls(tmp_path, "tag-mismatch")
    smokes = _smoke_calls(calls)
    assert len(smokes) >= 2, calls  # deploy smoke(s) + rollback smoke
    for c in smokes:
        assert "--repo" in c and c[c.index("--repo") + 1] == str(project), c
