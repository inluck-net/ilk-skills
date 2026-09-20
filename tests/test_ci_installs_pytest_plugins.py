#!/usr/bin/env python3
"""CI must install every pytest plugin that pytest.ini's addopts requires.

Parses ``pytest.ini`` for long options that belong to third-party plugins and
checks that ``ci.yml``'s install step includes them.

Part of sub-plan ci-installs-what-pytest-ini-requires (step 0).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PYTEST_INI = _ROOT / "pytest.ini"
_CI_YML = _ROOT / ".github" / "workflows" / "ci.yml"

# Each key is an option prefix that pytest's core does not provide.
# The value is the pip package that supplies it.
_OPTION_TO_PLUGIN: dict[str, str] = {
    "--timeout": "pytest-timeout",
    "--timeout-method": "pytest-timeout",
}


def _parse_addopts() -> list[str]:
    """Return the tokenised ``addopts`` value from *pytest.ini*."""
    text = _PYTEST_INI.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("addopts"):
            # Format: addopts = --flag --flag=value ...
            _, _, value = stripped.partition("=")
            return value.split()
    return []


def _required_plugins(tokens: list[str]) -> set[str]:
    """Derive the set of pip packages required by *tokens*."""
    required: set[str] = set()
    for tok in tokens:
        # Normalise: strip ``=value`` suffix and negation prefix.
        opt = tok.split("=", 1)[0]
        if opt.startswith("--no-"):
            continue
        for prefix, plugin in _OPTION_TO_PLUGIN.items():
            if opt == prefix or opt.startswith(prefix + "="):
                required.add(plugin)
    return required


def _ci_install_packages() -> set[str]:
    """Parse ``ci.yml`` and return the set of pip packages it installs."""
    text = _CI_YML.read_text(encoding="utf-8")
    # Match the line: `run: python -m pip install --upgrade pip pytest ...`
    pattern = re.compile(
        r"pip\s+install\s+.*?(?:--upgrade\s+)?(.+)$", re.MULTILINE
    )
    packages: set[str] = set()
    for m in pattern.finditer(text):
        for token in m.group(1).split():
            # Skip flags and requirement-file references.
            if token.startswith("-"):
                continue
            packages.add(token.lower())
    return packages


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAddoptsPlugins:
    """Every plugin implied by ``pytest.ini`` addopts must be installed in CI."""

    def test_required_plugins_installed(self) -> None:
        """CI installs every plugin that addopts references."""
        tokens = _parse_addopts()
        required = _required_plugins(tokens)
        installed = _ci_install_packages()

        missing = {p for p in required if p not in installed}
        assert not missing, (
            f"pytest.ini addopts requires plugin(s) {missing} "
            f"but ci.yml does not install them"
        )

    def test_addopts_not_empty(self) -> None:
        """Sanity: pytest.ini actually defines addopts."""
        tokens = _parse_addopts()
        assert tokens, "pytest.ini addopts is empty or missing"

    def test_option_mapping_covers_known_prefixes(self) -> None:
        """The mapping must contain at least the prefixes addopts uses."""
        tokens = _parse_addopts()
        opts = {t.split("=", 1)[0] for t in tokens if t.startswith("--")}
        unknown = {
            o for o in opts
            if not any(o == p or o.startswith(p + "=") or o.startswith("--no-")
                       for p in _OPTION_TO_PLUGIN)
            and not o.startswith("--import-mode")
            and not o.startswith("--timeout")  # same plugin, covered above
        }
        # Only warn if there are genuinely unknown long opts — the mapping
        # doesn't need to cover built-in pytest flags like --import-mode.
        # This test exists to catch new third-party opts added later.
        assert not unknown, (
            f"Unrecognised long option(s) in addopts — "
            f"add to _OPTION_TO_PLUGIN if they need a plugin: {unknown}"
        )