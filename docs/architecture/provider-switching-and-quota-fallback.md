# Provider switching and quota fallback

> **Status:** proposed 2026-09-21 — tier 1 and tier 2 agreed with the
> operator; tier 3 (an LLM API router) considered and rejected.
> **Last updated:** 2026-09-21.
> **Driving incident:** Zhipu GLM returned its weekly/monthly cap at
> 2026-09-21 14:26 (`429 · [1310] 您已达到每周/每月使用上限，您的限额将在
> 2026-09-22 15:16:35 重置`), which stalled the gh-resolve loop and forced a
> by-hand switch of every worker home to MiMo and the manager home to the
> official account across two hosts.
> **Extends** [`role-tier-registry-design.md`](role-tier-registry-design.md)
> (§3 schema, §4 read contract) and
> [`model-worker-framework.md`](model-worker-framework.md) §0 (secrets) and
> §2c (registry row).

---

## 1. What this is

A design for *changing which provider a role runs on* — by hand in one
command, and automatically when a provider's quota is exhausted. It also
defines the one file this repo publishes so other consumers (gh-resolve
today) can tell whether a home is alive **before** spending work on it.

It does not change how a provider is bound. That remains what
`dual-claude-homes-design.md` established: a role owns a home, the home's
`settings.json` `env` block names the provider, and a `claude` process reads
it at start via `CLAUDE_CONFIG_DIR`.

## 2. What the 2026-09-21 incident established

Four measurements shape every decision below.

1. **Switching is cheap; knowing is expensive.** The switch itself is a JSON
   env block. Every failure that day was a *verification* failure — see the
   evidence table in §12.
2. **Each loop iteration is a fresh process.** `iter-01.log`, `iter-02.log`
   are separate `claude -p` invocations that each re-read `settings.json`. A
   provider swap therefore takes effect on the **next iteration**, with no
   restart of the runner, the watchdog, or the scheduler. `scheduler.sh`
   matches `ANTHROPIC|CLAUDE_CONFIG_DIR|settings.json|export` 0 times in
   1273 lines — it holds no provider state at all.
3. **A 429 is two different events wearing one status code.** Iteration 02
   carried ten transient `api_retry status=429` lines *and then* a terminal
   payload with a reset timestamp. The discriminator is the reset time, not
   the status code.
4. **Config-correct is not alive.** `claude auth status` reports that *a
   credential exists*, not that it works: on rezmac it returned
   `{"loggedIn": true, "authMethod": "oauth_token"}` for a plist token whose
   real call returned `403 Request not allowed`.

## 3. Scope

| tier | what | decision |
|---|---|---|
| 1 | one command switches a role's provider, on every host, verified | build |
| 2 | detect quota exhaustion, fall back, recover when it resets | build |
| 3 | a local LLM API router / proxy in front of every request | **rejected** |

**Why tier 3 is rejected.** A router puts a live process in front of every
request — a new single point of failure for all loops — while the thing that
actually failed was never the switching mechanism but the *observability* of
provider state (two registry copies disagreeing, a probe measuring the wrong
thing, a panel showing history). A router would add a third place where the
provider is decided. It also cannot model the official account cleanly: no
base url, no token, OAuth via Keychain or `CLAUDE_CODE_OAUTH_TOKEN`, failing
with 403 rather than 429. The single thing only a router can do is switch
providers *mid-session*, because Claude Code fixes its endpoint at process
start; per §2.2 our loops already restart per iteration, so that capability
buys nothing today. Revisit only for a long-lived interactive session that
must survive a cap without restarting.

## 4. Tier 1 — the switch command

`ilk-worker-model` (shipped 2026-09-21, `tools/claude-worker/worker_model.py`)
is the vehicle; it gains a provider argument and loses three defects.

### 4.1 Surface

```
ilk-worker-model roles                       # name, tier, home, configured model, auth mode
ilk-worker-model providers                   # id, display name, model, base-url host, token present
ilk-worker-model show [--host all]           # live state per home + valid targets
ilk-worker-model use <role> <provider> [--host all] [--dry-run] [--now]
ilk-worker-model restore [--host all]
```

`roles`, `providers` and `show` also emit `--json`; the SwiftBar surface
(§10) consumes that rather than re-implementing the lookups. Because the
valid values come from the registry and the provider store, `argparse
choices=` cannot express them: validation stays at runtime and prints the
full list on failure — which today only happens *after* a mistake, never
before it.

`--dry-run` prints the resolved env (token masked), the homes that would
change, and the registry field that would move. A wrong switch currently
costs a rollback across every home.

### 4.2 Provider resolution

