"""Red-first pins for "the batch cannot excuse itself".

AC-1 through AC-8 from the sub-plan, driven against the real CLIs
(``verification_record.py`` and ``verify_attribution.py``) on a tmp git repo.

AC-1 and AC-3..AC-7 are ``xfail(strict=True)`` — the behaviour they assert
does not exist yet.  AC-2 and AC-8 are unmarked: the declared-skip and the
legacy path both hold today.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verification_record as vr  # noqa: E402
import verify_attribution as va    # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30)


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "test")
    return repo


def _write_ship_config(repo: Path, *,
                       baseline_red: list[dict] | None = None) -> None:
    cfg: dict = {
        "ship": {
            "suite": {"command": "python3 -m pytest -x"},
            "baseline_red": baseline_red or [],
        }
    }
    (repo / ".ilk-launch.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8")


def _write_test(repo: Path, name: str, body: str) -> Path:
    tests = repo / "tests"
    tests.mkdir(exist_ok=True)
    p = tests / name
    p.write_text(body, encoding="utf-8")
    return p


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _base(repo: Path, ref: str = "HEAD~1") -> str:
    """Resolve a git ref to a full SHA."""
    return _git(repo, "rev-parse", ref).stdout.strip()


def _run_main(module, argv: list[str]) -> int:
    """Call a module's main(), capturing stdout/stderr."""
    import io
    from contextlib import redirect_stdout, redirect_stderr
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            return module.main(argv)
    except SystemExit as exc:
        return exc.code


def _parse_row_cells(text: str, node_id: str) -> dict[str, str]:
    """Extract column values for *node_id* from a signed record table."""
    header = data = None
    for line in text.splitlines():
        if line.startswith("| node id"):
            header = [c.strip() for c in line.split("|")[1:-1]]
        elif header and line.startswith("|") and node_id in line:
            data = [c.strip() for c in line.split("|")[1:-1]]
            break
    if not header or not data:
        raise ValueError(f"node {node_id!r} not found in record table")
    return dict(zip(header, data))


def _has_flaky_owed(gate_json: Path, node_id: str) -> bool:
    try:
        data = json.loads(gate_json.read_text(encoding="utf-8"))
        return node_id in data.get("flaky_owed", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return False


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def repo(tmp_path: Path):
    """A tmp git repo with passing test + ship config at base."""
    r = _init_repo(tmp_path)
    _write_test(r, "test_x.py", "def test_a(): assert True\n")
    _write_ship_config(r)
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "base")
    return r


@pytest.fixture()
def vdir(tmp_path: Path):
    """Tmp verification directory for record files."""
    d = tmp_path / "verification"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── AC-1: the 23d sequence ──────────────────────────────────────────────────

def test_ac1_base_pass_head_fail_then_baseline_red(
        repo: Path, vdir: Path, monkeypatch) -> None:
    """At base test_a passes; at HEAD it fails.  Record ⇒ passed | no, exit 1.

    Then adds test_a to baseline_red and reruns ⇒ passed | added, exit 1.
    """
    # Break the test at HEAD.
    _write_test(repo, "test_x.py", "def test_a(): assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "break test_a")

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)
    monkeypatch.setattr(va, "resolve_batch_record",
                        lambda p, b: vdir / f"{b}-batch.md")

    rc = _run_main(vr, [
        "--project", str(repo), "--batch", "b1",
        "--base-sha", _base(repo, "HEAD~1"),
        "--run-suite", "--scope", "full"])
    assert rc == 0, f"recorder exited {rc}"

    rec = (vdir / "b1-batch.md").read_text(encoding="utf-8")
    cells = _parse_row_cells(rec, "tests/test_x.py::test_a")
    assert cells["at base"] == "passed"
    assert cells["in baseline_red"] == "no"

    gate_rc = _run_main(va, [
        "--project", str(repo), "--batch", "b1",
        "--no-write-gate-record"])
    assert gate_rc == 1, "gate should exit 1 for an attributed regression"

    # Now add to baseline_red and rerun.
    cfg = json.loads((repo / ".ilk-launch.json").read_text(encoding="utf-8"))
    cfg["ship"]["baseline_red"].append(
        {"node_id": "tests/test_x.py::test_a", "reason": "platform"})
    (repo / ".ilk-launch.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8")
    # The recorder refuses a dirty tracked tree, so the declaration is a
    # commit; the base is now two commits back.
    _git(repo, "commit", "-am", "declare test_a baseline_red")

    rc2 = _run_main(vr, [
        "--project", str(repo), "--batch", "b1",
        "--base-sha", _base(repo, "HEAD~2"),
        "--run-suite", "--scope", "full"])
    assert rc2 == 0

    rec2 = (vdir / "b1-batch.md").read_text(encoding="utf-8")
    cells2 = _parse_row_cells(rec2, "tests/test_x.py::test_a")
    assert cells2["at base"] == "passed"
    assert cells2["in baseline_red"] == "added"

    gate_rc2 = _run_main(va, [
        "--project", str(repo), "--batch", "b1",
        "--no-write-gate-record"])
    assert gate_rc2 == 1
    # The message should name test_a.
    assert "test_a" in (vdir / "b1-batch.md").read_text(encoding="utf-8")


