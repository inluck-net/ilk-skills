"""Tests for release_train permit contract — fail-closed permit semantics.

Sub-plan: a-train-has-a-permit-for-every-host, step 0 (red-first pins).

Each test builds a throwaway data root under ``tmp_path`` with a permit store
containing one of six fixture states: complete, missing, stale, revoked,
partial, or crossed.  All external commands (prove, cut, deploy) are stubbed
as recording spies so refusal is asserted by verifying zero calls.

The five acceptance criteria:
  AC-1  scheduler and manual run refuse before prove/cut unless every
        configured host has a fresh permit bound to project, base tag,
        candidate head, host, and expiry.
  AC-2  permits are consumed atomically when deployment begins; stale,
        revoked, crossed-write, partial, or reused permits refuse.
  AC-3  deploy iterates ship.hosts, choosing explicit local or injected
        remote adapters, and records extract/bounce/smoke evidence per host.
  AC-4  one host failure cannot be hidden by another host's success; the
        result names deployed, rolled-back, untouched, and unverified hosts.
  AC-5  rollback is per host and roll-forward can be proven without
        changing real release links or using SSH in tests.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.allow_real_data_home

# ── Path setup ──────────────────────────────────────────────────────────────

SHIP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOOP_SCRIPTS = Path(__file__).resolve().parents[2] / "ilk-loop" / "scripts"

# ── Permit fixture helpers ──────────────────────────────────────────────────

_PERMIT_DIR = "runtime" / Path("permits")


def _permit_path(data_dir: Path, host: str) -> Path:
    """Return the permit file path for a given host."""
    return data_dir / _PERMIT_DIR / f"{host}.json"


def _project_key(project: Path) -> str:
    """Resolve the project key for a given project path."""
    sys.path.insert(0, str(LOOP_SCRIPTS))
    from ilk_paths import project_key
    return project_key(project)


def _write_permit(
    data_dir: Path,
    host: str,
    *,
    project: str | None = None,
    project_path: Path | None = None,
    base_tag: str = "v0.0.1",
    candidate_head: str = "a" * 40,
    expiry_minutes: int = 60,
    revoked: bool = False,
    missing_fields: list[str] | None = None,
    crossed_project: str | None = None,
    crossed_base_tag: str | None = None,
    crossed_candidate_head: str | None = None,
) -> None:
    """Write a permit fixture file for the given host.

    Args:
        data_dir: The project data root.
        host: Host name.
        project: Project key the permit is bound to.  If None, resolved from project_path.
        project_path: Project path for key resolution (used when project is None).
        base_tag: Base tag the permit is bound to.
        candidate_head: Candidate head SHA the permit is bound to.
        expiry_minutes: Minutes from now until expiry (negative = stale).
        revoked: If True, mark the permit as revoked.
        missing_fields: List of required fields to omit (partial permit).
        crossed_project: Override project field (crossed-write permit).
        crossed_base_tag: Override base_tag field (crossed-write permit).
        crossed_candidate_head: Override candidate_head field (crossed-write permit).
    """
    if project is None:
        if project_path is None:
            raise ValueError("Either project or project_path must be provided")
        project = _project_key(project_path)

    permit_dir = data_dir / _PERMIT_DIR
    permit_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    expiry = now + timedelta(minutes=expiry_minutes)

    permit = {
        "project": project,
        "base_tag": base_tag,
        "candidate_head": candidate_head,
        "host": host,
        "issued_at": now.isoformat(),
        "expires_at": expiry.isoformat(),
        "consumed": False,
        "revoked": revoked,
    }

    # Apply crossed-write overrides
    if crossed_project is not None:
        permit["project"] = crossed_project
    if crossed_base_tag is not None:
        permit["base_tag"] = crossed_base_tag
    if crossed_candidate_head is not None:
        permit["candidate_head"] = crossed_candidate_head

    # Remove fields for partial permit
    if missing_fields:
        for field in missing_fields:
            permit.pop(field, None)

    _permit_path(data_dir, host).write_text(
        json.dumps(permit, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_hosts_config(data_dir: Path, hosts: list[str]) -> None:
    """Write a minimal ship.hosts configuration."""
    config_dir = data_dir / "runtime"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {"ship": {"hosts": hosts}}
    (config_dir / "ship-config.json").write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_recording_prove() -> tuple[callable, list]:
    """Return a prove stub that records calls and returns proven=True."""
    calls: list = []

    def _prove(project: Path, data_dir: Path) -> dict:
        calls.append(("prove", project, data_dir))
        return {"proven": True, "reason": "", "new_failing_ids": [], "proof_file": None}

    return _prove, calls


def _make_recording_cut() -> tuple[callable, list]:
    """Return a cut stub that records calls and returns a tag."""
    calls: list = []

    def _cut(project: Path, data_dir: Path) -> dict:
        calls.append(("cut", project, data_dir))
        return {"tag": "v0.0.2", "commit": "b" * 40, "pushed": True, "baseline": "/dev/null"}

    return _cut, calls


def _make_recording_deploy() -> tuple[callable, list]:
    """Return a deploy stub that records calls and returns deployed=True."""
    calls: list = []

    def _deploy(project: Path, tag: str, data_dir: Path) -> dict:
        calls.append(("deploy", project, tag, data_dir))
        return {"tag": tag, "deployed": True, "exit_code": 0}

    return _deploy, calls


# ── AC-1: complete permits pass, missing/stale/revoked/partial/crossed refuse ──


class TestPermitContract:
    """AC-1/AC-2: prove/cut/deploy refuse unless every host has a fresh, valid permit."""

    def test_complete_permit_allows_prove(self, tmp_path: Path) -> None:
        """A complete, fresh permit for every host → prove is called.

        Control test: this should always pass.  When the permit contract
        is implemented, this verifies the happy path is not broken.
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project)
        _write_permit(data_dir, "host-b", project_path=project)
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        # run() should proceed past the permit check and call prove
        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 1, "prove should be called with a complete permit"

    def test_missing_permit_refuses_prove(self, tmp_path: Path) -> None:
        """No permit file for host-b → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project)
        # host-b: no permit file
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a missing permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a missing permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a missing permit"

    def test_stale_permit_refuses_prove(self, tmp_path: Path) -> None:
        """Permit for host-b is expired → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project)
        _write_permit(data_dir, "host-b", project_path=project, expiry_minutes=-10)
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a stale permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a stale permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a stale permit"

    def test_revoked_permit_refuses_prove(self, tmp_path: Path) -> None:
        """Permit for host-b is revoked → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project)
        _write_permit(data_dir, "host-b", project_path=project, revoked=True)
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a revoked permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a revoked permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a revoked permit"

    def test_partial_permit_refuses_prove(self, tmp_path: Path) -> None:
        """Permit for host-b is missing required fields → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project)
        _write_permit(data_dir, "host-b", project_path=project, missing_fields=["candidate_head"])
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a partial permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a partial permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a partial permit"

    def test_crossed_permit_refuses_prove(self, tmp_path: Path) -> None:
        """Permit for host-b is bound to wrong project → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project)
        _write_permit(data_dir, "host-b", project_path=project, crossed_project="wrong-project")
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a crossed permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a crossed permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a crossed permit"

    def test_crossed_base_tag_refuses_prove(self, tmp_path: Path) -> None:
        """Permit for host-b is bound to wrong base tag → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project, base_tag="v0.0.1")
        _write_permit(data_dir, "host-b", project_path=project, crossed_base_tag="v0.0.0")
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a crossed base_tag permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a crossed base_tag permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a crossed base_tag permit"

    def test_crossed_candidate_head_refuses_prove(self, tmp_path: Path) -> None:
        """Permit for host-b is bound to wrong candidate head → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project, candidate_head="a" * 40)
        _write_permit(data_dir, "host-b", project_path=project, crossed_candidate_head="f" * 40)
        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a crossed candidate_head permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a crossed candidate_head permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a crossed candidate_head permit"


# ── AC-2: consumed permit refuses reuse ────────────────────────────────────


class TestConsumedPermitRefusesReuse:
    """AC-2: a permit marked consumed cannot be reused."""

    def test_consumed_permit_refuses_prove(self, tmp_path: Path) -> None:
        """Permit for host-b is already consumed → prove is NOT called."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        _write_permit(data_dir, "host-a", project_path=project)
        _write_permit(data_dir, "host-b", project_path=project)
        # Mark host-b's permit as consumed
        permit = json.loads(_permit_path(data_dir, "host-b").read_text())
        permit["consumed"] = True
        _permit_path(data_dir, "host-b").write_text(json.dumps(permit, indent=2) + "\n")

        _write_hosts_config(data_dir, ["host-a", "host-b"])

        prove_fn, prove_calls = _make_recording_prove()
        cut_fn, cut_calls = _make_recording_cut()
        deploy_fn, deploy_calls = _make_recording_deploy()

        sys.path.insert(0, str(SHIP_SCRIPTS))
        sys.path.insert(0, str(LOOP_SCRIPTS))
        import release_train

        result = release_train.run(
            project=project,
            data_dir=data_dir,
            check_fn=lambda p, d: {"eligible": True, "reason": "", "last_tag": "v0.0.1", "head": "a" * 40},
            prove_fn=prove_fn,
            cut_fn=cut_fn,
            deploy_fn=deploy_fn,
            notify_script="/dev/null",
        )

        assert len(prove_calls) == 0, "prove should NOT be called with a consumed permit"
        assert len(cut_calls) == 0, "cut should NOT be called with a consumed permit"
        assert len(deploy_calls) == 0, "deploy should NOT be called with a consumed permit"


# ── Cleanup ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cleanup_env():
    """Clean up environment variables set by tests."""
    old_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(old_env)