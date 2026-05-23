#!/usr/bin/env python3
"""Gate 3 v0: independent reviewer agent via `claude -p`.

Reads sub-plan AC + git diff (subjects only) + optional test/CI context,
calls Claude Code CLI, writes reviewer-report.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PROMPT_TEMPLATE = SKILL_ROOT / "prompts" / "reviewer.md"

# Map BASE_URL host substrings to a coarse "vendor" label. Reviewer
# independence (spec reviewer-agent-spec.md sec5) requires the reviewer
# vendor to differ from the dev vendor.
VENDOR_HOSTS = (
    ("anthropic.com", "anthropic"),
    ("kimi.com", "moonshot"),
    ("moonshot", "moonshot"),
    ("minimax.io", "minimax"),
    ("openai.com", "openai"),
    ("deepseek.com", "deepseek"),
    ("aliyuncs.com", "alibaba"),
    ("bigmodel.cn", "zhipu"),
)


def detect_vendor(base_url: str | None) -> str:
    if not base_url:
        return "unknown"
    bu = base_url.lower()
    for needle, label in VENDOR_HOSTS:
        if needle in bu:
            return label
    return "unknown"


def dev_vendor_from_settings() -> tuple[str, str]:
    """Return (vendor, base_url) the dev side resolves to via claude -p.

    Priority: explicit env > ~/.claude/settings.json env block.
    """
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if not base:
        settings = Path.home() / ".claude" / "settings.json"
        if settings.is_file():
            try:
                data = json.loads(settings.read_text(encoding="utf-8"))
                base = (data.get("env") or {}).get("ANTHROPIC_BASE_URL", "")
            except (json.JSONDecodeError, OSError):
                pass
    return detect_vendor(base), base


def run_git(project: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout or ""


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for raw in text[3:end].splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line and not line.startswith("- "):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def extract_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(text)
    return text[start:end].strip()


def extract_scope_paths(text: str, fm: dict[str, str]) -> str:
    if "scope_paths" in fm:
        return f"scope_paths (frontmatter): {fm['scope_paths']}"
    block = extract_section(text, "Declared scope_paths")
    return block or "(not declared)"


def build_sub_plan_excerpt(sub_plan_text: str, fm: dict[str, str]) -> str:
    parts = [
        f"plan slug: {fm.get('plan', Path(fm.get('sub_plan', 'unknown')).stem)}",
        "",
        "## Objectives",
        extract_section(sub_plan_text, "Objectives") or "(none)",
        "",
        "## Acceptance criteria",
        extract_section(sub_plan_text, "Acceptance criteria") or "(none)",
        "",
        "## Out of scope",
        extract_section(sub_plan_text, "Out of scope") or "(none)",
        "",
        "## Declared scope_paths",
        extract_scope_paths(sub_plan_text, fm),
    ]
    return "\n".join(parts)


def format_diff_with_subjects(project: Path, base: str, head: str) -> str:
    stat = run_git(project, "diff", "--stat", f"{base}..{head}").strip()
    raw_diff = run_git(project, "diff", f"{base}..{head}")
    if not raw_diff.strip():
        return "(empty diff)"

    chunks = re.split(r"(?=^diff --git )", raw_diff, flags=re.MULTILINE)
    out: list[str] = [f"## Files changed\n\n{stat}\n"]
    for chunk in chunks:
        if not chunk.strip():
            continue
        header = chunk.splitlines()[0]
        m = re.match(r"diff --git a/(.+?) b/(.+)", header)
        path = m.group(2) if m else "unknown"
        try:
            meta = run_git(
                project,
                "log",
                f"{base}..{head}",
                "-1",
                "--format=%H %s",
                "--",
                path,
            ).strip()
        except subprocess.CalledProcessError:
            meta = "? (no subject)"
        prefix = f"\n# {path} [{meta}]\n"
        out.append(prefix + chunk)
    return "\n".join(out)


def load_test_results(path: Path | None) -> str:
    if not path or not path.is_file():
        return json.dumps(
            {"before": {}, "after": {}, "new_tests": [], "disabled_tests": []},
            indent=2,
        )
    return path.read_text(encoding="utf-8")


def render_prompt(
    template: str,
    *,
    sub_plan_slug: str,
    sub_plan_excerpt: str,
    test_results: str,
    ci_state: str,
    diff: str,
    model_name: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    replacements = {
        "{{sub_plan_slug}}": sub_plan_slug,
        "{{generated_at}}": now,
        "{{model_name}}": model_name,
        "{{sub_plan_excerpt}}": sub_plan_excerpt,
        "{{test_results}}": test_results,
        "{{ci_state}}": ci_state,
        "{{diff}}": diff,
    }
    out = template
    for key, val in replacements.items():
        out = out.replace(key, val)
    return out


def build_claude_cmd(prompt: str, model: str | None) -> list[str]:
    arg_list = ["-p", "--dangerously-skip-permissions", "--output-format", "text"]
    if model:
        arg_list.extend(["--model", model])
    arg_list.append(prompt)
    return arg_list


def invoke_claude(
    project: Path,
    prompt: str,
    model: str | None,
    reviewer_base_url: str = "",
    reviewer_auth_token: str = "",
) -> str:
    settings_json = Path.home() / ".claude" / "settings.json"
    env = os.environ.copy()
    if settings_json.is_file():
        try:
            data = json.loads(settings_json.read_text(encoding="utf-8"))
            if data.get("env"):
                for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"):
                    env.pop(key, None)
        except (json.JSONDecodeError, OSError):
            pass

    if reviewer_base_url:
        env["ANTHROPIC_BASE_URL"] = reviewer_base_url
    if reviewer_auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = reviewer_auth_token

    args = build_claude_cmd(prompt, model)
    proc = subprocess.run(
        ["claude", *args],
        cwd=str(project),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "claude failed"
        raise RuntimeError(f"claude exit {proc.returncode}: {err[:500]}")
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reviewer agent (gate 3)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--sub-plan", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--output", required=True)
    parser.add_argument("--test-results", default="")
    parser.add_argument("--ci-state", default="unknown")
    parser.add_argument("--ci-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--dev-vendor",
        default="",
        help="Vendor label of the dev agent (e.g. moonshot, anthropic). "
        "Reviewer refuses to run if same as resolved reviewer vendor.",
    )
    parser.add_argument(
        "--reviewer-base-url",
        default="",
        help="Override ANTHROPIC_BASE_URL just for this reviewer invocation. "
        "Use to route reviewer to a different vendor than dev.",
    )
    parser.add_argument(
        "--reviewer-auth-token",
        default="",
        help="Override ANTHROPIC_AUTH_TOKEN for this reviewer invocation.",
    )
    parser.add_argument(
        "--allow-same-vendor",
        action="store_true",
        help="Bypass the same-vendor independence check (use only for smoke "
        "testing; reviewer findings cannot be trusted when bypassed).",
    )
    args = parser.parse_args()

    project = Path(args.project).resolve()
    sub_plan_path = Path(args.sub_plan).resolve()
    output_path = Path(args.output).resolve()
    test_path = Path(args.test_results).resolve() if args.test_results else None

    if not sub_plan_path.is_file():
        print(f"sub-plan not found: {sub_plan_path}", file=sys.stderr)
        return 1
    if not PROMPT_TEMPLATE.is_file():
        print(f"prompt template missing: {PROMPT_TEMPLATE}", file=sys.stderr)
        return 1

    sub_plan_text = sub_plan_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(sub_plan_text)
    slug = fm.get("plan") or sub_plan_path.stem

    # Reviewer independence check (reviewer-agent-spec.md sec5).
    dev_vendor = args.dev_vendor.strip().lower()
    if not dev_vendor:
        dev_vendor, _ = dev_vendor_from_settings()
    if args.reviewer_base_url:
        reviewer_vendor = detect_vendor(args.reviewer_base_url)
    else:
        # Reviewer uses the same settings.json route as dev.
        rv, _ = dev_vendor_from_settings()
        reviewer_vendor = rv
    if (
        dev_vendor
        and reviewer_vendor
        and dev_vendor == reviewer_vendor
        and dev_vendor != "unknown"
        and not args.allow_same_vendor
    ):
        print(
            f"[run_reviewer] REFUSING: dev and reviewer both resolve to vendor "
            f"'{dev_vendor}'. Reviewer independence requires a different "
            f"vendor. Configure --reviewer-base-url + --reviewer-auth-token, "
            f"or pass --allow-same-vendor to bypass (smoke testing only).",
            file=sys.stderr,
        )
        return 4
    if args.allow_same_vendor and dev_vendor == reviewer_vendor:
        print(
            f"[run_reviewer] WARNING: same-vendor bypass active "
            f"(both='{dev_vendor}'); reviewer findings are NOT independent.",
            file=sys.stderr,
        )

    ci_state_line = args.ci_state
    if args.ci_url:
        ci_state_line = f"{args.ci_state} ({args.ci_url})"

    excerpt = build_sub_plan_excerpt(sub_plan_text, fm)
    diff = format_diff_with_subjects(project, args.base, args.head)
    test_results = load_test_results(test_path)
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    model_name = args.model or os.environ.get("ANTHROPIC_MODEL") or "kimi-k2.6 (from settings)"

    prompt = render_prompt(
        template,
        sub_plan_slug=slug,
        sub_plan_excerpt=excerpt,
        test_results=test_results,
        ci_state=ci_state_line,
        diff=diff,
        model_name=model_name,
    )

    try:
        report = invoke_claude(
            project,
            prompt,
            args.model or None,
            reviewer_base_url=args.reviewer_base_url,
            reviewer_auth_token=args.reviewer_auth_token,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