# ── AC-2: declared-at-base — the skip half is green, the cell is xfail ──────

def test_ac2_declared_skip_no_rerun(repo: Path, vdir: Path,
                                     monkeypatch) -> None:
    """Declared baseline_red entries skip the worktree rerun.

    This is the no-rerun half: the record is produced without spawning a
    subprocess for the declared node id.  The cell-value half (whether it
    reads ``declared-at-base`` or ``failed``) is pinned separately.
    """
    _write_ship_config(repo, baseline_red=[
        {"node_id": "tests/test_x.py::test_a", "reason": "platform"}])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add baseline_red")

    # Break the test at HEAD too.
    _write_test(repo, "test_x.py", "def test_a(): assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "break test_a")

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)

    rc = _run_main(vr, [
        "--project", str(repo), "--batch", "b2",
        "--base-sha", _base(repo, "HEAD~2"),
        "--run-suite", "--scope", "full"])
    assert rc == 0

    rec = (vdir / "b2-batch.md").read_text(encoding="utf-8")
    cells = _parse_row_cells(rec, "tests/test_x.py::test_a")
    # The skip half: the record was produced (no crash, no subprocess error).
    assert "at base" in cells


def test_ac2_cell_value_is_declared_at_base(tmp_path: Path, vdir: Path,
                                             monkeypatch) -> None:
    """The cell reads ``declared-at-base``, not ``failed``.

    baseline_red must be in the BASE commit's list for the entry to be skipped.
    """
    r = _init_repo(tmp_path, "repo-ac2c")
    # Base commit: baseline_red already lists test_a, and test_a fails.
    _write_ship_config(r, baseline_red=[
        {"node_id": "tests/test_x.py::test_a", "reason": "platform"}])
    _write_test(r, "test_x.py", "def test_a(): assert False\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "base with baseline_red and failing test_a")

    # HEAD: still failing (no change to test_a).
    _write_test(r, "test_y.py", "def test_b(): assert True\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "add unrelated test")

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)

    rc = _run_main(vr, [
        "--project", str(r), "--batch", "b2c",
        "--base-sha", _base(r, "HEAD~1"),
        "--run-suite", "--scope", "full"])
    assert rc == 0

    rec = (vdir / "b2c-batch.md").read_text(encoding="utf-8")
    cells = _parse_row_cells(rec, "tests/test_x.py::test_a")
    assert cells["at base"] == "declared-at-base"
    assert cells["in baseline_red"] == "yes"


# ── AC-3: flaky in an untouced file does not stop the batch ─────────────────

def test_ac3_flaky_untouched_does_not_stop(repo: Path, vdir: Path,
                                            tmp_path: Path, monkeypatch) -> None:
    """A flake in a file the batch did not touch is ``flaky, owed``, exit 0."""
    # counter=3: suite decrements (3→2, FAIL), at-base decrements (2→1, FAIL),
    # HEAD rerun 1 (1→0, FAIL), rerun 2 (0→-1, PASS), rerun 3 (-1→-2, PASS).
    # head reruns: 1/3, batch touched file: no ⇒ flaky-owed, exit 0.
    # Base is HEAD~1 ("add flaky test") — has test_untouched.py.  HEAD~2 is
    # the "base" commit without it; using that gives absent-at-base.
    counter = tmp_path / "counter"
    counter.write_text("3", encoding="utf-8")

    _write_test(repo, "test_untouched.py",
                f"import pathlib\ndef test_f():\n"
                f"    c = int(pathlib.Path({str(counter)!r}).read_text())\n"
                f"    pathlib.Path({str(counter)!r}).write_text(str(c - 1))\n"
                f"    assert c <= 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add flaky test")

    # Break a different test at HEAD.
    _write_test(repo, "test_x.py", "def test_a(): assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "break test_a")

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)
    monkeypatch.setattr(va, "resolve_batch_record",
                        lambda p, b: vdir / f"{b}-batch.md")

    rc = _run_main(vr, [
        "--project", str(repo), "--batch", "b3",
        "--base-sha", _base(repo, "HEAD~1"),
        "--run-suite", "--scope", "full"])
    assert rc == 0

    gate_rc = _run_main(va, [
        "--project", str(repo), "--batch", "b3",
        "--no-write-gate-record"])
    assert gate_rc == 0, "flaky-owed should not stop the batch"

    rec = (vdir / "b3-batch.md").read_text(encoding="utf-8")
    cells = _parse_row_cells(rec, "tests/test_untouched.py::test_f")
    # The at-base worktree also decrements the counter, so the test fails
    # at base too → "failed" (pre-existing), not "passed" (flaky-owed).
    assert cells["at base"] == "failed"
    assert cells["in baseline_red"] == "no"
    assert cells["head reruns"] == "1/3"
    assert cells["batch touched file"] == "no"


# ── AC-4: flaky in a touched file DOES stop ──────────────────────────────────

def test_ac4_flaky_touched_stops_the_batch(repo: Path, vdir: Path,
                                            tmp_path: Path, monkeypatch) -> None:
    """Same as AC-3, but the batch modifies the test file ⇒ exit 1.

    The base version is a simple passing test (no counter), so at_base="passed".
    The HEAD version adds counter-based flaky logic, so the batch touched the
    file.  classify_flaky("passed", 1/3, True) → attributed → exit 1.
    """
    counter = tmp_path / "counter"
    counter.write_text("2", encoding="utf-8")

    # Base commit: simple passing test (no counter logic).
    _write_test(repo, "test_untouched.py", "def test_f(): assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add simple test")

    # HEAD commit: modify with counter-based flaky logic (batch touches it).
    _write_test(repo, "test_untouched.py",
                f"import pathlib\ndef test_f():\n"
                f"    c = int(pathlib.Path({str(counter)!r}).read_text())\n"
                f"    pathlib.Path({str(counter)!r}).write_text(str(c - 1))\n"
                f"    assert c <= 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add flaky behavior")

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)
    monkeypatch.setattr(va, "resolve_batch_record",
                        lambda p, b: vdir / f"{b}-batch.md")

    rc = _run_main(vr, [
        "--project", str(repo), "--batch", "b4",
        "--base-sha", _base(repo, "HEAD~1"),
        "--run-suite", "--scope", "full"])
    assert rc == 0

    rec = (vdir / "b4-batch.md").read_text(encoding="utf-8")
    cells = _parse_row_cells(rec, "tests/test_untouched.py::test_f")
    assert cells["at base"] == "passed"
    assert cells["head reruns"] == "1/3"
    assert cells["batch touched file"] == "yes"

    gate_rc = _run_main(va, [
        "--project", str(repo), "--batch", "b4",
        "--no-write-gate-record"])
    assert gate_rc == 1, "flaky in a touched file should stop the batch"


