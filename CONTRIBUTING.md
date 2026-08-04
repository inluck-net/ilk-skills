# Contributing to ilk-skills

Thanks for helping out. This repo is a **toolkit of agent skills**, not a
long-running service — most changes are shell/PowerShell scripts, Python
helpers, and the Markdown skill definitions the host agents read.

## Before you install

`install.sh --apply` / `install.ps1 -Apply` edits your **user-global agent
instructions** (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.cursor/rules/`), not just this repo. Read the warning at the top of the
[Quick start](README.md#quick-start) first — especially the part about setting
`auto_use_ilk_plan: false` if you don't want global routing changes.

## The self-modification hazard

This repo *is* the toolkit your agent session is running from. Editing it
changes the behavior of any live ilk loop on the same machine, because
installed skills are **symlinks into this working tree**.

Before making changes, confirm nothing is running against it:

```bash
pgrep -fl 'run_ilk_loop|scheduler\.(sh|ps1)|watchdog\.(sh|ps1)'
```

If a loop or scheduler is live, stop it (`/ilk-stop`) or wait. A batch that
rewrites scripts underneath a running loop produces confusing, hard-to-
reproduce failures.

## Repo layout

| Path | What lives there |
|---|---|
| `skills/` | one directory per skill; `SKILL.md` is the contract the agent reads |
| `commands/` | slash-command definitions (`/ilk-plan`, `/ilk-run`, …) |
| `conventions/` | cross-host conventions + the `auto_use_ilk_plan` switch |
| `tools/` | standalone helpers (claude-worker, tray, xbar) |
| `tests/` | repo-level shell tests; per-skill tests live in `skills/*/tests/` |
| `docs/` | design notes and field evidence from real runs |

## Running the tests

**Shell suite** (repo-level; hermetic — each test redirects `HOME` to a temp
dir, so it never touches your real `~/.claude`):

```bash
for f in tests/*.sh; do bash "$f" || echo "FAIL $f"; done
```

**Python suite** — needs `pytest`; there is no runtime dependency manifest
because the shipped helpers are stdlib-only:

```bash
pip install pytest          # or: uv run --with pytest python -m pytest
python -m pytest            # pytest.ini pins --import-mode=importlib
```

`pytest.ini` and the root `conftest.py` are what make a single root-level run
work at all: the suite has same-named modules in different skills (two
`cli.py`, two `status_all.py`) that cannot share one `sys.modules` entry. Read
`conftest.py` before restructuring tests.

**Known-failing tests.** Some Python tests are environment-dependent and fail
outside a fully installed layout — they need `powershell`/`pwsh` (absent on
stock macOS/Linux) or a real installed skill home rather than an isolated
`HOME`. Export `ILK_SKILL_HOME=/path/to/this/clone` to fix a subset. Before
claiming a failure is yours, check it against a clean checkout:

```bash
git stash && python -m pytest <the/test.py>; git stash pop
```

**Lints.** The FM-0003 subprocess-encoding guard must stay clean:

```bash
python skills/ilk-loop/scripts/lint_subprocess_encoding.py --scan skills tools
```

Any `subprocess.run`/`Popen` that captures output needs an explicit
`encoding=`. If a call must capture **raw bytes**, pass `encoding=None`
explicitly and say why in a comment — that documents the intent and satisfies
the lint without changing behavior.

## Conventions

- **Cross-platform parity.** Most runtime logic exists as both `*.sh` and
  `*.ps1`. Change one, change the other — several tests statically assert
  parity, and they will fail if you don't.
- **bash 3.2.** macOS ships bash 3.2, so avoid `declare -A` and other bash 4+
  features in shipped scripts.
- **Keep per-machine state out of git.** Real project paths, tokens, and
  markers belong in `~/.ilk-data/` or a gitignored `*.json`. Commit the
  `*.example.json` instead.
- **Docs cite real runs.** `docs/` and `references/` deliberately record field
  evidence (dates, project names, failure classes). Keep new evidence concrete
  and avoid adding customer-identifying detail.

## Pull requests

1. Branch off `main`.
2. Keep the shell suite green and the encoding lint at zero violations.
3. Note which platform(s) you actually exercised — "unverified on Windows" is
   a useful and acceptable statement; a silent assumption is not.