Today `cmd_use` sources the env block from the **role's own home** and
overrides only `ANTHROPIC_MODEL`, so it can change the model *within* a
provider but can never switch providers — a glm→mimo switch writes a MiMo
model string against the Zhipu base url. The fix is to resolve the provider
from the cc-switch store:

```
python3 tools/claude-worker/ccswitch_import.py export --provider <id> --machine
```

This stays inside the framework §0 secrets rule: cc-switch is *"a reference
to copy from, never a runtime source"* — the switch tool copies at switch
time; no running session ever reads it.

### 4.3 Sweep by role, not by glob

`worker_homes()` globs `~/.claude-worker*`, so `~/.claude-manager` is
invisible to the tool and the entire manager half of the 2026-09-21 switch
was hand-edited JSON. The sweep becomes: resolve roles from the registry,
expand each home, include numeric worker slots per `get_slot_home`.

### 4.4 Verification — two probes, because they answer different questions

| probe | command | answers | failure mode it catches |
|---|---|---|---|
| config | `claude -p --output-format stream-json --verbose --max-turns 1 hi \| head -1` → `.model` | which model the process loaded | wrong env written, home skipped |
| live | `claude -p --output-format json --max-turns 1 "Say OK"` → `is_error` | auth and quota | expired token, 403, 429 cap |

The existing `probe_model()` asks the model to *state its own id*, which is
the model's opinion: MiMo answers `claude-sonnet-4-20250514`, and the strict
equality check rolled back a correct switch on all three homes (exit 4). The
init event is emitted from config before any API response, so it also
survives a 429. Neither probe alone is sufficient — §2.4.

`--skip-probe` must stop printing `switch verified for every home above`.

### 4.5 Atomic registry + home write

A role's provider and its registry row are one fact in two files. Since the
preflight keys on `auth` (§5), moving the manager off the official account
means clearing `auth` in the same operation that writes the env — otherwise
the preflight refuses the leftover token. The command therefore writes the
committed registry and re-materialises the installed copy through
`install.sh --apply --only-path` (never by hand), so both readers move
together.

## 5. Registry amendment — `auth: official`

`claude-worker.sh`'s fail-closed preflight required
`ANTHROPIC_BASE_URL`/`AUTH_TOKEN`/`MODEL` unconditionally, on the premise
that an empty provider env meant an *accidental* fallback to the planner's
OAuth identity. When the manager moved to the official account deliberately,
the guard refused exactly the configuration the role was supposed to have.

The role registry gains an optional `auth` field (`role-tier-registry-design.md`
§3). For a role declared `"auth": "official"` the three checks invert: an
absent provider env is correct, and a *leftover* base url or token is the
error. Silence is the fail-closed answer — an unreadable registry, a missing
Python, or a home no role claims all keep the strict preflight.

Implemented 2026-09-21 (`8a19050`), `test_preflight.sh` 11 of 11.

## 6. The published contract — `provider-state.json`

One file, secret-free, that says whether a home is alive. gh-resolve is the
first consumer: it never authenticates anything itself (it hands a child
process a `CLAUDE_CONFIG_DIR` and lets the home resolve its own identity),
so what it needs is not "switch a provider" but "don't spend a judgement on
a dead home".

```json
{
  "version": 1,
  "homes": {
    "/Users/chad/.claude-worker": {
      "provider": "66f41c76-...", "model": "mimo-v2.5-pro",
      "state": "ok",
      "config_probe":  {"result": "mimo-v2.5-pro", "at": "2026-09-21T15:02:11+08:00"},
      "live_probe":    {"ok": true,  "detail": "", "at": "2026-09-21T15:02:19+08:00"},
      "reset_at": null,
      "evidence": {"log": ".../iter-02.log.jsonl", "run_id": "20260921-142231"}
    },
    "/Users/chad/.claude-manager": {
      "provider": "claude-official", "model": "opus",
      "state": "exhausted",
      "config_probe":  {"result": "claude-opus-5", "at": "..."},
      "live_probe":    {"ok": false, "detail": "403 Request not allowed", "at": "..."},
      "reset_at": null,
      "evidence": {"log": "~/.gh-resolve-data/logs/triage.stderr.log"}
    }
  }
}
```

Rules, each with a reason:

- **Keyed by resolved home path, not by role.** Consumers select an identity
  by home path; keying by role would force every consumer to read the
  registry too, and then the two files can disagree — the exact split-brain
  found at 14:45 on 2026-09-21, when the installed registry still described
  the manager as Zhipu GLM.
- **Both probes recorded separately**, each with its own timestamp and
  result. A state file that reports `ok` on config evidence alone reproduces
  §2.4 one consumer further from the evidence.
