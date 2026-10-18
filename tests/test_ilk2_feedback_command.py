"""Pin commands/ilk2-feedback.md as the postmortem command for an ilk2 batch.

docs/07-retiring-v1.md §7.4.3 rebuilds ``ilk-feedback`` on ilk2's versioned
JSON, beside the V1 command until the switch.  This file is that command's
instructions, so it must:

* drive ``postmortem.py`` rather than reimplement it;
* name the two ilk2 commands it actually uses (``ilk2 status`` to resolve the
  batch, ``ilk2 improvement add`` to record findings);
* never point at V1's run files or ilk2's run directories -- those are the
  paths the whole batch exists to stop reading;
* carry the judgement the script cannot: what the labels mean and what to
  change next time, citing the report's numbers.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMAND_MD = REPO_ROOT / "commands" / "ilk2-feedback.md"

# The V1 / run-directory spellings this command must never contain.  Each is
# a thing V1's collector reads and ilk2's report replaces; naming one here is
# how a reader gets sent back to the wrong tree.
FORBIDDEN = (
    "~/.ilk-data",
    ".ilk-loop.log",
    "last-launch.json",
    "last-exit.json",
    "data/runs",
)


def _text() -> str:
    """The file as written, for the forbidden-spelling check."""
    return COMMAND_MD.read_text(encoding="utf-8")


def _phrases() -> str:
    """The file with whitespace collapsed, for phrase checks.

    Prose wraps; a sentence must not fail a pin because a line break landed
    inside it.  The forbidden-spelling check above uses the raw text.
    """
    return " ".join(_text().split())


def test_the_file_exists() -> None:
    assert COMMAND_MD.is_file(), "commands/ilk2-feedback.md is missing"


def test_it_names_the_script_and_the_two_ilk2_commands() -> None:
    text = _phrases()
    assert "postmortem.py" in text
    assert "ilk2 improvement add" in text
    assert "ilk2 status" in text


def test_it_names_no_v1_run_file_and_no_run_directory() -> None:
    text = _text()
    for spelling in FORBIDDEN:
        assert spelling not in text, (
            "commands/ilk2-feedback.md mentions %r: this command reads ilk2's "
            "JSON, not V1's run files or ilk2's run directories" % spelling
        )


def test_it_resolves_the_batch_from_an_argument_or_from_status() -> None:
    text = _phrases()
    assert "batch id" in text.lower()
    assert "ilk2 status --json" in text
    # an unresolved batch stops the command; it is never invented
    assert "Do not invent" in text


def test_it_runs_the_script_with_write() -> None:
    text = _phrases()
    assert "postmortem.py" in text
    assert "--write" in text
    assert "~/.ilk2/postmortems" in text


def test_it_adds_judgement_the_script_cannot() -> None:
    """The three levers, and the report numbers the advice must cite."""
    text = _phrases()
    lowered = text.lower()
    for lever in ("plan shape", "gates", "step size"):
        assert lever in lowered, "missing lever: %s" % lever
    for field in (
        "minutes_per_accepted_step",
        "first_attempt_share",
        "first edit",
        "long requests",
    ):
        assert field in text, "missing report number: %s" % field
    assert "Ground every claim in a number" in text


def test_it_offers_findings_and_says_a_person_accepts_them() -> None:
    text = _phrases()
    assert "--findings" in text
    assert "ilk2 improvement add" in text
    assert "only after a person marks them" in text
    assert "`accepted`" in text


def test_it_never_launches_or_restarts_anything() -> None:
    text = _phrases()
    assert "never launches" in text
    assert "restart" in text
    assert "Never launch, restart, resume, park or cancel anything." in text


def test_it_says_the_v1_only_labels_do_not_apply() -> None:
    text = _phrases()
    for label in (
        "shipped-unverified",
        "self-hosting-drift",
        "merge-conflict",
        "merge-deferred",
    ):
        assert label in text, "missing V1-only label: %s" % label
    assert "do not apply to ilk2" in text


def test_it_treats_null_as_unknown_not_zero() -> None:
    text = _phrases()
    assert "unknown" in text
    assert "Null is unknown, never zero" in text
