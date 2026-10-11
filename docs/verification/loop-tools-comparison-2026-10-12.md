# Controlled shell/native toolkit repair

Actual-init model in both streams: **MiMo PRO, mimo-v2.6-pro**. Toolkit snapshot
093d67d6255a58b18381a9c953535670bc805720; task base
ab55826c0d95ba80e5364447e7b8d051b5e00809. Both repair the same deliberately
reverted exact-root normalization in the performance reporter, with the same
seven-test focused gate, eight copied hooks and explicit disabled thinking.
No baseline or final suite was run.

| Observed quantity | Shell profile | Native profile |
|---|---:|---:|
| Probe wall seconds | 242.845 | 104.744 |
| Accepted repair | No | Yes |
| Accepted driver gates | 0 | 1 |
| Pointer advanced | No | Yes |
| Unique root assistant messages (parser v6) | 32 | 9 |
| Root tool calls | 32 | 10 |
| Normalized bootstrap path indicators | 20 | 2 |
| Indicators before first native edit | 20; no native edit observed | 1 |
| First native-edit tool span seconds | Unknown | 41.986 |
| Foreground test-pattern calls | 3 | 2 |
| Timestamped foreground-test union seconds | Unknown; 1.719 lower bound | 1.611 |
| Completed thinking seconds | Unknown | Unknown |
| CLI interval seconds from accepted worker record | Unknown | 89.371228 |
| Accepted worker cost USD | Unknown | 0.200278 |
| Source worker settings unchanged | Yes | Yes |
| Owned registry idle at completion | Yes | Yes |

All quantities above come from these two actual-model runs; test-pattern calls
and bootstrap indicators are parser observations, not a measured waste verdict.
Shell writes through Bash do not establish the reporter's native first-edit
timestamp. The unmatched shell test interval makes its union unknown rather
than zero. Explicit thinking-disabled requests do not create a measurable
thinking-duration observation.

The shell run reached the original 240s iteration cap without a driver gate.
Its historical runner then made an ungated WIP commit and labeled unavailable
post-deadline audit observation as a ship-integrity violation. The retained raw
result stays failed. Subsequent owner correction c40e1ed0 prevents those two
actions; its verification is separate from this comparison. It does not turn
the failed run into acceptance.

This is one unrandomized pair. Host concurrency was uncontrolled, the native
probe began shortly before the shell probe's final cleanup, and source checkout
paths differ. Code snapshot, task/base, requested/observed model, hook count,
thinking profile and gate were held fixed. The successful native run supports
that opt-in profile for the checked Mac path. A time ratio against a failed
shell run is not a speedup for accepted work, nor evidence for changing global
model/thinking defaults.

The fixture's 240s iteration and 300s outer bound were judgment calls based on
the earlier accepted 199s consumer sample and a smaller reporter task. The
shell timeout falsifies sufficiency of that fixture bound for shell completion;
it does not authorize increasing production caps or restarting an active run.

Raw streams, summaries and parser-v6 reports:
`~/.ilk-data/projects/users-chad-projects-github-inluck-net-ilk-skills-604d727/runtime/analysis/design-revision-20261011/controlled-toolkit-tools-pair-20261012-{shell,native}/`.
The [manual tracker](../implementation/loop-execution-improvement-plan.md)
records subsequent checks and delivery.
