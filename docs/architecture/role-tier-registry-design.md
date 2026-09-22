# Role tier registry

> **Status:** accepted 2026-09-18 — implemented by batch-2026-09-18b
> (ilk-skills `MASTER-2026-09-18-manager-tier-role-registry`).
> **Last updated:** 2026-09-21 — schema amended with the optional `auth`
> field (§3); see
> [`provider-switching-and-quota-fallback.md`](provider-switching-and-quota-fallback.md)
> for the switching design that motivated it.
> **Driving request:** handoffs inbox entry `2026-09-18 —
> manager-tier-role-registry-and-judgement-boundary`. Extends
> [`model-worker-framework.md`](model-worker-framework.md) §2c (registry row)
> and [`dual-claude-homes-design.md`](dual-claude-homes-design.md) (home
> isolation).

---

## 1. What this is

The formal role→provider registry that `model-worker-framework.md` §2c has
carried as `_to build_` since 2026-06-28: a small JSON file that names every
agentic role, its tier, its home, and its provider, plus the contract for
reading it. Consumers (the installer here; gh-resolve's tier guard via
Decision 0095) resolve entitlement from this file instead of guessing from
directory names.

## 2. Tier model

Three **judgement-authority ranks**, ordered:

| tier | rank | homes today | carries judgement? |
|---|---|---|---|
| `worker` | 0 | `~/.claude-worker` (coder), `~/.claude-worker-draw` (art + VL) | never |
| `planner` | 1 | `~/.claude` | orchestration, not triage judgement |
| `manager` | 2 | `~/.claude-manager` | **yes** — the judgement tier |

The rank is *judgement authority*, not org-chart position. The entry calls
the manager tier "between planner and worker" in the delegation sense; for
the guard, the ordering that makes the semantics work is `worker < planner <
manager`, because a floor is a *minimum rank*:

- a **planner floor** (`rank ≥ 1`) refuses worker homes only — this is what
  `session.render`'s old worker-denylist approximated;
- a **manager floor** (`rank ≥ 2`) refuses worker AND planner homes — the
  triage judgement boundary;
- an **unknown home is always refused**, at any floor. A home absent from
  the registry has no tier, and no tier satisfies a minimum. Fail closed.

## 3. Registry schema (v1)

- **Committed source:** `tools/claude-worker/role-registry.json` (this repo;
  git history is the audit trail).
- **Installed copy:** `~/.ilk-data/role-registry.json` (materialized by
  `install.sh` — the shared root consumers read; `~/.ilk-data/` already hosts
  per-project ledgers).
- **Override:** readers honour `ILK_ROLE_REGISTRY` (absolute path). Tests and
  harnesses point this at tmp fixtures; production never sets it.

```json
{
  "version": 1,
  "hosts": [
    {"name": "chad-mbp", "ssh": "chad-mbp"},
    {"name": "rezmac", "ssh": "rezmac"}
  ],
  "roles": {
    "planner": {"tier": "planner", "home": "~/.claude",
                 "provider": "Claude Official"},
    "manager": {"tier": "manager", "home": "~/.claude-manager",
                 "provider": "Claude Official", "model": "opus",
                 "auth": "official",
                 "path_command": "claude-manager"},
    "coder":   {"tier": "worker", "home": "~/.claude-worker",
                 "provider": "Xiaomi MiMo V2.6 - Pro", "model": "mimo-v2.6-pro",
                 "path_command": "claude-worker"},
    "art":     {"tier": "worker", "home": "~/.claude-worker-draw",
                 "provider": "MiniMax", "model": "MiniMax-M3"}
  }
}
```

Rules:

- `tier` ∈ `{worker, planner, manager}` — anything else is invalid.
- `home` keeps its leading `~`; readers expand it.
- A resolved home MAY appear under several roles (art + VL curator share
  `~/.claude-worker-draw` — one registry entry, `art`, covers the pair since
  only the prompt differs) but MUST carry the same tier under each. A
  conflicting duplicate is invalid; reader and installer both fail loudly.
- `path_command` (optional) names a command the installer puts on PATH for
  that role. Roles without one are reached by `CLAUDE_CONFIG_DIR` dispatch,
  not a wrapper.
