# Lessons for coordinating running batches

- Before interrupting a running batch to incorporate additional work, decide
  whether it can be appended as a new subplan. Prefer append-only when it meets
  the request and leaves dispatched steps, gates, and dependencies unchanged.
- Do not park or stop a batch merely to append future work. Check the actual
  coordination requirements first; interrupt only when a concrete conflict
  requires it, such as changing the dispatched worker's plan or gates.
- Choose the smallest sufficient operation before changing runtime state.
  Avoid unnecessary park → stop → unpark cycles: they waste time and discard
  progress. If the approach changes after an interruption, explain that
  honestly rather than presenting the interruption as necessary.

Recorded after the 2026-10-11 batch amendment: appending subplan #7 was
sufficient, so the initial park/stop cycle was unnecessary.

# Verification preference (operator, 2026-10-11)

For now, omit the final verification subplan from new batches. Keep
`ship.verification_subplan: optional` and retain focused step gates and relevant
caller tests. Do not schedule full-suite runs by default or claim full-suite
verification from scoped results. If concrete evidence makes broader testing
necessary (for example, an unresolved regression spanning components that
scoped checks cannot cover), explain the specific reason to the operator
before changing this approach. Do not interrupt a running batch to apply this
preference.


# Kernel ownership (operator, 2026-10-11)

Every new loop-built kernel change, including session-planned work, belongs
in a supervised owner build. Expanded landing paths include bounce_daemons.sh,
install.sh and host_deploy_status.py. Template/report tools remain non-kernel.
Policy acceptance does not mean runtime enforcement has been migrated.
Do not interrupt or rewrite an already dispatched step to adopt this policy.


# Active manual implementation

Before continuing loop execution/ownership/performance work, read
`docs/implementation/loop-execution-improvement-plan.md`. It tracks the full
authorized manual scope, evidence and exact next action. Use its isolated owner
checkout; do not reconstruct the plan from chat or overwrite active worker work.
Update the tracker after each checked increment and before handing off.
