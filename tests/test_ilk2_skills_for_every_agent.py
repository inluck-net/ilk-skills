"""Pin the two ilk2 skills and their skill-root resolution (MASTER D2-D3).

``skills/ilk2-plan`` and ``skills/ilk2-feedback`` are the discoverable skills:
a thin ``SKILL.md`` each, pointing at the command file that carries the real
instructions.  Every ``commands/ilk2-*.md`` resolves ``<skill-root>`` from
whichever agent's link exists -- Claude Code, Codex or Cursor -- not only
Claude's, so the planner is reachable from Codex and Cursor too.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = REPO_ROOT / "skills"
COMMANDS = REPO_ROOT / "commands"

# The five commands that share one ## Paths block shape.  Listed explicitly so
# a dropped command cannot pass the suite by dropping out of a glob.
ILK2_COMMAND_NAMES = (
    "ilk2-feedback.md",
    "ilk2-inbox.md",
    "ilk2-lark.md",
    "ilk2-plan.md",
    "ilk2-spec.md",
)
ILK2_COMMAND_FILES = [COMMANDS / n for n in ILK2_COMMAND_NAMES]

# The two commands that are also skills, and so get a second candidate
# through ~/.codex/skills/<name> and ~/.cursor/skills/<name> (MASTER D3).
SKILL_WRAPPERS = ("ilk2-plan", "ilk2-feedback")

# A skill folder is only a wrapper: it points at its command file and says
# where <skill-root> is.  3,000 characters is the ceiling the AC sets for
# "does not copy the command's steps" -- the command files are 10x that.
WRAPPER_BODY_LIMIT = 3000


def _text(path: Path) -> str:
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


# --- the ## Paths block ----------------------------------------------------

def _paths_block(text: str) -> str:
    """The fenced bash block under ## Paths, as written in the file.

    The ACs run exactly this block -- not a copy, and not the prose around
    it -- so the test reads it back out of the command file.
    """
    start = text.index("## Paths")
    next_heading = text.find("\n## ", start + 1)
    section = text[start:next_heading] if next_heading != -1 else text[start:]
    open_at = section.index("```bash")
    body_at = section.index("\n", open_at) + 1
    close_at = section.index("```", body_at)
    return section[body_at:close_at]


def _run_paths_block(block: str, home: Path) -> subprocess.CompletedProcess[str]:
    """Run the block with HOME pointed at `home`, then echo the SKILL_ROOT.

    The block itself need not print: the AC appends `echo "$SKILL_ROOT"`
    after it, and the value it set is what gets asserted.
    """
    return subprocess.run(
        ["bash", "-c", block + '\necho "$SKILL_ROOT"'],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        cwd=str(home),
    )


def _link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


def _fresh_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


# --- AC-1: the two SKILL.md wrappers --------------------------------------

def _split_skill_md(path: Path) -> tuple[str, str]:
    text = _text(path)
    assert text.startswith("---\n"), f"{path} has no YAML front-matter"
    end = text.index("\n---\n", 3)
    return text[4:end], text[end + 5 :]


def _front_matter_field(fm: str, key: str) -> str:
    """The value of `key:`, joining the lines of a folded (>-) block scalar."""
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith(f"{key}:"):
            continue
        inline = line.split(":", 1)[1].strip()
        if inline and inline not in (">-", ">", "|", "|-", "|+"):
            return inline
        folded = []
        for cont in lines[i + 1 :]:
            if cont[:1].isspace():
                folded.append(cont.strip())
            elif not cont.strip():
                continue
            else:
                break
        return " ".join(x for x in folded if x)
    raise AssertionError(f"front-matter has no {key}: line")


def test_the_two_skill_wrappers_exist() -> None:
    for name in SKILL_WRAPPERS:
        assert (SKILLS / name / "SKILL.md").is_file(), (
            f"skills/{name}/SKILL.md is missing"
        )


@pytest.mark.parametrize("name", SKILL_WRAPPERS)
def test_front_matter_names_the_folder_and_its_slash_trigger(name: str) -> None:
    fm, _body = _split_skill_md(SKILLS / name / "SKILL.md")
    assert _front_matter_field(fm, "name") == name, (
        f"skills/{name}/SKILL.md front-matter name: must equal its folder name"
    )
    desc = _front_matter_field(fm, "description")
    assert desc.strip(), f"skills/{name}/SKILL.md description: is empty"
    assert f"/{name}" in desc, (
        f"skills/{name}/SKILL.md description: does not name the /{name} trigger"
    )


@pytest.mark.parametrize("name", SKILL_WRAPPERS)
def test_the_body_points_at_the_command_file_and_defines_skill_root(name: str) -> None:
    _fm, body = _split_skill_md(SKILLS / name / "SKILL.md")
    assert f"../../commands/{name}.md" in body, (
        f"skills/{name}/SKILL.md does not name "
        f"../../commands/{name}.md (the command file, from the skill folder)"
    )
    assert "follow" in body.lower(), (
        f"skills/{name}/SKILL.md does not tell the agent to follow the command file"
    )
    assert "<skill-root>" in body, (
        f"skills/{name}/SKILL.md never defines <skill-root>"
    )
    assert "parent" in body.lower(), (
        f"skills/{name}/SKILL.md does not say <skill-root> is the parent of the "
        f"skill's folder"
    )


@pytest.mark.parametrize("name", SKILL_WRAPPERS)
def test_the_body_is_a_wrapper_not_a_copy_of_the_command(name: str) -> None:
    _fm, body = _split_skill_md(SKILLS / name / "SKILL.md")
    assert len(body) < WRAPPER_BODY_LIMIT, (
        f"skills/{name}/SKILL.md body is {len(body)} characters: a wrapper "
        f"stays under {WRAPPER_BODY_LIMIT} and copies none of the steps"
    )
    assert "## 1." not in body, (
        f"skills/{name}/SKILL.md body carries the command's step headings"
    )


# --- AC-2: the ## Paths block resolves from any agent's link ---------------

@pytest.mark.parametrize("cmd_path", ILK2_COMMAND_FILES, ids=lambda p: p.name)
def test_paths_block_resolves_skill_root_from_a_claude_link(
    tmp_path: Path, cmd_path: Path
) -> None:
    home = _fresh_home(tmp_path)
    _link(home / ".claude" / "commands" / cmd_path.name, cmd_path)
    proc = _run_paths_block(_paths_block(_text(cmd_path)), home)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(SKILLS), proc.stdout


@pytest.mark.parametrize("cmd_path", ILK2_COMMAND_FILES, ids=lambda p: p.name)
def test_paths_block_resolves_skill_root_from_a_codex_link(
    tmp_path: Path, cmd_path: Path
) -> None:
    home = _fresh_home(tmp_path)
    _link(home / ".codex" / "commands" / cmd_path.name, cmd_path)
    proc = _run_paths_block(_paths_block(_text(cmd_path)), home)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(SKILLS), proc.stdout


@pytest.mark.parametrize("cmd_path", ILK2_COMMAND_FILES, ids=lambda p: p.name)
def test_paths_block_resolves_skill_root_from_a_cursor_link(
    tmp_path: Path, cmd_path: Path
) -> None:
    home = _fresh_home(tmp_path)
    _link(home / ".cursor" / "commands" / cmd_path.name, cmd_path)
    proc = _run_paths_block(_paths_block(_text(cmd_path)), home)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(SKILLS), proc.stdout


@pytest.mark.parametrize("name", SKILL_WRAPPERS)
@pytest.mark.parametrize("agent", ["codex", "cursor"])
def test_paths_block_resolves_skill_root_from_a_skill_link(
    tmp_path: Path, name: str, agent: str
) -> None:
    """A Codex or Cursor skills link alone is enough: the skill folder's
    SKILL.md realpaths to <repo>/skills/<name>/SKILL.md, whose grandparent
    is the skills directory."""
    home = _fresh_home(tmp_path)
    cmd_path = COMMANDS / f"{name}.md"
    _link(home / f".{agent}" / "skills" / name, SKILLS / name)
    proc = _run_paths_block(_paths_block(_text(cmd_path)), home)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(SKILLS), proc.stdout


@pytest.mark.parametrize("cmd_path", ILK2_COMMAND_FILES, ids=lambda p: p.name)
def test_paths_block_fails_loudly_with_nothing_linked(
    tmp_path: Path, cmd_path: Path
) -> None:
    home = _fresh_home(tmp_path)
    proc = _run_paths_block(_paths_block(_text(cmd_path)), home)
    assert proc.returncode != 0, (
        "the ## Paths block succeeded with no agent link installed; "
        f"it printed {proc.stdout!r}"
    )
    claude_path = home / ".claude" / "commands" / cmd_path.name
    named = str(claude_path) in proc.stderr or (
        f"~/.claude/commands/{cmd_path.name}" in proc.stderr
    )
    assert named, (
        "the ## Paths block's stderr does not name the Claude path it "
        f"tried ({claude_path}): {proc.stderr!r}"
    )


# --- AC-3: the old one-line snippet cannot pass the Codex case -------------

def test_the_paths_block_is_not_the_claude_only_snippet() -> None:
    """Guard for AC-3, stated statically: the block must name more than the
    Claude link.  The behavioural form is the Codex case above -- restore the
    old one-liner in any one command and that test goes red; this pin stops
    the shortcut from being reintroduced without touching the block's shape.
    """
    for cmd_path in ILK2_COMMAND_FILES:
        block = _paths_block(_text(cmd_path))
        assert ".codex/commands/" in block, (
            f"{cmd_path.name} ## Paths block only looks at the Claude link"
        )
        assert ".cursor/commands/" in block, (
            f"{cmd_path.name} ## Paths block only looks at the Claude link"
        )
    for name in SKILL_WRAPPERS:
        block = _paths_block(_text(COMMANDS / f"{name}.md"))
        assert f".codex/skills/{name}" in block, (
            f"{name} ## Paths block has no skill-folder fallback"
        )