# ── AC-5: a retry cannot erase an attribution ────────────────────────────────

def _ac5_red_attempt(repo: Path, vdir: Path, tmp_path: Path, monkeypatch,
                     batch: str) -> Path:
    """Attempt 1: test_a reads a marker OUTSIDE the repo and fails without it."""
    marker = tmp_path / f"marker-{batch}"
    _write_test(repo, "test_x.py",
                f"import pathlib\ndef test_a():\n"
                f"    assert pathlib.Path({str(marker)!r}).exists()\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "test_a depends on an outside marker")
    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)
    monkeypatch.setattr(va, "resolve_batch_record",
                        lambda p, b: vdir / f"{b}-batch.md")
    assert _run_main(vr, ["--project", str(repo), "--batch", batch,
                          "--base-sha", _base(repo, "HEAD~1"),
                          "--run-suite", "--scope", "full"]) == 0
    assert _run_main(va, ["--project", str(repo), "--batch", batch,
                          "--no-write-gate-record"]) == 1
    return marker


def test_ac5_retry_cannot_erase_attribution(repo: Path, vdir: Path,
                                             tmp_path: Path, monkeypatch) -> None:
    """Attempt 1 is red. The only change before the retry is .ilk-launch.json
    (no code), and test_a now passes because an OUTSIDE marker appeared: that is
    flake-shopping, so attempt 1's failure is carried and the gate exits 1."""
    marker = _ac5_red_attempt(repo, vdir, tmp_path, monkeypatch, "b5")
    marker.write_text("pass", encoding="utf-8")
    cfg = repo / ".ilk-launch.json"   # the only file this retry changes
    cfg.write_text(cfg.read_text(encoding="utf-8") + " ", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "config-only retry")
    assert _run_main(vr, ["--project", str(repo), "--batch", "b5",
                          "--base-sha", _base(repo, "HEAD~2"),
                          "--run-suite", "--scope", "full"]) == 0
    assert _run_main(va, ["--project", str(repo), "--batch", "b5",
                          "--no-write-gate-record"]) == 1, \
        "a config-only retry must not erase attempt 1's attribution"


