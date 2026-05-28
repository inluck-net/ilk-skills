# Prerequisites — &lt;Plan Title&gt;

> Read this before launching any sub-plan. Run
> `./check-prereqs.ps1` (Windows) or `./check-prereqs.sh` (macOS/Linux)
> to verify the automatable bits.

## A. Active dev environment (DO NOT MUTATE)

Services that are already running on the host machine and that worker
sessions must NOT restart, kill, or reconfigure.

- Example: `convex dev` running on port 3210 — workers may read deploy
  state, must not bounce
- Example: a `vite dev` server on port 5173 — workers may hit it for
  smoke tests, must not restart

## B. Worker-machine setup (one-time)

Tools that must be on PATH or accessible. The companion
`check-prereqs` script verifies these.

- &lt;tool 1&gt; — `which &lt;tool&gt;` should resolve
- &lt;tool 2&gt; — version &gt;= X.Y

## C. Environment variables

Names + canonical source. Workers MUST NOT modify `.env` files in the
project.

- `&lt;VAR_NAME&gt;` — set by &lt;source&gt; (e.g. shell rc, deploy provider, 1Password
  injection). If unset, ask the human; do NOT invent a value.

## D. Per-group runtime prereqs / blockers

Group-by-group runtime state required + known external blockers.

- Group 1: &lt;state required&gt;
- Group 2: depends on group 1 having seeded &lt;data&gt; in dev deployment.
  If uncertain, run &lt;query&gt; before starting group 2.

## E. Local checks vs runtime verification

`local_checks` in sub-plan frontmatter are run by the loop driver
after each step's commit. By default they are compile-only; the
sub-plan body should add at least one runtime smoke (live API call,
integration test, browser assertion) per the
[Decomposition Principles](../references/decomposition-principles.md) doc.

What `local_checks` covers vs what manual verification covers:

- &lt;example: local_checks asserts JSON shape; manual asserts the
  rendered page actually shows the value&gt;

## F. Restore-on-corruption procedure (optional)

How to recover if a sub-plan corrupts shared state — e.g. a Convex
snapshot restore, a database rollback, a `git reset`. Only fill in if
the plan touches shared state that other engineers might be using.

- &lt;procedure step 1&gt;
- &lt;procedure step 2&gt;
