#!/usr/bin/env python3
"""parse_branch_block.py — extract a MASTER plan's `branch:` block as JSON.

Reads the MASTER file given as argv[1], parses the `branch:` frontmatter
(either inline `{create_from: ..., name: ..., merge_back: ...}` or an indented
block), and prints a JSON object to stdout. Prints `{}` when there is no
branch block.

This was previously an inline `python -c @"..."@` here-string inside
run_ilk_loop_claude.ps1 / .sh. That approach broke twice on Windows:
  1. open() used the locale encoding (GBK/cp936 on zh-CN) -> UnicodeDecodeError
     on UTF-8 masters with non-ASCII bytes.
  2. the PowerShell expandable here-string mangled the embedded quotes in
     `strip('"').strip("'")` -> SyntaxError (unterminated string literal).
A real script file sidesteps both: explicit utf-8-sig read, and no
here-string/quote marshalling. Keep stdout ASCII-only (zh-CN cp936 console).
"""
from __future__ import annotations

import json
import re
import sys


def parse_branch(content: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return {}
    fm = m.group(1)
    branch_match = re.search(r"^branch:\s*(.*)$", fm, re.MULTILINE)
    if not branch_match:
        return {}
    rest = branch_match.group(1).strip()
    if rest in ("null", "None", "~", ""):
        return {}

    # Inline dict form: branch: {create_from: HEAD, name: spike/x, merge_back: false}
    if rest.startswith("{"):
        inner = rest.strip("{}")
        d: dict = {}
        for part in re.split(r",\s*", inner):
            if ":" in part:
                k, v = part.split(":", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "merge_back":
                    d[k] = v.lower() in ("true", "yes", "1")
                else:
                    d[k] = v
        return d

    # Indented block form:
    #   branch:
    #     create_from: HEAD
    #     name: spike/x
    d = {}
    in_branch = False
    for line in fm.split("\n"):
        if re.match(r"^branch:\s*$", line):
            in_branch = True
            continue
        if in_branch:
            if re.match(r"^\s+", line):
                kv = re.match(r"^\s+(\w+):\s*(.*)$", line)
                if kv:
                    k = kv.group(1)
                    v = kv.group(2).strip().strip('"').strip("'")
                    if k == "merge_back":
                        d[k] = v.lower() in ("true", "yes", "1")
                    else:
                        d[k] = v
            else:
                break
    return d


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("{}")
        return 0
    path = argv[1]
    try:
        # Explicit UTF-8 (BOM-tolerant): masters are UTF-8; the locale default
        # (GBK/cp936 on zh-CN Windows) crashes on non-ASCII bytes.
        with open(path, encoding="utf-8-sig") as f:
            content = f.read()
    except OSError:
        print("{}")
        return 0
    print(json.dumps(parse_branch(content)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