- `auth` (optional, added 2026-09-21) ∈ `{official}`. It declares that the
  role runs on the official Claude account, i.e. that its home carries **no**
  provider env and authenticates by OAuth (Keychain login, or
  `CLAUDE_CODE_OAUTH_TOKEN` in the environment). Absence means a
  token-provider home, which is the fail-closed default: `claude-worker.sh`'s
  preflight requires `ANTHROPIC_BASE_URL` / `AUTH_TOKEN` / `MODEL` for every
  home the registry does not mark, so an *accidental* fallback to the
  planner's identity is still refused. For a marked home the checks invert —
  a LEFTOVER base url or token is the error, because it would mean the home
  is still on a third-party provider while the registry claims otherwise.
  Rationale and incident: `provider-switching-and-quota-fallback.md` §5.
- **No secret ever enters this file.** Provider and model names only; tokens
  stay in each home's `settings.json` (framework §0 secrets rule — cc-switch
  is a reference to copy from, never a runtime source).

## 4. Read contract

Any consumer (this repo's installer, gh-resolve's guard) resolves entitlement
as follows:

1. Path: `ILK_ROLE_REGISTRY` if set, else `~/.ilk-data/role-registry.json`.
2. Load and validate: `version == 1`; every tier in the enum; no resolved
   home under two roles with different tiers. Failure at any point is an
   error — **never a fallback default**. A missing registry is a hard stop,
   not "assume planner".
3. Look a config dir up by **resolved-path equality or ancestor-prefix**:
   expanduser + resolve both sides; `config_dir` equal to a home, or located
   beneath one, inherits that home's tier. The ancestor rule preserves the
   old denylist's nested-case refusal (`/opt/something/.claude-worker/configs`
  resolves under the coder home → tier worker → refused at a planner floor).
4. Unknown home (no registry entry matches) ⇒ error naming the registry path
   and the config dir.

## 5. Guard contract

```
require_min_tier(config_dir, tier) -> None (raises on refusal)
```

- Resolve `tier_of_home(config_dir)` per the read contract.
- Raise if unknown, or if `rank(tier_of_home) < rank(tier)`.
- The error message names the registry path used, the required tier, what
  the config dir resolved to, and what tier (if any) it carries.

gh-resolve's Decision 0095 applies this at two floors: `render` keeps a
planner floor; the triage boundary requires manager.

## 6. PATH-entry generation rule

- Registry roles carrying `path_command` drive installer PATH entries:
  `coder → claude-worker`, `manager → claude-manager` (wrapper at
  `tools/claude-worker/claude-manager.sh`, symlinked like the others).
- `claude-worker-switch` is a utility, not a role — it stays a static row in
  `install.sh`.
- Mechanism: `install.sh` reads the committed registry through `python3 -c`
  (JSON parsing in bash is not a thing this repo will grow) and composes the
  `name=source` pairs. A missing, unparseable, or conflicting registry aborts
  the installer loudly — no silent fallback to hardcoded role rows.
- The installer materializes the committed registry to
  `~/.ilk-data/role-registry.json` on every apply (idempotent copy), so the
  shared copy cannot drift from the source.

## 7. Evidence table (state on 2026-09-18, before this batch)

| claim | evidence |
|---|---|
| four homes exist | `~/.claude`, `~/.claude-worker`, `~/.claude-worker-draw` (framework §2a) and `~/.claude-manager` (provisioned 2026-09-18 via `bootstrap.sh --apply --home ~/.claude-manager --from-ccswitch --provider "Zhipu GLM" --link-skills`; live-verified: answers `claude -p`, exit 0) |
| installer hardcodes its PATH rows | `install.sh:469-472` — `PATH_ENTRIES=( "claude-worker=…" "claude-worker-switch=…" )`, no registry concept |
| a stopgap papered over the gap | `~/.local/bin/claude-manager` — real file, header `STOPGAP — unmanaged by install.sh`, body `exec claude-worker --home ~/.claude-manager "$@"`; its own header names this batch as the replacement |
| registry promised, not built | framework §2c row `_to build_`; §5.2 sentence "A formal role→provider registry file is deferred — the current implicit mapping … is sufficient" |
| consumers want a positive check | gh-resolve `session.py:25-28` (marker denylist), `config.py:437-461` (planner-default triage identity) — the gap Decision 0095 closes |

## 8. Out of scope

- Provisioning rezmac's manager home (operator, per the inbox entry).
- Any MCP/capability additions (§2b of the framework is untouched).
- gh-resolve's guard implementation — its own batch, citing this doc.