- `state` ∈ `{ok, exhausted, unknown}`; `reset_at` is ISO-8601 **with
  offset**; `observed_at` on every probe so a consumer can judge staleness
  itself.
- **Unknown `version` is a hard error, never a default**; a malformed or
  stale file fails closed. `role_tiers.py`'s loader is the reference shape.
- **No secret ever enters this file** — same rule as the registry.

## 7. The credential cache — `providers.json` (not the contract)

Autonomous fallback on a remote host needs that host to hold the credentials
of the provider it is falling back to. That is a *different* file from §6
with different rules:

| | `provider-state.json` | `providers.json` |
|---|---|---|
| contains | observed state | provider env blocks incl. tokens |
| secrets | never | yes |
| mode | 0644 | **0600** |
| published to other tools | yes, contract | no, private to this repo's tooling |
| source | each host's own probes | pushed from the control host |

Justification for the second copy: rezmac has **no cc-switch store** —
`~/.cc-switch`, `~/Library/Application Support/cc-switch` and
`~/.config/cc-switch` are all absent, and `find ~ -maxdepth 4 -name
cc-switch.db` finds nothing. Without a local cache, a remote host can only
fall back to a provider that already happens to be configured in one of its
homes, and cannot recover autonomously while the control host is asleep.
The exposure is not new in kind: the same tokens already sit at 0600 in
every worker home's `settings.json` on that host. The cost that *is* new:
key rotation must sweep every host.

## 8. Tier 2 — fallback and recovery

### 8.1 Detect

In the runner, which already parses the stream. Declare a provider exhausted
only when a terminal payload carries a **future reset timestamp**, or when N
consecutive iterations end `api_error` with 0 tokens. Never on a bare 429 —
§2.3.

### 8.2 Decide

An ordered preference list per role, e.g. `coder: [glm, mimo, kimi]`,
`manager: [glm, official]`. Fallback and recovery are the same operation in
opposite directions.

### 8.3 Act

Through tier 1's code path — one implementation, two triggers. Because of
§2.2 a running loop picks the change up on its next iteration.

### 8.4 Recover

On the scheduler's existing 5-minute poll; no new daemon. `reset_at` is a
*hint*, not permission: switch back only after a live probe succeeds. The
clock said the cap lifts at 15:16:35 on 2026-09-22; the probe is what makes
it true.

### 8.5 Guardrails

- **Judging roles are pinned by default.** Workers auto-switch; the manager
  tier does not, unless opted in per role. The tier guard validates `tier`
  and `home` only, so a demoted provider on `~/.claude-manager` passes the
  guard unchanged while the judgement is actually formed by a worker-grade
  model — and gh-resolve's policy is explicit that *"a judgement formed under
  any lesser identity is not the operator's judgement"*. Auto-fallback would
  make that both invisible and automatic.
- **Loud, not silent.** Every automatic switch writes a ledger row and
  surfaces in the panel; a fallback that quietly works is one discovered
  three weeks later in a ledger row.
- **Cooldown.** One switch per provider per reset window, so a flapping
  endpoint cannot ping-pong the fleet.

## 9. Multi-host

- **One control host** (chad-mbp). cc-switch stays the only place keys are
  entered.
- **The host list rides in the role registry**, which is already versioned
  and already materialised to `~/.ilk-data` on every host by `install.sh`.
  A new machine is learned by pulling; no second config file. No secrets —
  ssh targets only.
