# Consumer step profile observations — 2026-10-11

These are disposable gh-resolve consumer checks, not a release baseline or a
controlled speed comparison. Installed source repos, production claims/daemons
and the paused toolkit worker were not changed. [CSV](consumer-step-profile-baseline-2026-10-11.csv)
keeps the numbers and their parser version for later comparison.

## Task and inputs

Consumer source `9c24a89c`: recognize `upgrade_request_invalid` with the same
unfinished-work guards as `upgrade-drained`, editing only reap.py/reconcile.py.
A committed per-worktree/shared-key regression fixture was independently red
before each launch. The authoritative gate selected that fixture plus
`test_toolkit_upgrade_drain.py`; it was not a full suite. The model was actual
`mimo-v2.6-pro` in every CLI init, using eight copied production hooks and the
opt-in explicit-disabled thinking adapter. No model/default change was made.

| Sample / toolkit | Tools | Fixture wall | Completed CLI | Step accepted | Run completed |
|---|---|---:|---:|---|---|
| Original / `357ddc1c` | Shell | 123.071s | unknown | No | No |
| Explicit execution-root instructions / `0657df35` | Shell | 123.226s | unknown | No | No |
| Revised task bound / `0657df35` | Shell | 243.227s | unknown | No; partial WIP retained | No |
| Native tools / `8c884dc1` | Native + shell | 300.038s | 135.569s | Yes | No; unintended automatic batch gate |
| Accepted consumer resume / `bec678df` | No model call | 1.320s | zero invocations | Prior closed acceptance retained | Yes; already-shipped |

Fixture wall is parent monotonic elapsed including preparation after spawn,
shutdown and observation; the native outer bound interrupted the unwanted
batch gate. Completed CLI is result.duration_ms, not fixture wall. The resumed
run is separate; do not invent an end-to-end runtime by adding its wall to the
old CLI duration. The positive original close records a 144s iteration duration.

The 120s iteration / 180s outer fixture bounds were judgments based on earlier
provider capability calls around 20s. Two real consumer samples falsified their
allowance for discovery. The fresh 240s iteration / 300s outer bound was a
judgment based on those 14-call discovery spans. Another valid timeout would
falsify that bound. Neither changed production limits or renewed an existing run.
The native model completed inside 240s; the 300s failure was after acceptance.

## Measured split and limitations

Native sample: CLI-reported num_turns **31**; parser-v5 unique root assistant
message IDs **24**; **30** unique root tool IDs (10 Bash, 3 Grep, 13 Read,
4 Edit). These denominators differ and must not be called the same turn count.
The first native Edit started **73.755s** after the first timestamped root tool.
Bash edits are not inferred; the earlier WIP edit therefore stays unobserved
by this first-edit metric even though Git proves it exists.

Native completed CLI API clock **125.771s** overlaps CLI wall. Worker diagnostic
foreground tests occupy a matched **7.050s** wall union; these are separate from
the authoritative gate. Completed renderer thinking time is **unknown**, not
zero: no thinking blocks were observed under the explicit-disabled profile.
No clocks are subtracted to invent exclusive model thinking time.

The authoritative driver child gate took **0.750s**, with 12 tests passing in
**0.510s** pytest-reported time. Shared-host monotonic driver phase intervals:
gate **0.951s**, commit/hooks **0.265s**, integrity/audit **0.405s**, owned cleanup
**0.164s**; landing is not applicable for this ordinary consumer. These intervals
may overlap and omit preparation, so are not a summed run decomposition.

Parser v4's bootstrap path indicator counted all 30 native calls because the
fixture root was beneath `.ilk-data`. Parser v5 excludes native writes and
normalizes only the supplied frozen execution-root prefix: **8** indicators,
with raw **30** retained. This remains a path-pattern indicator, not proof of
wasted bootstrap. Existing v4 reports remain beside separate v5 reports.

The native sample has a positive accepted commit/CAS and closed integrity/audit;
source worker settings still match their captured hash. Its model session made
no tool errors. The shell 240s sample made 38 calls and encountered malformed
wrapper quoting/protected-log patch attempts; its WIP commit is diagnostic,
never green proof. Native tools have feasibility evidence on this task, not a
general default recommendation. Runs were not randomized paired samples,
toolkit pins and path spellings differ, and host concurrency was not controlled.

## Pipeline defect and correction

The native run triggered the runner's old unconditional batch-end suite because
the cloned consumer config had no optional waiver. This unintended isolated
suite launch is not a verification claim. At the fixture bound the run was
stopped; registry is idle and its recorded batch-gate PID is dead.

`bec678df` defaults automatic final verification off. Only explicit
`--run-batch-gate` enables it; launchers and drain/relaunch retain that choice.
A harmless marker reproduced the old automatic invocation (1 failed check in
6.79s); four archived default/native/explicit-opt-in/drain callers passed in
44.21s after the fix, with no full-suite command. Six gate reporting/drain
checks passed in 0.78s. Already accepted consumer state then resumed in 1.320s,
zero provider streams, terminal already-shipped, registry idle, no final suite.

Raw evidence is external under
`~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/runtime/analysis/design-revision-20261011/consumer-provider-*`:
per-sample summary, original stream/renderer, v4/v5 cost reports, native gate
capture and resume-summary. The owner implementation tracker retains exact
checkpoint/evidence distinctions. Representative alternating comparisons and
production installation are still open; no batch speedup is claimed.