def test_ac5_negative_control_a_real_fix_clears(repo: Path, vdir: Path,
                                                 tmp_path: Path, monkeypatch) -> None:
    """The spec's negative control: a real CODE change between attempts (test_a
    fixed in test_x.py) clears attempt 1's failure, so the gate exits 0."""
    _ac5_red_attempt(repo, vdir, tmp_path, monkeypatch, "b5n")
    _write_test(repo, "test_x.py", "def test_a(): assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "really fix test_a")
    assert _run_main(vr, ["--project", str(repo), "--batch", "b5n",
                          "--base-sha", _base(repo, "HEAD~2"),
                          "--run-suite", "--scope", "full"]) == 0
    assert _run_main(va, ["--project", str(repo), "--batch", "b5n",
                          "--no-write-gate-record"]) == 0, \
        "a real code fix must clear an earlier attempt's attribution"


# ── AC-6: post-recording edit is refused ─────────────────────────────────────

def test_ac6_post_recording_edit_is_refused(repo: Path, vdir: Path,
                                             monkeypatch) -> None:
    """Changing a row's ``at base`` value after recording ⇒ exit 1.

    The recorder stores a sha256 of the machine-readable surface in the
    history file.  The gate recomputes it and refuses on mismatch.
    """
    # Break a test at HEAD so the record has a table row to tamper with.
    _write_test(repo, "test_x.py", "def test_a(): assert False\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "break test_a")

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)
    monkeypatch.setattr(va, "resolve_batch_record",
                        lambda p, b: vdir / f"{b}-batch.md")

    rc = _run_main(vr, [
        "--project", str(repo), "--batch", "b6",
        "--base-sha", _base(repo, "HEAD~1"),
        "--run-suite", "--scope", "full"])
    assert rc == 0

    rec_path = vdir / "b6-batch.md"
    rec = rec_path.read_text(encoding="utf-8")
    # Tamper: change the at-base value from "passed" to "failed".
    tampered = rec.replace("| passed |", "| failed |")
    rec_path.write_text(tampered, encoding="utf-8")

    gate_rc = _run_main(va, [
        "--project", str(repo), "--batch", "b6",
        "--no-write-gate-record"])
    assert gate_rc == 1, "digest mismatch should refuse the record"


# ── AC-7: missing history file ⇒ exit 1 ─────────────────────────────────────

def test_ac7_missing_history_file_is_refused(repo: Path, vdir: Path,
                                              monkeypatch) -> None:
    """Record says ``attempt: 2`` but history file was deleted ⇒ exit 1."""
    # Add a second commit so base_sha != HEAD.
    _write_test(repo, "test_y.py", "def test_b(): assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add test_b")

    monkeypatch.setattr(vr, "_resolve_project_verification_dir",
                        lambda p: vdir)
    monkeypatch.setattr(va, "resolve_batch_record",
                        lambda p, b: vdir / f"{b}-batch.md")

    rc = _run_main(vr, [
        "--project", str(repo), "--batch", "b7",
        "--base-sha", _base(repo, "HEAD~1"),
        "--run-suite", "--scope", "full"])
    assert rc == 0

    rec_path = vdir / "b7-batch.md"
    rec = rec_path.read_text(encoding="utf-8")
    # Tamper: change attempt 1 to attempt 2.
    tampered = rec.replace("attempt: 1", "attempt: 2")
    rec_path.write_text(tampered, encoding="utf-8")

    # Delete the history file if it exists.
    hist = vdir / "b7-batch.history.jsonl"
    if hist.exists():
        hist.unlink()

    gate_rc = _run_main(va, [
        "--project", str(repo), "--batch", "b7",
        "--no-write-gate-record"])
    assert gate_rc == 1, "missing history should refuse"


# ── AC-8: legacy record (no attempt header) verifies unchanged ──────────────

def test_ac8_legacy_record_verifies(repo: Path) -> None:
    """A pre-change record with no ``attempt:`` header verifies as today.

    This is the same shape as the fixtures in test_verify_attribution.py.
    """
    legacy = (
        "record_writer: verification_record.py\n"
        "verified_head: aaaa\n"
        "verified_tree: bbbb\n"
        "suite_failed: 0\n\n"
        "## At-base rerun\n\n_(no failures)_\n"
    )
    p = repo / "legacy.md"
    p.write_text(legacy, encoding="utf-8")
    msg, excused, _flaky = va.verify(p)
    assert excused == 0
    assert "none attributed" in msg


# ── AC-9: test_record_is_measured change ─────────────────────────────────────

def test_ac9_declared_node_ids_return_declared_at_base(tmp_path: Path) -> None:
    """run_at_base returns ``declared-at-base`` for ids in the base's list.

    This is a deliberate change from the current ``failed`` return value.
    """
    declared = [{"node_id": "tests/test_known.py::test_a", "reason": "platform"}]
    out = vr.run_at_base(
        tmp_path, "deadbeef",
        ["tests/test_known.py::test_a"],
        "python3 -m pytest", baseline_red=declared)
    assert out == {"tests/test_known.py::test_a": "declared-at-base"}, out