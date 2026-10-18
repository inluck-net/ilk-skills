Generate a postmortem for an ilk2 batch — its labels, its numbers, and what
to change next time — then optionally record those findings through
`ilk2 improvement add`.

This is the **ilk2-native** postmortem command, built beside `/ilk-feedback`
until the switch (docs/07 §7.4.3). It reads only ilk2's versioned JSON
(`ilk2 report`, `ilk2 status`, `ilk2 events`) via
`<skill-root>/ilk2-feedback/scripts/postmortem.py`. It never reads V1's run
files or ilk2's run directories, and it never launches, restarts, resumes or
cancels anything: apart from the postmortem file it writes (and, only on your
go-ahead, the improvement rows it files), it is read-only.

The script does the measurable part — runs the three JSON commands, classifies
the batch against the kept subset of V1's taxonomy, and writes
`~/.ilk2/postmortems/<project>/<batch>.md`. What it cannot do is *judge*: what
those labels mean for this particular batch, and what to change in the next
plan. That is this command's job.

Follow these steps in order. Do NOT skip the review in step 4 — a postmortem
that only restates the script's output has not earned its keep.

## 1. Resolve the batch

- If the user named a batch id after the command, use it.
- Otherwise resolve the project (walk up from the cwd to the git root) and run
  `ilk2 status --json`. Among the batches listed for this project, take the
  most recent one: latest `shipped_at` where the batch shipped, otherwise the
  last entry the status command lists. If the two disagree, say which rule you
  used.
- If there is no batch for this project, say so with the search space ("0
  batches for `<project>` in `ilk2 status --json`") and stop. **Do not invent
  a batch id**, and do not fall back to a different project's batch.

## 2. Generate the postmortem

```bash
python3 "<skill-root>/ilk2-feedback/scripts/postmortem.py" <batch-id> --write
```

`--write` prints the Markdown and saves it under
`~/.ilk2/postmortems/<project>/<batch>.md` (overwritten if you run it again).
The script exits non-zero if `ilk2` itself failed — report that and stop; do
not reconstruct the report by hand.

Useful flags:

| Flag | Effect |
|---|---|
| `--ilk2 PATH` | use a specific `ilk2` executable |
| `--findings` | print the `ilk2 improvement add` argvs one per failure label (runs nothing) |
| `--apply` | with `--findings`, actually run them |

## 3. Read the report back

Open the file that step 2 wrote and render it in chat, in this order:

- **Labels and their evidence** — the classification, with the step/attempt
  strings the report cites. Keep the evidence; it is what makes the label
  checkable.
- **The numbers** — `minutes_per_accepted_step`, `first_attempt_share`, the
  per-step table (minutes, attempts, red, first edit, long requests and the
  longest request where they are known), and the phase minutes.
- **Refusals and submit warnings**, if any.

**Null is unknown, never zero.** Attempts recorded before report_version 2
carry null request and timing fields. Quote `unknown` as `unknown`; never
write `0` in its place, and never average across a null as if it were a zero.

## 4. Add the judgement the script cannot

For each non-`clean` label, say what it means **for this batch**, then what to
change next time. Ground every claim in a number from the report — a step's
attempts × minutes, a gate that stayed red, a first-edit minute. An unnumbered
recommendation is an opinion.

| Label | What to look at | What to change |
|---|---|---|
| `timeout-bound` | which attempt hit the cap, and what it had already edited | **Step size** — a step that eats a whole attempt window is too big. Split it. |
| `stuck-no-progress` | the judged attempt that ended red with an empty edit map | **Plan shape** — the step said what to build but not how to know it worked. Sharpen the acceptance criteria, or split until a step is checkable. |
| `api-flaky` / `api-blocked` | how many attempts died on `api`, and whether the batch blocked on one | Upstream, not the plan. Say so plainly before recommending plan edits. |
| `quota-exhausted` | a `quota` attempt or termination | Provider quota. Distinct from a budget cap — do not recommend raising a budget. |
| `interrupted` | which attempt was interrupted and when | Stopped from outside. Report what was carried; recommend nothing unless the interruption repeats. |
| `local-checks-stuck` | the gate name + `command_digest` red on 3+ attempts of one step | **Gates** — the same check failing across attempts means the acceptance criterion is wrong or over-specified, the step is too coarse, or there is a real bug. Read the gate's attributed tests before recommending a relaunch. |
| `clean` | — | Nothing to change. Say so; do not invent a finding. |

Then, across the batch as a whole, check the three plan levers the toolkit
actually measures (docs/07 §7.4.2):

- **Step size** — a step is about 25-30 worker-minutes, at most 6 files of
  scope and 12,000 characters of plan text. Compare with the per-step minutes
  and attempts in the report.
- **Plan shape** — dependencies only where scopes overlap or behaviour is
  required; a serial chain of disjoint steps is work lanes could have run.
- **Gates** — red-first, one gate per real risk, and a `command_digest` that
  stays stable so a repeated red is visible as the same gate (which is exactly
  what `local-checks-stuck` counts).

## 5. Offer to record the findings

If the judgement produced something worth carrying into the toolkit, offer
`--findings`:

```bash
python3 "<skill-root>/ilk2-feedback/scripts/postmortem.py" <batch-id> --findings
```

That prints one `ilk2 improvement add --project <project> --source postmortem
--fingerprint <label>:<project> …` argv per non-`clean` label and runs
nothing. Show the user the argvs. Only if they say to record them, re-run with
`--apply` (or run the argvs yourself).

**Rows for project `ilk2` reach ilk2's RSI only after a person marks them
`accepted`** (docs/07 D14) — filing one is not an instruction to the
self-improvement loop. Tell the user that when you offer.

## Boundary rules

- **Read-only except for two things**: the postmortem under
  `~/.ilk2/postmortems/`, and (only on a yes) improvement rows via
  `ilk2 improvement add`. Nothing else is written.
- **Never launch, restart, resume, park or cancel anything.** This command
  reports on a batch; it does not operate the engine. Relaunching is a
  separate decision the user makes.
- **One input only: ilk2's versioned JSON.** The three `ilk2 … --json`
  commands are the whole search space. Do not go looking for V1 run files, for
  a JSONL summary log, or for per-iteration text logs — none of them describe
  an ilk2 batch, and the ones that remain on disk are being retired (docs/07
  §7.4.6).
- **Don't fabricate fields.** A null request or timing count is unknown. A
  `0` written in its place is a measurement nobody made.
- **V1's labels this command does not produce.** `shipped-unverified`,
  `self-hosting-drift` and the selfmod merge labels (`merge-conflict`,
  `merge-deferred`) are V1 taxonomy for V1's runner and watchdog. They do not
  apply to ilk2 batches — do not look for them, do not map a case onto them,
  and do not report their absence as a gap. Same for `budget-exhausted`:
  `quota-exhausted` is the kept label and the two are deliberately distinct.

## See also

- `<skill-root>/ilk2-feedback/scripts/postmortem.py` — `collect` / `render` /
  `write` / `findings`, and the frontmatter fields this command quotes.
- `<skill-root>/ilk2-feedback/scripts/classify.py` — the label subset and the
  JSON-only rule for each one.
- `/ilk2-plan` — where the plan-shape and step-size advice lands next time.
- `/ilk-feedback` — the V1 command this one sits beside; it classifies V1
  runs from V1's records, which is a different question.
