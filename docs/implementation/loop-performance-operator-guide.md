# Operating the loop performance preview

This is a local preview, not a published release. The durable
[implementation tracker](loop-execution-improvement-plan.md) records the
installed commit, checked consumer version, measurements and remaining work.
The supported owned profile is macOS with a clean entry in one repository,
including checked fresh selfmod and declared-worktree acceptance and upgrades. Windows,
member-repository and arbitrary/timeout dirty-entry owned adapters remain unavailable; use the
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


An exact retained red diff can resume on the owned Mac profile after a prior
`local_checks_failed` launch. This is new local-preview behavior checked on
2026-10-12, not a public release. The latest durable close must show a known red
gate on the exact unchanged diff, positive state audit, unchanged base/input
identity and plan state, and no live prior runner or registered child group.
Preparation checks this before starting a provider. The context retains a
diagnostic ownership receipt; fresh gates still run and retry counts persist.
An additional hand edit, changed toolkit/input, missing close, timeout with
unobserved work, or unknown state requires owner handling and cannot silently
be attributed to the previous worker. Accepted-transaction recovery continues
to use its separate original transaction adapter.


A private toolkit pin permits an unrelated consumer batch to keep running
while future toolkit inputs are updated. It does not permit updating the
checkout that a batch is editing or will land into. The registry host guard
checks both cloned and execution roots for overlap with that update target.
Leave a working batch alone; activate at its checked accepted/idle boundary.


Unattended results are driver output. Their absolute path must stay outside
both the source project and execution worktree, including symlink aliases;
invalid paths refuse before parent-directory creation. The frozen context
checks this again against its resolved roots. A result reader with the existing
close log reports its exact recorded run range; missing/conflicting capture
stays unknown. A current Git parent is not a run baseline.


During a frozen iteration, status uses the recorded master, plans directory,
runtime and execution root. It cannot switch to another queue candidate through
an ambient master setting. Invalid or missing frozen identity returns an error;
status outside an iteration retains existing queue selection.


## Opt-in progress with a measured unrelated red

For the supported owned Mac launch, `ILK_MEASURED_RED_PROGRESS=1` enables
fresh attribution of the selected focused gate. It measures failed node IDs
against the exact recorded batch and step bases within the existing deadline;
it does not obtain a full-suite baseline. Complete unchanged failures present
at the required bases can allow durable progress. New, mixed or unmeasured
failures refuse. Such progress stays `shipped-unproven`, with accepted:false
and green_accepted:false; consumers cannot treat it as green verification.
The actual disposable gh-resolve repair exercised this opt-in path with the
production provider and hooks. Final verification remains separately opt-in.

Prepared plain global instructions and rule files are explicit bootstrap reads
because owned print mode disables automatic setting sources. Repository
instruction references are inventoried with cycle detection. Protocol 2 mirrors
relative Markdown imports wholly within the source worker home, preserving
bytes, paths and cycles. Absolute/external imports, imported symlinks, missing
files and references into protected worker resources still refuse preparation.
Managed policy and external runtime upgrades still need explicit coordination;
private toolkit snapshots do not freeze host libraries or remote policy.

Multi-owner explicit verification red returns are recoverable per owner. An
interrupted operation recognizes each exact journaled plan revision and resumes
unfinished owners without duplicate checks or findings. Initially human-held
owners refuse before any revision. Human edits after a partial return require
owner handling; the operation does not claim atomic replacement across files.
This path is a recovery adapter, not permission to run final verification.


Declared-worktree upgrades preserve the existing absolute `work_tree` binding.
The driver rechecks the master declaration, Git indirection identity and common
Git store under the original deadline. Consumer guards carry both clone and
execution roots; the launcher retains the original master, limits and budget.
The full two-launch declared-worktree drain fixture passed in 29.83s. This does
not permit hot replacement of active workers or reusing a gate outcome.
