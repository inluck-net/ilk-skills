"""The release safety case runs the golden batch on the incumbent AND the candidate.

RSI design point 1 (Chad, Oct 6): a machinery regression check. The oracle
(fixture + expected.json) comes from the candidate tree's rules-tier
``tests/invariants``; the incumbent is the deployed release
(``~/.ilk/current``). A verdict flip (incumbent passes, candidate fails) gets
one re-run, then refuses. Time is a flag only.

Every test stubs the component runner, so nothing here runs the real golden
batch. The real ``--skills-from`` path was exercised by hand when this landed
(see the commit message).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

LOOP_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(LOOP_SCRIPTS))

import safety_case  # noqa: E402


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    budget_dir = project / "tests" / "invariants" / "fixtures" / "golden"
    budget_dir.mkdir(parents=True)
    (budget_dir / "budget.json").write_text(json.dumps({"max_seconds": 120}))
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", "add", "."],
                   cwd=project, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
                    "-qm", "init"], cwd=project, check=True)
    return project


def _incumbent(tmp_path: Path) -> Path:
    inc = tmp_path / "releases" / "v0"
    runner = inc / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/bash\n")
    return inc


class _Stub:
    """Runner stub: golden outcome per side, in call order for the candidate."""

    def __init__(self, candidate: list[int], incumbent: int,
                 candidate_sleep: float = 0.0, incumbent_sleep: float = 0.0):
        self.candidate = list(candidate)
        self.incumbent = incumbent
        self.candidate_sleep = candidate_sleep
        self.incumbent_sleep = incumbent_sleep
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        if "--skills-from" in cmd:
            rc, nap = self.incumbent, self.incumbent_sleep
        else:
            rc, nap = self.candidate.pop(0), self.candidate_sleep
        time.sleep(nap)
        out = json.dumps({"verdict": "pass" if rc == 0 else "fail",
                          "mismatches": [] if rc == 0 else [{"slug": "x"}],
                          "seconds": nap})
        return type("R", (), {"returncode": rc, "stdout": out, "stderr": ""})()


def _golden(result: dict) -> dict:
    return next(c for c in result["components"] if c["name"] == "golden")


def _run(tmp_path, stub, incumbent):
    return safety_case.run(_project(tmp_path), data_dir=tmp_path / "data",
                           components=("golden",), runner=stub,
                           no_record=True, incumbent=incumbent)


def test_a_verdict_flip_refuses_after_one_rerun(tmp_path: Path) -> None:
    inc = _incumbent(tmp_path)
    stub = _Stub(candidate=[1, 1], incumbent=0)
    result = _run(tmp_path, stub, inc)
    g = _golden(result)
    assert result["verdict"] == "fail"
    assert g["ok"] is False
    assert g["refusal"] == "verdict flip: incumbent passes, candidate fails twice"
    assert g["incumbent"]["ok"] is True
    assert g["incumbent"]["skills_from"] == str(inc)
    assert len(stub.calls) == 3  # candidate, incumbent, candidate re-run
    assert stub.calls[1][-2:] == ["--skills-from", str(inc)]


def test_a_flip_that_clears_on_the_rerun_passes(tmp_path: Path) -> None:
    stub = _Stub(candidate=[1, 0], incumbent=0)
    result = _run(tmp_path, stub, _incumbent(tmp_path))
    g = _golden(result)
    assert result["verdict"] == "pass"
    assert g["note"] == "verdict flip cleared on one re-run"
    assert g["candidate_retry"]["ok"] is True


def test_both_failing_is_a_failure_without_a_rerun(tmp_path: Path) -> None:
    stub = _Stub(candidate=[1], incumbent=1)
    result = _run(tmp_path, stub, _incumbent(tmp_path))
    g = _golden(result)
    assert result["verdict"] == "fail"
    assert "refusal" not in g and "candidate_retry" not in g
    assert len(stub.calls) == 2


def test_a_slower_candidate_is_flagged_not_failed(tmp_path: Path) -> None:
    stub = _Stub(candidate=[0], incumbent=0, candidate_sleep=0.3, incumbent_sleep=0.05)
    result = _run(tmp_path, stub, _incumbent(tmp_path))
    g = _golden(result)
    assert result["verdict"] == "pass"
    assert g["time_flag"] is True
    assert g["golden"]["verdict"] == "pass"


def test_a_comparable_candidate_is_not_flagged(tmp_path: Path) -> None:
    stub = _Stub(candidate=[0], incumbent=0, candidate_sleep=0.05, incumbent_sleep=0.3)
    g = _golden(_run(tmp_path, stub, _incumbent(tmp_path)))
    assert g["time_flag"] is False


def test_no_incumbent_is_recorded_and_changes_nothing(tmp_path: Path) -> None:
    stub = _Stub(candidate=[0], incumbent=0)
    g = _golden(_run(tmp_path, stub, None))
    assert g["ok"] is True
    assert g["incumbent"] == {"status": "unavailable"}
    assert len(stub.calls) == 1


def test_an_injected_runner_never_auto_compares_against_the_real_release(tmp_path: Path) -> None:
    stub = _Stub(candidate=[0], incumbent=0)
    g = _golden(_run(tmp_path, stub, "auto"))
    assert g["incumbent"] == {"status": "unavailable"}
    assert len(stub.calls) == 1


@pytest.mark.parametrize("case", ["missing", "no-runner", "is-project"])
def test_resolve_incumbent_refuses_what_it_cannot_run(tmp_path: Path, monkeypatch, case) -> None:
    project = _project(tmp_path)
    if case == "missing":
        target = tmp_path / "nope"
    elif case == "no-runner":
        target = tmp_path / "empty"
        target.mkdir()
    else:
        target = project
        runner = project / "skills" / "ilk-loop" / "scripts" / "run_ilk_loop_claude.sh"
        runner.parent.mkdir(parents=True)
        runner.write_text("#!/bin/bash\n")
    monkeypatch.setenv("ILK_GOLDEN_INCUMBENT", str(target))
    assert safety_case._resolve_incumbent(project) is None


def test_resolve_incumbent_follows_the_current_symlink(tmp_path: Path, monkeypatch) -> None:
    inc = _incumbent(tmp_path)
    link = tmp_path / "current"
    link.symlink_to(inc)
    monkeypatch.setenv("ILK_GOLDEN_INCUMBENT", str(link))
    assert safety_case._resolve_incumbent(_project(tmp_path)) == inc.resolve()


# ── Sealed held-out slice (RSI design point 2) ─────────────────────────────


def _teeth_stub(outcomes: list[str], rc: int | None = None):
    calls: list[list[str]] = []

    def stub(cmd, **kw):
        calls.append(list(cmd))
        out = json.dumps({"verdict": "pass" if all(o == "caught-red" for o in outcomes) else "fail",
                          "results": [{"id": f"secret-{i}", "outcome": o, "output": f"secret-{i} FAILED"}
                                      for i, o in enumerate(outcomes)]})
        code = rc if rc is not None else (0 if all(o == "caught-red" for o in outcomes) else 1)
        return type("R", (), {"returncode": code, "stdout": out, "stderr": "secret tail"})()
    stub.calls = calls
    return stub


def _sealed(tmp_path, stub, catalog):
    result = safety_case.run(_project(tmp_path), data_dir=tmp_path / "data",
                             components=("sealed",), runner=stub, no_record=True,
                             incumbent=None, sealed_catalog=catalog)
    return result, result["components"][0]


def _catalog(tmp_path: Path) -> Path:
    c = tmp_path / "sealed" / "mutations.json"
    c.parent.mkdir()
    c.write_text('{"schema": 1, "mutations": []}')
    return c


def test_every_sealed_case_caught_passes_with_a_count_only(tmp_path: Path) -> None:
    cat = _catalog(tmp_path)
    stub = _teeth_stub(["caught-red"] * 4)
    result, c = _sealed(tmp_path, stub, cat)
    assert result["verdict"] == "pass"
    assert c["sealed"] == {"total": 4, "caught": 4}
    assert stub.calls[0][-2:] == ["--catalog", str(cat)]
    # Nothing that names a case survives into the result.
    assert "secret" not in json.dumps(result)
    assert str(cat) not in json.dumps(result)


def test_a_surviving_sealed_case_refuses(tmp_path: Path) -> None:
    result, c = _sealed(tmp_path, _teeth_stub(["caught-red", "survived", "caught-red"]),
                        _catalog(tmp_path))
    assert result["verdict"] == "fail"
    assert c["sealed"] == {"total": 3, "caught": 2}
    assert "secret" not in json.dumps(result)


def test_unparseable_teeth_output_refuses(tmp_path: Path) -> None:
    def stub(cmd, **kw):
        return type("R", (), {"returncode": 0, "stdout": "not json", "stderr": ""})()
    result, c = _sealed(tmp_path, stub, _catalog(tmp_path))
    assert result["verdict"] == "fail"
    assert c["sealed"] == {"total": None, "caught": None}


def test_an_empty_sealed_result_refuses(tmp_path: Path) -> None:
    result, c = _sealed(tmp_path, _teeth_stub([]), _catalog(tmp_path))
    assert result["verdict"] == "fail"


def test_an_absent_sealed_catalog_is_recorded_not_run(tmp_path: Path) -> None:
    stub = _teeth_stub(["caught-red"])
    result, c = _sealed(tmp_path, stub, tmp_path / "nope.json")
    assert result["verdict"] == "pass"
    assert c["sealed"] == {"status": "absent"}
    assert stub.calls == []


def test_an_injected_runner_never_reads_the_real_sealed_slice(tmp_path: Path) -> None:
    stub = _teeth_stub(["caught-red"])
    result, c = _sealed(tmp_path, stub, "auto")
    assert c["sealed"] == {"status": "absent"}
    assert stub.calls == []


def test_the_default_run_includes_the_sealed_slice() -> None:
    import inspect
    default = inspect.signature(safety_case.run).parameters["components"].default
    assert "sealed" in default
