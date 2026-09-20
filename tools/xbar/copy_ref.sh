#!/bin/bash
# Copy an ilk-ref onto the clipboard. Invoked by the SwiftBar panel's
# per-row "Copy reference" action as:  bash /bin/bash param1=<this> param2=<ref>
#
# The ref arrives as $1 and is SPACE-FREE BY GRAMMAR
# (ilk-ref:<key>/<master-file>/<sub-file>) — that is the whole point:
# every SwiftBar param value in the menu line stays bare, needing no
# quoting at all. SwiftBar's parser drops actions whose params contain
# pipes (split delimiter) and chokes on quotes nested inside quoted
# values; the two prior attempts died to each of those in turn
# (2026-09-20). Keep this script's contract: one argument, no spaces,
# printf not echo (no trailing newline — the paste lands as one line).
printf '%s' "$1" | pbcopy
