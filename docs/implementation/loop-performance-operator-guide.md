# Operating the loop performance preview

This is a local preview, not a published release. The durable
[implementation tracker](loop-execution-improvement-plan.md) records the
installed commit, checked consumer version, measurements and remaining work.
The supported owned profile is macOS with a clean entry in one repository,
including checked fresh selfmod and declared-worktree acceptance. Windows,
member-repository and dirty-entry owned adapters remain unavailable; use the
existing legacy workflow for those cases. Legacy fallback retains legacy
worker authority.

## What changes without selecting a new profile

The driver supplies an exact dispatch brief and adds context, provider origin,
worker-cost and phase observations to existing records. Final verification is
opt-in. Required step gates still run; no prior gate result substitutes for a
current measurement. Defaults for model, thinking, tool profile and worker
authority are unchanged.

## Selecting the supported owned profile for a new launch

Use the installed launcher after the project's current run has reached its
normal boundary. Choose the existing master, budget and iteration limits as
usual. Set the following environment only for that launch:

```bash
ILK_WORKER_MODE=code-only ILK_WORKER_TOOLS=native \
  bash "$HOME/.codex/skills/ilk-launcher/scripts/launch.sh" \
  --project-path "/absolute/path/to/project" \
  --engine claude-worker --run-local-checks
```

The detached command carries the selected authority explicitly; it does not
depend on a new Terminal inheriting the calling shell's environment. The
launcher prepares private, committed toolkit code and copied worker inputs.
The worker edits permitted code. The driver runs gates, commits the accepted
diff and advances one step. A held or changed contract refuses acceptance.

Native tools are an explicit profile, not a faster-model default. For the
checked MiMo PRO endpoint/model only, adding
`ILK_WORKER_THINKING=disabled` selects the measured provider adapter. CLI
effort flags alone did not establish thinking-off. Unsupported provider/model
combinations refuse this selection. Existing credentials remain private;
public records identify the configured origin rather than the full URL.

Do not add `--run-batch-gate` unless final verification is wanted. Choosing
the owned profile does not add a verification subplan or run a baseline suite.
Use the project's declared focused gates.

## Recovery and upgrades

Logs remain under the existing project data root. A close record must carry
positive acceptance, integrity, explained state and any required landing.
Open records and worker reports cannot establish shipment.

Red output, attempt IDs, input fingerprints and interrupted transcripts are
diagnostics. Same-input mixed results remain flaky; unmeasured results remain
inconclusive. Changed code receives fresh gates. Corrupt history refuses rather
than resetting retry budgets. An inconclusive pre-dispatch gate spends no
provider turn and produces no green proof, strike, rollback or quarantine.

The coordinated drain mechanism is separately opt-in. It requires an unused
prepared input snapshot, exact run/master identity and compatible consumer
readers. It drains only after accepted gates, state, integrity, landing and
joined child cleanup, then relinquishes the old owner/lock before relaunch.
An unfinished gh-resolve claim is preserved. It does not renew the batch's
budget or change its master. There is no supported hot edit of worker settings
or replacement of active run inputs.

## Reading the performance evidence

Compare accepted work with the same task, model, inputs and gate. Keep wall
time, worker turns, test intervals, observed thinking and driver phases
separate: clocks overlap, and missing thinking is unknown. The measured
consumer brief pair was 229.799s versus 199.194s, with acceptance on both.
One unrandomized pair under uncontrolled host concurrency does not establish
a general speed improvement. Retained failed runs remain failed.

This preview does not implement gate-outcome reuse or automatic stall killing.
Broader legacy verdict/ownership migration and platform rollout remain tracked;
supported Mac evidence does not establish whole-design completion.
