AFTER your step commit, run the declared gate and iterate until green:
{gate_cmds}

Rules:
- Run every command above after your commit.
- Before claiming green, run the canonical inventory entry (`run_local_checks.py --print-gate-inventory --project <project> --slug <slug> --step <step>`) and run every check it names — it is the driver's inventory, including synthesized mention partitions, not just the declared gate above.
- While any is red: fix the code (or fix a test-infra fault), commit with 'test-infra:' in the body if fixing a test, then rerun.
- End your turn ONLY when all are green.
- If green is not reachable, end your turn with the failing ids written to the sub-plan's Findings section.
