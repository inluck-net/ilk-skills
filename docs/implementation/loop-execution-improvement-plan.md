# ilk-skills delivery tracker — D1/D2 complete

Completed 2026-10-12T23:12:19+08:00. Chad assigned D1 and D2 to this owner. D3 remains deferred and none of its nine rows was started. Detailed dated evidence remains in `~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/runtime/analysis/design-revision-20261011/owner-readiness-report-20261012.md` and Git history.

## Definition of done

- [x] D1: ilk-skills 11b and gh-resolve 10c masters shipped through loop-owned commits, unattended on v0.9.194 or later, without owner edits to progress state.
- [x] D2: a real two-step code-only trial qualified against the predeclared default comparator; code-only became the managed-Mac default on both hosts; a genuine live batch then accepted and shipped a step on that released default.
- [ ] D3: deferred by Chad; do not start without new scope.

## Delivered evidence

- 11b master: `status: shipped`; loop ship commit `54893916`. Its old terminal audit reported stale shared verdict evidence, retained as a reporting limitation.
- 10c master: `status: shipped`; final driver acceptance `d55f1ac2`, loop ship commit `4cb3a82e`, pushed to `origin/main`. No final verification sub-plan ran and no owner progress-state edit was made.
- 10c inherited gate repair: side-ref commit `ed7ecbc9`; the 36 previously failing nodes plus the real-corpus prerequisite passed 37/37 under the isolated gate environment, with liveness/pin checks 14/14. No full suite ran.
- D2 controlled attempts: attempt 1 used 3,602 driver seconds and accepted 0 steps; attempt 2 accepted `bb8b46c3` and `460c03d0` in 2,746 + 1,384 seconds. All-attempt cost was 7,732 / 2 = 64.43 minutes per accepted step versus the predeclared comparable default 9,377 / 2 = 78.14 minutes.
- Released default: v0.9.197 enabled qualified code-only/native execution for managed Macs; v0.9.198 (`d17ffdcd`) added exact-owner late reservation binding. Both releases were installed and verified on this Mac and rezmac. Current on both hosts is v0.9.198; rezmac `host-compat.json` remains hash `480c287bf2000016e62541da8eb57ec0b95a2d25d806e7de69497b16dc96adf0`.
- Genuine released-default proof: 10c run `20261012-222753` used v0.9.198 code-only default, passed its authored driver gates, accepted step 1, and wrote the loop-owned step/ship commits above.

## Measured limitations

- 10c ended `shipped-unproven` even though its final driver gate passed and the loop wrote the ship commit. The terminal proof lookup reports all post-state-commit trees as stale or missing. This is an integrity-reporting defect; no proof was manufactured and no full suite was run.
- The 1M context declaration improved observed headroom but does not prove a full effective 1M window. The frozen v0.9.194 run compacted near 167–168k input/cache tokens; the qualified trial reached about 242k without compaction.
- Actual hands-on time was not continuously instrumented. Do not substitute elapsed wall time for hands-on time.

## Timing against the accepted estimate

- D1 continuation: 14:39:30 to 23:12:19 = 8.55 hours, slightly above the 5–8 hour estimate.
- 11b ship to v0.9.195 verified on both hosts: 15:45:23 to 16:34:00 = 0.81 hours versus the 2–3 hour merge/release estimate.
- D2 first trial launch to genuine live-default acceptance: 16:40:54 to 23:06:00 = 6.42 elapsed hours. This is calendar time; hands-on actual remains unknown.

## Operating boundary

Keep code-only as the managed-Mac default. Preserve explicit legacy/opt-out paths and fail closed when required native ownership probes are unavailable. Use focused tests only for follow-up defects. D3 remains outside this delivery.
