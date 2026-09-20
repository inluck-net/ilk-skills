#!/bin/bash
# Copy an ilk-ref onto the clipboard. Invoked by the SwiftBar panel's
# per-row "Copy reference" action as:  bash /bin/bash param1=<this> param2=<ref>
#
# The ref arrives as $1 and is SPACE-FREE BY GRAMMAR
# (ilk-ref:<key>/<master-file>/<subplan-file>) — that is the whole point:
# every SwiftBar param value in the menu line stays bare-or-quoted without
# nesting, since the parser splits the params blob on the first pipe and
# ends unquoted values at spaces. Keep this script's contract: one
# argument, no spaces, printf not echo (no trailing newline — the paste
# lands as one line).
#
# One-line append to /tmp/ilk-copy-ref.log per invocation: an entry proves
# SwiftBar fired the action; no entry after a click means it never did
# (parse failure), which is the first thing to check when debugging.
printf '%s\n' "$(date '+%H:%M:%S') ref=${1:-EMPTY}" >> /tmp/ilk-copy-ref.log
printf '%s' "$1" | pbcopy
