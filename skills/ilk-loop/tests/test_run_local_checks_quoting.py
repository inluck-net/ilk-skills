"""Regression (FM-0004): run_local_checks must unescape YAML double-quoted
`local_checks` commands, so `command: "node -e \\"...\\""` reaches bash as
  node -e "..."
not the literal
  node -e \\"...\\"
which dies with `bash: syntax error near unexpected token '('` → a false
`local_checks_failed` (it false-failed math-blocks' numberline-primitive-ui
node -e gate even though the files exist and the check passes directly).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_local_checks as rlc  # noqa: E402


class TestCoerceUnescape:
    def test_double_quoted_node_e_unescapes_inner_quotes(self) -> None:
        # the exact FM-0004 shape: a YAML double-quoted scalar with \" inside
        raw = '"node -e \\"JSON.parse(x)\\""'
        assert rlc._coerce(raw) == 'node -e "JSON.parse(x)"'

    def test_double_quoted_backslash_pair_unescaped(self) -> None:
        # \\  ->  \   (Windows-path-ish), without touching the rest
        assert rlc._coerce('"a\\\\b"') == "a\\b"

    def test_double_quoted_leaves_other_backslashes_literal(self) -> None:
        # conservative: \n stays a literal backslash-n (not a newline), so we
        # never mangle a backslash a command legitimately passes to a tool
        assert rlc._coerce('"grep -P \\"\\\\d+\\""') == 'grep -P "\\d+"'

    def test_single_quoted_doubles_collapse(self) -> None:
        assert rlc._coerce("'it''s'") == "it's"

    def test_plain_quoted_passthrough(self) -> None:
        assert rlc._coerce('"plain command"') == "plain command"
        assert rlc._coerce("'plain'") == "plain"

    def test_unquoted_and_scalars_unchanged(self) -> None:
        assert rlc._coerce("cd server && npm test") == "cd server && npm test"
        assert rlc._coerce("true") is True
        assert rlc._coerce("120") == 120
