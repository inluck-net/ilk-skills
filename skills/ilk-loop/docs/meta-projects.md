# Meta-projects (polyrepo umbrellas)

This document is a worked tutorial. For the concept overview and rules,
see `skills/ilk-loop/SKILL.md` → "Meta-projects (polyrepo umbrellas)".

## When this is the right tool

Use a meta-project when you have:

- A non-git **parent directory** (e.g. `C:\workspace\myproj\`)
- Several **sibling git repos** inside it (`api/`, `portal/`, `ops/`,
  `docs/`, etc.), each with its own `.git`, its own branches, its own
  CI, its own deploys
- Features and bug-fixes that routinely **touch 2-3 of those siblings
  in one logical change** — and you currently coordinate them by hand

If your product is one repo (with or without sub-folders), use ilk in
its normal single-repo mode. If you have several truly independent
projects, just register them separately in
`skills/ilk-launcher/projects.json`. Meta is for the in-between case:
**one product, several repos**.

## Worked example

Throughout this doc the example umbrella is `myproj/`, containing five
git sibling repos and two non-git helper directories:

```
C:\workspace\myproj\
├── .ilk-meta.json                ← the umbrella marker (you'll create this)
├── api/                          ← git repo
├── portal/                       ← git repo
├── ops/                          ← git repo
├── docs/                         ← git repo
├── chrome-extension/             ← git repo
├── e2e/                          ← NOT a git repo; integration test workspace
├── scripts/                      ← NOT a git repo; helper scripts
└── myproj.code-workspace
```

Goal: ship a feature "shipment tracking" that needs changes in `api`
(new endpoint), `portal` (form that calls it), and `ops` (rollout flag).

### Step 1 — scaffold the umbrella marker

From the umbrella root:

```powershell
python $HOME\.cursor\skills\ilk-loop\scripts\init_meta_project.py `
  --root C:\workspace\myproj
```

This scans for child directories that contain `.git` and writes a
`.ilk-meta.json` like:

```json
{
  "name": "myproj",
  "repos": [
    { "name": "api",              "path": "api" },
    { "name": "chrome-extension", "path": "chrome-extension" },
    { "name": "docs",             "path": "docs" },
    { "name": "ops",              "path": "ops" },
    { "name": "portal",           "path": "portal" }
  ]
}
```

The `_comment` field is optional. The repo `name`s are what sub-plans
will reference — pick names that are short and stable. If you want to
exclude a member (e.g. `chrome-extension/` is shipped on its own
cadence), delete its entry by hand. The `name` does not have to equal
the directory `path`; `name` is the convention, `path` is the actual
location relative to the umbrella root.

Re-running with `--merge` later is safe — it adds newly-discovered
members without touching existing entries.

### Step 2 — verify ilk recognizes the umbrella

```powershell
python $HOME\.cursor\skills\ilk-loop\scripts\ilk_paths.py `
  --start C:\workspace\myproj\api\src
```

Expected output (abbreviated):

```json
{
  "project_root": "C:\\workspace\\myproj",
  "project_kind": "meta",
  "project_key": "c-workspace-myproj",
  "external_plans_dir": "C:\\Users\\<you>\\.ilk-data\\projects\\c-workspace-myproj\\plans",
  "current_member": { "name": "api", "path": "C:\\workspace\\myproj\\api" },
  "meta_members": [
    { "name": "api",    "path": "..." },
    { "name": "portal", "path": "..." },
    ...
  ]
}
```

Key things to check:

- `project_kind` is `meta`, not `single`.
- `project_root` is the umbrella, not the member repo you started in.
- `meta_members` lists exactly the repos you expect.

If `project_kind` came back `single`, the marker file is malformed (or
points at non-existent directories). Re-read `read_meta_manifest`'s
validation in `ilk_paths.py` for the exact rules.

### Step 3 — plan the batch

```text
/ilk-plan ship a shipment-tracking feature: api needs a new
POST /shipments endpoint, portal needs a form that calls it, ops
needs a feature flag for staged rollout
```

The planner reads the JSON probe from step 2, sees `project_kind ==
meta`, and:

1. Includes a `Repo` column in its grouping table during the approval
   gate.
2. Writes each sub-plan with `repo: <member-name>` in its frontmatter.
3. Writes a MASTER with a "Repos in scope" section listing api / portal
   / ops with one-line rationales.
4. Runs an extra QC pass (7c) confirming every sub-plan's `repo:` is a
   valid member name. **This is a hard gate** — the planner won't
   advance to commit until it's clean.

After approval, the files land at
`~/.ilk-data/projects/c-workspace-myproj/plans/`.

### Step 4 — launch the loop

From anywhere inside the umbrella (or with `-ProjectPath`):

```powershell
& $HOME\.cursor\skills\ilk-launcher\scripts\launch.ps1 `
  -ProjectPath C:\workspace\myproj
```

A single detached window starts. The driver:

- Snapshots HEAD of every member repo before each iteration.
- Detects new commits per repo after each iteration.
- For each shipped sub-plan, looks up its `repo:` field and routes the
  CI wait + reviewer + ship-report to that member's git directory.

The agent inside the loop reads each sub-plan and, when it sees
`repo: api`, performs all edits and `git add/commit` from the `api/`
working tree.

### Step 5 — observe progress

From anywhere inside the umbrella:

```powershell
python $HOME\.cursor\skills\ilk-loop\scripts\loop_status.py
```

Output gets an extra `repo` column:

```
sub-plan                              repo    status            step
------------------------------------  ------  ----------------  --------
2026-05-23-api-shipment-endpoint.md   api     [OK] shipped       5/5
2026-05-23-portal-shipment-form.md    portal  [..] in-progress   2/6
2026-05-23-ops-shipment-flag.md       ops     [  ] pending       0/3

Next: 2026-05-23-portal-shipment-form.md  (status=in-progress, step=2/6)
Path: ...\c-workspace-myproj\plans\2026-05-23-portal-shipment-form.md
Repo: portal  (C:\workspace\myproj\portal)
```

The `Repo:` line on the "Next" block is the resolved absolute path —
useful when scripting against the output.

### Step 6 — ship

Each sub-plan ships into its own member repo with its own PR and its
own CI run. The MASTER is `shipped` when every sub-plan is `shipped`.
There is no umbrella-level "merge train" — see SKILL.md's
"Atomic ship is not promised" note for the trade-offs and recommended
patterns (feature flags / risk ordering).

## FAQ

**Q: Can a sub-plan touch two repos?**
No. One sub-plan = one repo, by convention. If a logical change truly
spans repos, split it into N sub-plans and wire them with
`depends_on:`.

**Q: What about a worktree of one member?**
A worktree of (say) `api/` lives at a different absolute path with its
own `.git` file. If you want ilk to drive it separately from the main
`api/`, add it as its own line in `.ilk-meta.json`:
`{"name": "api-feat-x", "path": "../api-feat-x"}`. Otherwise it's
invisible to the umbrella loop.

**Q: My umbrella has legacy in-tree plans inside `docs/plans/` (left
over from when `docs/` was driven as its own ilk project). Will they
collide?**
No. The plans-dir resolver in meta mode is **strict external-only**: it
never falls back to walking up into a member's `docs/plans/`. Your
legacy plans are harmless dead weight — keep, move, or delete them at
your own pace.

**Q: How do I uninstall meta mode?**
Delete `.ilk-meta.json` from the umbrella root. The next `ilk_paths.py`
call will see `project_kind == single` for cwds inside member repos
(each member resolves to its own `.git` root, with its own
`project_key`). Plans at `~/.ilk-data/projects/<umbrella-key>/` are
preserved on disk; you can delete that directory by hand when you're
sure you don't need them.

**Q: Can I run several umbrellas in parallel?**
Yes. Each umbrella's `project_key` derives from its own absolute path,
so `~/.ilk-data/projects/<key>/` is per-umbrella. Launcher PID files
live under `<umbrella>/.ilk-launcher/`, also per-umbrella. The same
holds across machines if you sync `.ilk-data/` via your own tooling.
