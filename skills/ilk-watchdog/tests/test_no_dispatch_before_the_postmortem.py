"""Pin that the scheduler waits for a failed run's postmortem (ilk-skills #30).

gh-resolve 2026-10-09: run 20261009-212756 ended local_checks_failed at
22:21:38. The scheduler dispatched again at 22:23:27, but the watchdog wrote
the postmortem (local-checks-stuck, which blacklists) at 22:23:28. The
relaunch hit the same red. The same 1 s margin happened on 2026-09-14.

AC-1 (the real case): sentinel local_checks_failed, ended 22:21:38, no
      postmortem, now 22:23:27 => awaiting, naming the run.
AC-2: the postmortem file exists => not awaiting.
AC-3: the grace window has passed (a run that never gets a postmortem)
      => not awaiting, so it cannot wedge the project.
AC-4: a success or running sentinel => not awaiting.
AC-5: the CLI prints {"awaiting": true, ...} for AC-1.
AC-6: scheduler.sh checks it before the blacklist skip in the dispatch
      loop, and logs skip-awaiting-postmortem.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

TZ = dt.timezone(dt.timedelta(hours=8))


def _project(tmp_path: Path, state: str, ended: str | None = "2026-10-09T22:21:38+0800",
             with_pm: bool = False) -> Path:
    d = tmp_path / "projects" / "k"
    (d / "runtime" / "launcher" / "postmortems").mkdir(parents=True)
    sentinel = {"state": state, "run_id": "20261009-212756"}
    if ended:
        sentinel["ended_at"] = ended
    (d / "runtime" / "launcher" / "last-exit.json").write_text(json.dumps(sentinel))
    if with_pm:
        (d / "runtime" / "launcher" / "postmortems" / "20261009-212756.md").write_text(
            '---\nclassification: "local-checks-stuck"\n---\n')
    return d


NOW = dt.datetime(2026, 10, 9, 22, 23, 27, tzinfo=TZ)


def test_ac1_a_failed_run_without_its_postmortem_is_awaited(tmp_path):
    from blacklist_status import awaiting_postmortem
    r = awaiting_postmortem(_project(tmp_path, "local_checks_failed"), now=NOW)
    assert r is not None and r["run_id"] == "20261009-212756"


def test_ac2_the_postmortem_ends_the_wait(tmp_path):
    from blacklist_status import awaiting_postmortem
    d = _project(tmp_path, "local_checks_failed", with_pm=True)
    assert awaiting_postmortem(d, now=NOW) is None


def test_ac3_the_grace_window_bounds_the_wait(tmp_path):
    from blacklist_status import awaiting_postmortem, POSTMORTEM_GRACE_S
    d = _project(tmp_path, "local_checks_failed")
    later = dt.datetime(2026, 10, 9, 22, 21, 38, tzinfo=TZ) + dt.timedelta(
        seconds=POSTMORTEM_GRACE_S)
    assert awaiting_postmortem(d, now=later) is None


def test_ac4_success_and_running_are_never_awaited(tmp_path):
    from blacklist_status import awaiting_postmortem
    assert awaiting_postmortem(_project(tmp_path / "a", "all-shipped"), now=NOW) is None
    assert awaiting_postmortem(_project(tmp_path / "b", "running", ended=None), now=NOW) is None


def test_ac5_the_cli_reports_it(tmp_path):
    d = _project(tmp_path, "local_checks_failed",
                 ended=dt.datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z"))
    out = subprocess.run([sys.executable, str(SCRIPTS / "blacklist_status.py"),
                          "awaiting", "--project", str(d)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["awaiting"] is True and payload["run_id"] == "20261009-212756"


def test_ac6_the_scheduler_waits_before_the_blacklist_skip():
    text = (SCRIPTS / "scheduler.sh").read_text(encoding="utf-8")
    wait = text.index('blacklist_status.py" awaiting --project "$path"')
    blacklist = text.index("# blacklist / backoff skip")
    assert wait < blacklist
    assert '"skip-awaiting-postmortem"' in text[wait:blacklist]
