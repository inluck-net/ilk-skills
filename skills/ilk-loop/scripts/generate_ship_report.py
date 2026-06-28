#!/usr/bin/env python3
"""Gate 4 v0: generate ship-report skeleton with placeholder RISK FLAGs."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VERDICT_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}
FLAG_VERDICT_RANK = {"OK": 0, "WARN": 1, "FAIL": 2}
FLAG_ICON = {"OK": "[OK]", "WARN": "[WARN]", "FAIL": "[FAIL]"}

SHIP_REPORTS_WHITELIST_PREFIX = "docs/plans/ship-reports/"
DOC_EXTENSIONS = (".md", ".txt", ".rst")

DEFAULT_DANGEROUS_PATHS_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "dangerous_paths.yaml"
)


def run_git(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout or ""


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML frontmatter parser.

    Supports flat scalars AND simple top-level lists written as:
        scope_paths:
          - "portal/**"
          - "apps/**"
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict = {}
    current_key: str | None = None
    for raw in text[3:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            current_key = None
            continue
        stripped = raw.strip()
        if stripped.startswith("- ") and current_key:
            item = stripped[2:].strip().strip('"').strip("'")
            if item:
                fm.setdefault(current_key, []).append(item)
            continue
        if ":" in raw and not raw.startswith(" "):
            k, _, v = raw.partition(":")
            key = k.strip()
            val = v.strip()
            if not val:
                # Possibly a list-valued key; wait for "- " lines.
                current_key = key
                fm.setdefault(key, [])
            else:
                fm[key] = val.strip('"').strip("'")
                current_key = None
    # Collapse empty list keys back to "" for backward compatibility with
    # callers that expect a string when no items were given.
    for k, v in list(fm.items()):
        if isinstance(v, list) and not v:
            fm[k] = ""
    return fm


def parse_reviewer_frontmatter(text: str) -> dict[str, str]:
    return parse_frontmatter(text)


def extract_ac_checklist(reviewer_text: str) -> str:
    m = re.search(
        r"##\s*1\.\s*AC verdicts\s*\n(.*?)(?:\n##\s|\Z)",
        reviewer_text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        block = m.group(1).strip()
        if "| AC |" in block:
            return block
    return "(reviewer report missing AC verdicts table)"


def parse_reviewer_verdict(reviewer_text: str) -> str:
    fm = parse_reviewer_frontmatter(reviewer_text)
    if fm.get("overall_verdict"):
        return fm["overall_verdict"].upper()
    m = re.search(r"RECOMMEND:\s*(GREEN|YELLOW|RED)", reviewer_text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return "YELLOW"


def worst_verdict(*verdicts: str) -> str:
    best = "GREEN"
    for v in verdicts:
        key = v.upper()
        if VERDICT_RANK.get(key, 1) > VERDICT_RANK.get(best, 0):
            best = key
    return best


def ci_verdict(ci_state: str) -> str:
    s = ci_state.lower()
    if s in ("success", "green", "skipped"):
        return "GREEN"
    if s in ("failure", "red", "failed"):
        return "RED"
    if s in ("pending", "timeout", "unknown"):
        return "YELLOW"
    return "YELLOW"


def status_badge(status: str) -> str:
    icons = {"GREEN": "✅", "YELLOW": "⚠️", "RED": "⛔"}
    return f"{icons.get(status, '⚠️')} **{status}**"


def changed_files(project: Path, base: str, head: str) -> list[str]:
    out = run_git(project, "diff", "--name-only", f"{base}..{head}")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        # Allow inline "[a, b]" or "a,b"
        s = value.strip().strip("[]")
        return [p.strip().strip('"').strip("'") for p in s.split(",") if p.strip()]
    return []


# ── Definition-of-Done (DoD) outcome banner ──────────────────────────────────
#
# Detects user-facing outcomes from ticket types and body text, then finds
# which AC verifies the outcome at the entry-point level. Uses the same
# consumer-entry keyword set as plan_lint.py's vertical-slice lint for
# consistency (see plan_lint._CONSUMER_ENTRY_RE).

_CONSUMER_ENTRY_RE = re.compile(
    r"""
    click|take_snapshot|press_key|fill\(|hover\(|        # chrome-devtools
    fetch\(|curl\b|requests\.\w+|httpx|aiohttp|          # HTTP
    playwright|cypress|selenium|                          # browser e2e
    e2e/|/e2e\b|                                          # e2e directory
    subprocess|run_command|cli\b|invoke\b|                # CLI verbs
    integration|live\s*smoke|                             # integration
    navigate|goto|open_page|new_page|                     # page navigation
    socket|websocket                                      # real-time
    """,
    re.VERBOSE | re.IGNORECASE,
)

_USER_FACING_TYPE_RE = re.compile(r"新功能|体验优化|feature|enhancement|ux", re.IGNORECASE)

_USER_FACING_VERB_RE = re.compile(
    r"render|display|show|add|enable|create|launch|open|play|start|use|click|"
    r"upgrade|sell|build|move|attack|spawn|select|drag|drop|drag.and.drop",
    re.IGNORECASE,
)


def _detect_user_facing_outcome(sub_plan_text: str) -> tuple[str, str] | None:
    """Detect a user-facing outcome from the sub-plan.

    Returns (outcome_text, ticket_type) or None.
    Checks the tickets table for user-facing types first, then body text.
    """
    fm = parse_frontmatter(sub_plan_text)
    body = sub_plan_text

    # 1. Check tickets table for user-facing types.
    ticket_type = ""
    table_re = re.compile(
        r"\|.*?(?:Ticket|Type|Pri).*?\|(.*?\|)*", re.IGNORECASE | re.DOTALL
    )
    table_match = table_re.search(body)
    if table_match:
        table_block = table_match.group(0)
        if _USER_FACING_TYPE_RE.search(table_block):
            m = _USER_FACING_TYPE_RE.search(table_block)
            if m:
                ticket_type = m.group(0)

    # 2. Check Objectives / outcome section for a user-facing verb.
    outcome = None
    obj_match = re.search(
        r"##\s*(?:Objectives?|Goals?|outcome).*?\n(.*?)(?:\n##|\Z)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if obj_match:
        for line in obj_match.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", "|", "---")):
                continue
            if _USER_FACING_VERB_RE.search(stripped):
                outcome = re.sub(r"^[\s\-\*\d\.]+", "", stripped).strip()
                break

    # 3. Fallback: any AC line mentioning a user-facing verb.
    if not outcome:
        for line in body.splitlines():
            if not re.match(r"^\s*[-*]\s*\*?\*?AC", line, re.IGNORECASE):
                continue
            if _USER_FACING_VERB_RE.search(line):
                outcome = re.sub(r"^[\s\-\*\d\.]+", "", line).strip()
                break

    # 4. If we have a user-facing type but no explicit outcome, use the title.
    if not outcome and ticket_type:
        title_m = re.search(r"^#\s+(.+)", body, re.MULTILINE)
        if title_m:
            outcome = title_m.group(1).strip()

    if not outcome:
        return None
    return (outcome, ticket_type)


def _find_outcome_ac(sub_plan_text: str) -> str | None:
    """Find the AC that verifies the outcome at the entry-point level.

    Returns the matched AC line text, or None if no outcome-level AC exists.
    """
    ac_line_re = re.compile(r"^\s*[-*]\s*\*?\*?AC-?\d+", re.IGNORECASE)
    for line in sub_plan_text.splitlines():
        if not ac_line_re.match(line):
            continue
        if _CONSUMER_ENTRY_RE.search(line):
            return line.strip()
    return None


def dod_section(sub_plan_text: str) -> str:
    """Render the Definition-of-Done block.

    If the sub-plan describes a user-facing outcome, renders:
    - The restated outcome + matched AC (if outcome-level AC found), or
    - A [WARN] line for orphaned-model ships.
    Returns empty string for non-user-facing sub-plans.
    """
    result = _detect_user_facing_outcome(sub_plan_text)
    if result is None:
        return ""

    outcome_text, ticket_type = result
    matched_ac = _find_outcome_ac(sub_plan_text)

    lines = ["## Definition of Done", ""]
    if matched_ac:
        lines.append(f"**Outcome:** {outcome_text}")
        lines.append("")
        lines.append(f"**Verified by:** {matched_ac}")
    else:
        lines.append(f"**Outcome:** {outcome_text}")
        lines.append("")
        lines.append(
            "[WARN] outcome not verified at outcome level -- "
            "no AC references a real entry point (UI/CLI/HTTP/e2e). "
            "This is the GRIDLOCK Gap-A 'orphaned model' shape."
        )
    lines.append("")
    return "\n".join(lines)


def _glob_match(path: str, pattern: str) -> bool:
    """fnmatch with two pragmatic Tailwind-grade tweaks:
    - '**' is treated like fnmatch's '*' but allowed to span '/'
    - bare prefix patterns ("portal/src/") match anything under them.
    """
    import fnmatch

    path = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if "**" in pattern:
        # naive expansion: ** -> *, with fnmatch already matching
        # path separators
        regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        regex = "^" + regex + "$"
        return re.match(regex, path) is not None
    return fnmatch.fnmatch(path, pattern)


# ---- FLAG detectors ----------------------------------------------------
#
# Each detector returns (verdict, summary_line, detail_lines).
#   verdict in {"OK", "WARN", "FAIL"}
#   summary_line: one-line string for the [FLAG-N] row
#   detail_lines: optional bullet list to append below the row


def flag_out_of_scope_files(
    project: Path,
    base: str,
    head: str,
    sub_plan_text: str,
    fm: dict,
) -> tuple[str, str, list[str]]:
    scope_globs = _as_list(fm.get("scope_paths"))
    if not scope_globs:
        return (
            "WARN",
            "scope_paths not declared in sub-plan frontmatter (cannot verify)",
            [],
        )
    changed = changed_files(project, base, head)
    if not changed:
        return ("OK", "no files changed", [])
    out_of_scope: list[str] = []
    for path in changed:
        norm = path.replace("\\", "/")
        if norm.startswith(SHIP_REPORTS_WHITELIST_PREFIX):
            continue
        if any(_glob_match(norm, g) for g in scope_globs):
            continue
        out_of_scope.append(norm)
    if not out_of_scope:
        return ("OK", f"all {len(changed)} changed files match declared scope", [])
    docs_only = all(
        p.endswith(DOC_EXTENSIONS) or p.startswith("docs/")
        for p in out_of_scope
    )
    if docs_only and len(out_of_scope) <= 2:
        return (
            "WARN",
            f"{len(out_of_scope)} doc-only file(s) outside declared scope",
            out_of_scope,
        )
    return (
        "FAIL",
        f"{len(out_of_scope)} file(s) outside declared scope",
        out_of_scope,
    )


def _load_dangerous_paths(project: Path, fm: dict) -> list[str]:
    """Load dangerous-path globs.

    Priority: <project>/.cursor/dangerous_paths.yaml ->
              ilk-loop/templates/dangerous_paths.yaml.
    Frontmatter 'extra_dangerous_paths' is appended.
    """
    paths: list[str] = []
    project_yaml = project / ".cursor" / "dangerous_paths.yaml"
    source = project_yaml if project_yaml.is_file() else DEFAULT_DANGEROUS_PATHS_TEMPLATE
    if source.is_file():
        for raw in source.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("- "):
                item = line[2:].strip()
                # strip trailing inline comment
                if "#" in item:
                    item = item.split("#", 1)[0].strip()
                item = item.strip('"').strip("'")
                if item:
                    paths.append(item)
    paths.extend(_as_list(fm.get("extra_dangerous_paths")))
    return paths


def flag_dangerous_paths_touched(
    project: Path,
    base: str,
    head: str,
    sub_plan_text: str,
    fm: dict,
) -> tuple[str, str, list[str]]:
    dangerous = _load_dangerous_paths(project, fm)
    if not dangerous:
        return ("WARN", "no dangerous_paths.yaml found (skipped)", [])
    allow = _as_list(fm.get("allow_dangerous_paths"))
    changed = changed_files(project, base, head)
    if not changed:
        return ("OK", "no files changed", [])
    hits_blocked: list[str] = []
    hits_allowed: list[str] = []
    for path in changed:
        norm = path.replace("\\", "/")
        matched_pattern = next(
            (g for g in dangerous if _glob_match(norm, g)),
            None,
        )
        if not matched_pattern:
            continue
        is_allowed = any(_glob_match(norm, g) for g in allow)
        if is_allowed:
            hits_allowed.append(f"{norm}  (matches {matched_pattern}; in allow_dangerous_paths)")
        else:
            hits_blocked.append(f"{norm}  (matches {matched_pattern})")
    if hits_blocked:
        return (
            "FAIL",
            f"{len(hits_blocked)} dangerous path(s) touched without declaration",
            hits_blocked + hits_allowed,
        )
    if hits_allowed:
        return (
            "WARN",
            f"{len(hits_allowed)} dangerous path(s) touched (declared in allow_dangerous_paths)",
            hits_allowed,
        )
    return ("OK", f"no dangerous paths touched (checked {len(dangerous)} patterns)", [])


UNSAFE_FILENAME_PATTERNS = [
    re.compile(r"(^|/)\.env(\.[^/]+)?$"),
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"(^|/)id_rsa(\.pub)?$"),
    re.compile(r"(^|/)id_ed25519(\.pub)?$"),
    re.compile(r"(^|/)secrets/"),
    re.compile(r"\.pfx$"),
    re.compile(r"\.keystore$"),
]

UNSAFE_TOKEN_PATTERNS = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub PAT", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9]{40,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{50,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe live key", re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

LARGE_FILE_BYTES = 1 * 1024 * 1024  # 1 MiB
LARGE_FILE_WHITELIST_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")
LARGE_FILE_WHITELIST_PREFIX = ("docs/",)


def _file_size_at(project: Path, ref: str, path: str) -> int | None:
    """Return size in bytes of `path` at git ref, or None if not present."""
    try:
        out = run_git(project, "ls-tree", "-l", ref, "--", path)
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        parts = line.split(None, 4)
        # format: <mode> <type> <sha> <size> <path>
        if len(parts) >= 5:
            try:
                return int(parts[3])
            except ValueError:
                return None
    return None


def flag_unsafe_commits(
    project: Path,
    base: str,
    head: str,
    sub_plan_text: str,
    fm: dict,
) -> tuple[str, str, list[str]]:
    changed = changed_files(project, base, head)
    if not changed:
        return ("OK", "no files changed", [])
    findings: list[str] = []
    for path in changed:
        norm = path.replace("\\", "/")
        # 1. filename match
        for pat in UNSAFE_FILENAME_PATTERNS:
            if pat.search(norm):
                findings.append(f"{norm}  (suspicious filename: {pat.pattern})")
                break
        # 2. large file (only check files actually present at head)
        if not any(norm.endswith(ext) for ext in LARGE_FILE_WHITELIST_EXT) or not any(
            norm.startswith(p) for p in LARGE_FILE_WHITELIST_PREFIX
        ):
            size = _file_size_at(project, head, norm)
            if size is not None and size > LARGE_FILE_BYTES:
                findings.append(
                    f"{norm}  (large file: {size // 1024} KiB > {LARGE_FILE_BYTES // 1024} KiB)"
                )
        # 3. content grep for token prefixes
        try:
            content = run_git(project, "show", f"{head}:{norm}")
        except subprocess.CalledProcessError:
            content = ""
        if content:
            # Cap inspection to first 200 KiB to keep this cheap on huge files
            sample = content[: 200 * 1024]
            for label, pat in UNSAFE_TOKEN_PATTERNS:
                if pat.search(sample):
                    findings.append(f"{norm}  (matches {label})")
                    break
    if not findings:
        return ("OK", f"no unsafe filenames / oversize files / secrets in {len(changed)} files", [])
    return ("FAIL", f"{len(findings)} unsafe commit finding(s)", findings)


FLAG_DETECTORS: list[tuple[str, str, callable]] = [
    ("FLAG-1", "out_of_scope_files", flag_out_of_scope_files),
    ("FLAG-4", "unsafe_commits", flag_unsafe_commits),
    ("FLAG-5", "dangerous_paths_touched", flag_dangerous_paths_touched),
]


def scope_section(project: Path, base: str, head: str, sub_plan_text: str) -> str:
    fm = parse_frontmatter(sub_plan_text)
    claimed = fm.get("scope_paths", "(not declared in frontmatter)")
    actual_files = changed_files(project, base, head)
    stat = run_git(project, "diff", "--stat", f"{base}..{head}").strip()
    lines = [
        "**Claimed scope:**",
        f"```\n{claimed}\n```",
        "",
        f"**Actual changed files ({len(actual_files)}):**",
    ]
    if actual_files:
        lines.append("```")
        lines.extend(actual_files[:50])
        if len(actual_files) > 50:
            lines.append(f"... and {len(actual_files) - 50} more")
        lines.append("```")
    else:
        lines.append("_(none)_")
    lines.extend(["", "**Diff stat:**", f"```\n{stat or '(empty)'}\n```"])
    return "\n".join(lines)


def test_delta_section(test_results_path: Path | None) -> str:
    if not test_results_path or not test_results_path.is_file():
        return (
            "| Metric | Value |\n"
            "| --- | --- |\n"
            "| before total / pass | n/a |\n"
            "| after total / pass | n/a |\n"
            "| new tests | n/a |\n"
            "| disabled tests | n/a |\n"
            "| pass rate delta | n/a (v0: no test-results file) |"
        )
    try:
        data = json.loads(test_results_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "_(invalid test-results JSON)_"

    before = data.get("before") or {}
    after = data.get("after") or {}
    b_total = before.get("total", "?")
    b_pass = before.get("pass", "?")
    a_total = after.get("total", "?")
    a_pass = after.get("pass", "?")
    new_tests = data.get("new_tests") or []
    disabled = data.get("disabled_tests") or []
    rate_note = "unchanged"
    try:
        if int(a_total) and int(b_total):
            br = int(b_pass) / int(b_total)
            ar = int(a_pass) / int(a_total)
            if ar < br:
                rate_note = "DECREASED"
            elif ar > br:
                rate_note = "increased"
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    return (
        "| Metric | Value |\n"
        "| --- | --- |\n"
        f"| before total / pass | {b_total} / {b_pass} |\n"
        f"| after total / pass | {a_total} / {a_pass} |\n"
        f"| new tests | {len(new_tests)} |\n"
        f"| disabled tests | {len(disabled)} |\n"
        f"| pass rate delta | {rate_note} |"
    )


def rollback_section(project: Path, base: str, head: str) -> str:
    shas = [
        ln.split()[0]
        for ln in run_git(project, "log", f"{base}..{head}", "--format=%H %s").splitlines()
        if ln.strip()
    ]
    if not shas:
        return "No commits to revert (empty diff range)."
    cmds = [f"git revert --no-edit {sha}" for sha in reversed(shas)]
    body = "\n".join(f"```powershell\n{cmd}\n```" for cmd in cmds[:10])
    if len(cmds) > 10:
        body += f"\n\n_({len(cmds) - 10} more revert commands omitted)_"
    body += "\n\n**Migration rollback:** inspect diff for `migrations/` — manual downgrade may be required."
    return body


def links_section(
    project: Path,
    base: str,
    head: str,
    reviewer_report: Path,
    ci_url: str,
) -> str:
    try:
        remote = run_git(project, "remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError:
        remote = "(no origin)"
    return "\n".join(
        [
            f"- diff: `{base}..{head}` (local) / remote: `{remote}`",
            f"- reviewer report: `{reviewer_report}`",
            "- staging URL: _(configure per project)_",
            f"- CI run: `{ci_url or 'n/a'}`",
        ]
    )


PLACEHOLDER_FLAGS = [
    ("FLAG-2", "weakened_tests"),
    ("FLAG-3", "mocks_or_todos_left"),
    ("FLAG-6", "undeclared_changes"),
    ("FLAG-7", "suspicious_literals"),
]


def risk_flags_section(
    project: Path,
    base: str,
    head: str,
    sub_plan_text: str,
    fm: dict,
) -> tuple[str, str]:
    """Run all detectors. Return (markdown_block, worst_verdict_status).

    worst_verdict_status is mapped: OK->GREEN, WARN->YELLOW, FAIL->RED.
    """
    rows: dict[str, tuple[str, str, list[str]]] = {}
    worst = "OK"
    for fid, name, detector in FLAG_DETECTORS:
        try:
            verdict, summary, details = detector(project, base, head, sub_plan_text, fm)
        except Exception as exc:  # noqa: BLE001
            verdict, summary, details = "WARN", f"detector error: {exc}", []
        if FLAG_VERDICT_RANK[verdict] > FLAG_VERDICT_RANK[worst]:
            worst = verdict
        rows[fid] = (name, f"{FLAG_ICON[verdict]} {summary}", details)
    for fid, name in PLACEHOLDER_FLAGS:
        rows.setdefault(fid, (name, "[OK-placeholder] not yet implemented (v1/v2)", []))

    lines: list[str] = []
    for fid, (name, summary, details) in sorted(rows.items(), key=lambda kv: kv[0]):
        lines.append(f"[{fid}] {name}: {summary}")
        for d in details[:8]:
            lines.append(f"    - {d}")
        if len(details) > 8:
            lines.append(f"    - ... and {len(details) - 8} more")
    status_map = {"OK": "GREEN", "WARN": "YELLOW", "FAIL": "RED"}
    return "\n".join(lines), status_map[worst]


def update_index(ship_reports_dir: Path, slug: str, report_name: str, status: str) -> None:
    index_path = ship_reports_dir / "INDEX.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    row = f"| {ts} | {slug} | [{report_name}](./{report_name}) | {status} |"
    if index_path.is_file():
        content = index_path.read_text(encoding="utf-8")
    else:
        content = (
            "# Ship Reports Index\n\n"
            "| generated | sub-plan | report | status |\n"
            "| --- | --- | --- | --- |\n"
        )
    content = content.rstrip() + "\n" + row + "\n"
    index_path.write_text(content, encoding="utf-8")


def build_report(
    *,
    project: Path,
    sub_plan_path: Path,
    sub_plan_text: str,
    base: str,
    head: str,
    reviewer_report_path: Path,
    reviewer_text: str,
    test_results_path: Path | None,
    ci_url: str,
    ci_state: str,
    iteration: int,
) -> str:
    fm = parse_frontmatter(sub_plan_text)
    slug = fm.get("plan") or sub_plan_path.stem
    master = fm.get("master_plan", "")
    reviewer_verdict = parse_reviewer_verdict(reviewer_text)
    flags_block, flags_status = risk_flags_section(
        project, base, head, sub_plan_text, fm
    )
    status = worst_verdict(reviewer_verdict, ci_verdict(ci_state), flags_status)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    frontmatter = "\n".join(
        [
            "---",
            f"sub_plan: {slug}",
            f"master_plan: {master}",
            f"generated_at: {now}",
            f"iteration: {iteration}",
            f"status: {status}",
            "shipped_to: staging",
            "prod_promote: pending",
            f"base_ref: {base}",
            f"head_ref: {head}",
            f"ci_run: {ci_url}",
            f"reviewer_report: {reviewer_report_path}",
            "---",
        ]
    )

    dod_block = dod_section(sub_plan_text)

    sections = [
        frontmatter,
        "",
        f"# Ship Report — {slug}",
        "",
        "## 1. STATUS",
        status_badge(status) + f" -- reviewer={reviewer_verdict}, CI={ci_state}, FLAGS={flags_status}",
        "",
        "## 2. RISK FLAGS (7)",
        flags_block,
        "",
        "## 3. SCOPE — claim vs actual",
        scope_section(project, base, head, sub_plan_text),
        "",
        "## 4. AC CHECKLIST",
        extract_ac_checklist(reviewer_text),
        "",
    ]
    if dod_block:
        sections.extend([
            "## 5. DEFINITION OF DONE",
            "",
            dod_block,
            "## 6. TEST DELTA",
            "",
            test_delta_section(test_results_path),
            "",
            "## 7. ROLLBACK",
            rollback_section(project, base, head),
            "",
            "## 8. LINKS",
            links_section(project, base, head, reviewer_report_path, ci_url),
            "",
        ])
    else:
        sections.extend([
            "## 5. TEST DELTA",
            "",
            test_delta_section(test_results_path),
            "",
            "## 6. ROLLBACK",
            rollback_section(project, base, head),
            "",
            "## 7. LINKS",
            links_section(project, base, head, reviewer_report_path, ci_url),
            "",
        ])
    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ship-report (gate 4 v0)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--sub-plan", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--reviewer-report", required=True)
    parser.add_argument("--test-results", default="")
    parser.add_argument("--ci-url", default="")
    parser.add_argument("--ci-state", default="unknown")
    parser.add_argument("--output", default="")
    parser.add_argument("--iteration", type=int, default=1)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    sub_plan_path = Path(args.sub_plan).resolve()
    reviewer_path = Path(args.reviewer_report).resolve()
    test_path = Path(args.test_results).resolve() if args.test_results else None

    if not sub_plan_path.is_file():
        print(f"sub-plan not found: {sub_plan_path}", file=sys.stderr)
        return 1
    if not reviewer_path.is_file():
        print(f"reviewer report not found: {reviewer_path}", file=sys.stderr)
        return 1

    sub_plan_text = sub_plan_path.read_text(encoding="utf-8")
    reviewer_text = reviewer_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(sub_plan_text)
    slug = fm.get("plan") or sub_plan_path.stem

    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    default_name = f"{slug}-{ts}.md"
    output_path = Path(args.output).resolve() if args.output else (
        project / "docs" / "plans" / "ship-reports" / default_name
    )

    report = build_report(
        project=project,
        sub_plan_path=sub_plan_path,
        sub_plan_text=sub_plan_text,
        base=args.base,
        head=args.head,
        reviewer_report_path=reviewer_path,
        reviewer_text=reviewer_text,
        test_results_path=test_path,
        ci_url=args.ci_url,
        ci_state=args.ci_state,
        iteration=args.iteration,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    status = parse_reviewer_verdict(reviewer_text)
    status = worst_verdict(status, ci_verdict(args.ci_state))
    update_index(output_path.parent, slug, output_path.name, status)
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
