"""Test: registry .md resolution — template + bare-slug back-compat.

AC-1: A master written from master-template.md (with linked .md refs)
      resolves sub-plans on first loop_status run (exit 1, sub-plan listed).

AC-2: A master whose registry uses bare YYYY-MM-DD-slug (no .md) also
      resolves — back-compat for hand-written masters.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root — three levels up from this file (tests/ → ilk-loop/ → skills/ → root).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LOOP_STATUS = REPO_ROOT / "skills" / "ilk-loop" / "scripts" / "loop_status.py"
TEMPLATE = REPO_ROOT / "skills" / "ilk-loop" / "templates" / "master-template.md"

_KEY_PUNCT = re.compile(r"[^a-z0-9]+")


def _project_key(root: Path) -> str:
    abs_str = str(root.resolve()).lower()
    slug = _KEY_PUNCT.sub("-", abs_str).strip("-")
    if len(slug) <= 80:
        return slug
    h = hashlib.sha1(abs_str.encode("utf-8")).hexdigest()[:7]
    return slug[: 80 - 8].rstrip("-") + "-" + h


# Fixed scratch dir inside the repo (gitignored).
SCRATCH = REPO_ROOT / "scratch" / "registry-md-resolution"
ILK_DATA = SCRATCH / "ilk-data"


def _make_git_project(name: str) -> Path:
    root = SCRATCH / "projects" / name
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(root)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True,
    )
    return root


def _cleanup():
    if SCRATCH.exists():
        import shutil
        def _rm_onerror(func, path, exc):
            try:
                os.chmod(path, 0o666)
                func(path)
            except OSError:
                pass
        shutil.rmtree(SCRATCH, onerror=_rm_onerror)


@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    yield
    _cleanup()


# ── AC-1: template-linked .md refs resolve ───────────────────────────

def test_template_linked_md_refs_resolve():
    """A master from master-template.md with linked .md registry rows
    resolves sub-plans on first loop_status run (exit 1, NOT 'no sub-plan
    references')."""
    name = "template-linked"
    root = _make_git_project(name)
    key = _project_key(root)
    plans = ILK_DATA / "projects" / key / "plans"
    plans.mkdir(parents=True, exist_ok=True)

    # Read the template and fill in real slugs.
    template_text = TEMPLATE.read_text(encoding="utf-8")
    sub_fname = "2026-06-15-template-sub.md"
    master_text = template_text.replace("<slug-1>", "template-sub")
    master_text = master_text.replace("<list>", "test")
    master_text = master_text.replace("<N>", "3")
    master_text = master_text.replace("2026-MM-DD", "2026-06-15")
    # The template's registry rows use the literal YYYY-MM-DD date placeholder
    # (filled with a real date by /ilk-plan). Render it the same way here, else
    # the registry link stays an invalid date and resolves to nothing.
    master_text = master_text.replace("YYYY-MM-DD", "2026-06-15")
    master_text = master_text.replace("HH:MM:SS", "12:00:00")
    master_text = master_text.replace("<human-readable plan title>",
                                       "Test template-linked")
    master_text = master_text.replace("<short-slug>", "template-linked")
    master_text = master_text.replace("<one-sentence summary>", "test fixture")
    master_text = master_text.replace("<explicit non-goal>", "none")
    # Remove the meta-project block (single-repo test).
    master_text = re.sub(
        r"<!--\nMETA PROJECTS ONLY.*?-->",
        "",
        master_text,
        flags=re.DOTALL,
    )
    # Remove other placeholder sections that won't resolve.
    master_text = master_text.replace("<one-sentence summary>", "test")

    (plans / f"MASTER-2026-06-15-{name}.md").write_text(master_text, encoding="utf-8")

    # Write a matching sub-plan.
    sub = (
        "---\n"
        "plan: template-sub\n"
        "status: pending\n"
        "current_step: 0\n"
        "tickets: []\n"
        "priority: P2\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-15\n"
        "---\n"
        "\n# Sub-plan for template-sub\n"
    )
    (plans / sub_fname).write_text(sub, encoding="utf-8")

    env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
    result = subprocess.run(
        [sys.executable, str(LOOP_STATUS)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}: {result.stderr}"
    assert "no sub-plan references" not in result.stderr.lower(), (
        f"template-linked master should resolve, but got: {result.stderr}"
    )
    assert sub_fname in result.stdout, (
        f"expected {sub_fname} in output, got:\n{result.stdout}"
    )


# ── Regression: frontmatter `slug:` must NOT be matched as a sub-plan ──

def test_frontmatter_slug_not_matched_as_subplan():
    """A master whose own `slug:` is a YYYY-MM-DD-* value must not be mis-read
    as a phantom sub-plan. Regression: after bare-slug matching was added, the
    extractor matched the frontmatter `slug: 2026-06-15-loop-robustness` line
    and invented a phantom MISSING sub-plan."""
    sys.path.insert(0, str(REPO_ROOT / "skills" / "ilk-loop" / "scripts"))
    from loop_status import extract_master_order  # noqa: E402

    master = (
        "---\n"
        "title: Test phantom\n"
        "slug: 2026-06-15-loop-robustness\n"   # date-prefixed slug — the trap
        "status: active\n"
        "---\n"
        "\n## Sub-plan registry\n\n"
        "| # | Order | Sub-plan | Items | Steps | Status |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 1 | [2026-06-15-real-sub.md](./2026-06-15-real-sub.md) | x | 3 | pending |\n"
    )
    order = extract_master_order(master)
    assert "2026-06-15-real-sub.md" in order, order
    assert "2026-06-15-loop-robustness.md" not in order, (
        f"frontmatter slug must not be matched as a sub-plan: {order}"
    )


# ── AC-2: bare YYYY-MM-DD-slug resolves ─────────────────────────────

def test_bare_slug_resolves():
    """A master whose registry uses bare YYYY-MM-DD-slug (no .md)
    also resolves — back-compat for hand-written masters."""
    name = "bare-slug"
    root = _make_git_project(name)
    key = _project_key(root)
    plans = ILK_DATA / "projects" / key / "plans"
    plans.mkdir(parents=True, exist_ok=True)

    sub_fname = "2026-06-15-bare-sub.md"
    # Master with BARE slug in registry (no .md, no link).
    master = (
        "---\n"
        f"title: Test {name}\n"
        f"slug: {name}\n"
        "created: 2026-06-15T12:00:00+08:00\n"
        "status: active\n"
        "priority: 5\n"
        "pause_after_ship: false\n"
        "branch: null\n"
        "goal: test bare-slug resolution\n"
        "out_of_scope: []\n"
        "cross_cutting_invariants: []\n"
        "---\n"
        f"\n# Test {name}\n\n"
        "## Sub-plan registry\n\n"
        "| # | Order | Slug | Items | Steps (est.) | Status |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 1 | 2026-06-15-bare-sub | test | 3 | pending |\n"
    )
    (plans / f"MASTER-2026-06-15-{name}.md").write_text(master, encoding="utf-8")

    # Sub-plan file (must exist on disk as YYYY-MM-DD-slug.md).
    sub = (
        "---\n"
        "plan: bare-sub\n"
        "status: pending\n"
        "current_step: 0\n"
        "tickets: []\n"
        "priority: P2\n"
        "estimated_steps: 3\n"
        "last_updated: 2026-06-15\n"
        "---\n"
        "\n# Sub-plan for bare-sub\n"
    )
    (plans / sub_fname).write_text(sub, encoding="utf-8")

    env = {**os.environ, "ILK_DATA_HOME": str(ILK_DATA)}
    result = subprocess.run(
        [sys.executable, str(LOOP_STATUS)],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}: {result.stderr}"
    assert "no sub-plan references" not in result.stderr.lower(), (
        f"bare-slug master should resolve, but got: {result.stderr}"
    )
    assert sub_fname in result.stdout, (
        f"expected {sub_fname} in output, got:\n{result.stdout}"
    )
