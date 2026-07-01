# Handoff → Mac agent: verify `.sh` steer-hook parity + fix `.sh` test Windows portability

> **For an agent running on macOS/Linux.** The `.sh` steer-hook parity (v0.9.41,
> commit `d2039b1`) was diagnosed from a Windows box — where the `.sh` runner
> isn't the real detached runner and Git Bash isn't a faithful POSIX env. This
> handoff captures that diagnosis and the two platform-specific tasks that need
> a real POSIX machine. Date: 2026-07-01. Author: Windows planner session.

## Background

`steer_hook.sh` + `.sh` runner wiring (`run_ilk_loop_claude.sh`) are the bash
parity of the PowerShell steer-hook shipped in the `loop-steer-and-vision` batch
(v0.9.40). The `.ps1` side is fully verified on Windows (510-test suite incl. a
mock-`claude` runtime harness). The `.sh` side was deferred to "a Mac-verified
batch" precisely because Windows can't runtime-gate detached bash.

## What I verified from Windows (and what I couldn't)

Running the two `.sh` test files on Windows Git Bash: **9 pass outright**
(the pure consume-protocol tests on `steer_hook.sh`), but **the runner-harness
tests and 2 crash-recovery tests fail**. I root-caused **both failure clusters
to Windows-Git-Bash environment artifacts — NOT defects in `steer_hook.sh`**:

1. **Runner-harness failures** — the mock `claude` in
   `test_steer_hook_runner_sh.py` logs via **`python3`**. On Windows `python3`
   resolves to the **Microsoft Store App-Execution-Alias stub**
   (`...\WindowsApps\python3`), which no-ops and returns 0 → the prompt log
   stays empty → "expected N prompts, got 0". **Proof:** with a real
   `python3`→`python` shim on PATH, the entire runner harness passes.

2. **Crash-recovery reconcile failures** (`test_steer_hook_sh.py::TestCrashRecovery::
   test_leftover_processing_already_consumed` and `..._mixed`) — the reconcile's
   `awk` step does `getline line < CONSUMED`, and **`awk` mangles Windows
   backslash paths** (`C:\Users\...` → `\U`,`\c`,`\A` treated as escape
   sequences), so it can't open `inbox.consumed.jsonl` → `seen[]` never
   populates → already-consumed entries get re-injected. **Proof:** same awk
   `getline` returns `count=1` on a forward-slash path and `count=0` on the
   backslash form of the same file. On POSIX (always forward-slash) this never
   triggers, and the `BEGIN{...seen[u]=1...}` / `if (uuid in seen) return` logic
   is correct.

**Conclusion:** the bash logic looks correct; I have high confidence it's green
on macOS/Linux. But I could not run it on a real POSIX box — that's task 1.

## Tasks for the Mac agent

### 1. Definitive parity verification (the reason a Mac is needed)
```bash
cd <ilk-skills>
python3 -m pytest skills/ilk-loop/tests/test_steer_hook_sh.py \
                  skills/ilk-loop/tests/test_steer_hook_runner_sh.py -v
```
- Expect **all green** on macOS. If anything FAILS on Mac, that IS a real bug in
  `steer_hook.sh` / the `.sh` wiring — fix it (the `.ps1` version in
  `steer_hook.ps1` + `test_steer_hook.py` is the reference behavior).

### 2. (Recommended) Real detached-runner smoke
- The harness *replicates* the wiring; the gold standard is a real detached
  `run_ilk_loop_claude.sh` iteration that sources `steer_hook.sh`, honors an
  `inbox.md` interjection once, and respects `pause.flag`. Confirm on Mac. This
  closes the deferred ".sh needs Mac verification" item from the
  `loop-steer-and-vision` batch.

### 3. Fix the `.sh` tests' Windows portability (they false-fail on Windows now)
The suite is meant to be cross-platform, but these two files currently red on
any Windows run — the v0.9.42 "make suite hermetic" pass hardened the `.ps1`
tests but missed these. Make them Windows-safe **and confirm still green on Mac**:
- `test_steer_hook_runner_sh.py`: mock `claude` should invoke **`sys.executable`**
  (or `python`) instead of the literal `python3`, so it doesn't hit the Windows
  Store stub.
- `test_steer_hook_sh.py` / the harness: either **skip on `sys.platform ==
  'win32'`** (simplest — the `.sh` runner is POSIX-only anyway), OR pass
  forward-slash / `cygpath`-normalized paths so `awk getline` works under Git
  Bash. Skip-on-win32 is the recommended minimum.

This is effectively an **escaped-bug** for the hermetic-hardening effort
(a suite that false-fails on a supported dev platform).

## Files
- `skills/ilk-loop/scripts/steer_hook.sh` — the hook (reconcile logic at
  `_steer_consume`, awk `BEGIN` loads `consumed.jsonl`).
- `skills/ilk-loop/scripts/run_ilk_loop_claude.sh` — the `.sh` runner wiring.
- `skills/ilk-loop/tests/test_steer_hook_sh.py` — consume-protocol tests.
- `skills/ilk-loop/tests/test_steer_hook_runner_sh.py` — runner harness (mock claude).

## Reference
- `.ps1` reference impl + tests: `steer_hook.ps1`, `test_steer_hook.py`,
  `test_steer_hook_runner.py` (all green on Windows).
- Contract: `../ilk-pocket/docs/handoffs/2026-07-01-ilk-loop-steer-hook.md`.

## Outcome (Mac agent, 2026-07-01)

- **Task 1 — DONE.** `test_steer_hook_sh.py` + `test_steer_hook_runner_sh.py`
  run on real macOS (darwin): **15/15 green**. Confirms the Windows diagnosis —
  the bash logic is correct; the Git-Bash failures were environment artifacts.
- **Task 3 — DONE.** Fixed the `.sh` tests' Windows portability:
  - mock `claude` now invokes `sys.executable` (not literal `python3`), so it
    can't hit the Windows Store app-alias stub.
  - both `.sh` test files now `skipif(sys.platform == "win32" or no bash)` — the
    `.sh` runner is POSIX-only, so Git Bash runs skip cleanly instead of
    false-failing (the awk backslash-path issue is thereby moot). Still 15/15
    green on Mac after the change.
- **Task 2 — NOT DONE (optional).** Real detached `run_ilk_loop_claude.sh`
  smoke (source `steer_hook.sh`, honor an `inbox.md` interjection once, respect
  `pause.flag`) not yet run — the harness replicates the wiring and Task 1
  passes, so this remains a recommended-but-not-blocking follow-up.
