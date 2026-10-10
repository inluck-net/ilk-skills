AFTER your step commit, run the declared gate and iterate until green:
{gate_cmds}

Rules:
- Run every command above after your commit.
- While any is red: fix the code (or fix a test-infra fault), commit with 'test-infra:' in the body if fixing a test, then rerun.
- End your turn ONLY when all are green.
- If green is not reachable, end your turn with the failing ids written to the sub-plan's Findings section.