- **Verify on each host, never from the control host.** Same config,
  different outcome: MiMo answers on both hosts, the official account
  answers on neither (rezmac's own `~/.claude` reports `OAuth session
  expired and could not be refreshed`).
- **Partial failure is reported per host** — "2 of 3 switched, rezmac
  unreachable", never a blanket "done".
- **No dependency on remote coreutils.** `timeout(1)` is absent from
  rezmac's ssh PATH and `gtimeout` is not installed there at all.
- **Quota is account-scoped, not host-scoped.** The GLM cap applies to every
  host using that account, so `provider-state.json` is really *account*
  state observed independently by each host. Each host records its own
  observations; a decision reads the union, most recent exhaustion wins.
  Exhaustion is monotonic until `reset_at`, so the hosts converge without a
  central authority — and the control host does not become a SPOF for
  fallback.

## 10. SwiftBar surface

The panel is `status_all.py --json | render_xbar.py`, re-run every 10s, and
already carries actions (`bash=… param1=… terminal=false refresh=true`).

- `status_all.py` grows a `roles` block: home, configured model, provider
  host, auth mode per registry role. Reading a handful of 1 KB files per
  poll is free.
- `render_xbar.py` grows a **Models** section — one row per role, each with a
  provider submenu invoking `ilk-worker-model use <role> <provider>`.
- **Keep both numbers.** The existing `worker model:` line is deliberately
  *what iterations actually ran* ("when config and reality diverge, reality
  is the thing worth seeing"); the new rows are *what is configured*. When
  they differ, render the divergence rather than choosing one.
- **A panel click never stops a loop**: config-only, no `--now`, labelled
  "applies from next iteration" — which is literally true per §2.2.
- **Failures must be dragged into view.** `render_xbar.py` already documents
  that SwiftBar `bash=` failures are invisible: no window, no error. The
  action runs detached, writes a result line, and the panel renders the last
  switch outcome.

## 11. Risks and open items

- **Plist token stripping.** Four LaunchAgents on rezmac carry
  `CLAUDE_CODE_OAUTH_TOKEN`; one of them, `net.inluck.ilk.scheduler`, is
  ours. gh-resolve's installer hit a bug where `upgrade` ran
  uninstall+reinstall from a context carrying neither secret and stripped the
  token three times on 2026-08-28; its fix is an env-wins-then-file
  `read_secret`. Anything ilk-skills writes into a plist inherits that bug
  unless it copies the pattern. **Answered 2026-09-22:** ilk writes
  `CLAUDE_CODE_OAUTH_TOKEN` in **0 places** across `install.sh` and `skills/`,
  so that plist was created by hand and an ilk upgrade cannot strip it. The
  flip side is that nothing re-renders it either, and it bit the same day: the
  rezmac rotation refreshed the six `gh-resolve` agents through
  `install_launch_agents.sh --live` and left `net.inluck.ilk.scheduler` on the
  dead token until it was set by hand. **It is the only copy no installer will
  fix.** `net.inluck.ilk.scheduler.plist.ROTATION.md` now sits beside it on
  rezmac carrying the four-write checklist and the current token's md5 prefix
  — a sibling file rather than an XML comment, because `PlistBuddy -c Set`
  rewrites the plist and drops comments, so an inline note would vanish on the
  first rotation. chad-mbp's copy of the plist carries no token at all (PATH
  and HOME only), so the hazard is rezmac-only.
- ~~**rezmac has no working official credential**~~ — **resolved 2026-09-22.**
  A `claude setup-token` credential minted on rezmac (md5 `558ffa02`) now backs
  `~/.claude`, `~/.claude-manager`, `~/.gh-resolve-data/claude-token` (and
  through it the six gh-resolve agents) and the ilk scheduler plist. Both homes
  return `authMethod: oauth_token` **and** a live `OK`; all seven LaunchAgents
  report `last_exit=0`, including `net.inluck.gh-resolve.triage`, which had
  been exiting 1 since 14:40 the previous day. The hosts hold separate tokens,
  so revoking one does not affect the other.
- **What we copy rather than couple** (~100 lines, none published as a
  module): `role_tiers.py`'s validate-and-fail-closed loader, doctor's
  per-home identity probe, the D-170 launchd-vs-ssh credential-store text,
  and the installer's env-wins-then-file secret pattern.

## 12. Evidence table (measured 2026-09-21)

| # | measurement | value |
|---|---|---|
| 1 | GLM cap payload | `429 · [1310] …重置 2026-09-22 15:16:35`, iter-02, 180.8s, 0 tokens |
| 2 | scheduler provider state | 0 matches in 1273 lines of `scheduler.sh` |
| 3 | self-report probe on a MiMo home | `claude-sonnet-4-20250514` → false rollback, exit 4 |
| 4 | init-event probe | `mimo-v2.5-pro` on 3 of 3 homes (chad-mbp), 6 of 6 (rezmac) |
| 5 | live round trip, worker | `OK`, 4.0s, both hosts |
| 6 | manager home, both hosts | `loggedIn: false, authMethod: none` |
| 7 | rezmac plist token | `auth status: loggedIn true` + real call `403 Request not allowed` |
| 8 | rezmac default home | `OAuth session expired and could not be refreshed` |
| 9 | registry split-brain | installed copy 18 Sep vs committed: manager row differed on both hosts |
| 10 | cc-switch on rezmac | 0 of 3 candidate paths present; 0 `cc-switch.db` under `~` (depth 4) |
| 11 | token-bearing LaunchAgents on rezmac | 4 of 11, all carrying the same token |
| 12 | preflight tests after the `auth` change | 11 of 11, both hosts |

## 13. Out of scope

- Per-request routing, cost-based routing, latency-based routing (see §3).
- Rotating provider keys — cc-switch remains the place keys are entered.
- Anything that moves gh-resolve's switching into this repo: it has no
  switching to move (§6).
