# Worker execution costs

Run `python3 tools/loop-performance/report.py <iter-NN.log.jsonl> ... --output report.json`.
This reads existing CLI streams and sibling renderer logs. It never changes plans,
worker settings, gate results or proof. New POSIX runner summaries include the
same report in the additive `worker_cost_observation` field when the tool exists.
Older releases without it continue to write their usual records.

Actual init model identifies the provider model. Unique root message IDs count
turns; `root_assistant_events` also counts split assistant emissions (the old
manual measurements counted those events). Subagent events are excluded. Tool
calls use unique IDs. Foreground test seconds union timestamped tool-use/result
intervals, including shell overhead; missing pairs remain unknown. Bootstrap
path matches are indicators, not an estimate of waste. Thinking uses completed
renderer blocks, excluding progress lines. Compaction duration comes from CLI
metadata. API/parallel/thinking clocks overlap: never subtract them to invent
"tool time" or sum them into elapsed. Interrupted results remain incomplete.

The reporter emits counts/identities, not raw tool commands, code or credentials.
A broad command-pattern match is an observation, not proof that tests executed
successfully. Driver gates remain the verdict.

`effort_probe.py --worker-home <home> --output <report> --timeout <seconds> --pairs <count>`
runs an isolated, tools-disabled synthetic task on the explicitly configured
provider/model. It strips ambient provider settings, disables hooks and session
persistence, and deletes temporary homes. The named real worker home is read-only.
Timeout and sample size must be chosen explicitly; they are experiment judgments,
not batch defaults. CLI flag acceptance and tiny-task speed do not establish
provider support or full-step acceptance. The October 11 two-pair probe varied
in both directions and reported per-turn effort inactive; no default was changed.

Deployment: this owner branch is a candidate, not a released toolkit. Keep
active workers pinned. Reconcile the existing batch's pending context plan
before landing; do not enqueue competing runner changes. A future installation
and a comparable accepted consumer step are needed before claiming a speedup.
