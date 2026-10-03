#!/usr/bin/env python3
"""Stub triage agent — records argv to a JSON file."""
import json, sys, pathlib
invocations_file = pathlib.Path(r"/Users/chad/Projects/github/inluck-net/ilk-skills/skills/ilk-watchdog/scripts/triage_invocations.json")
try:
    records = json.loads(invocations_file.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    records = []
records.append(sys.argv[1:])
invocations_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
