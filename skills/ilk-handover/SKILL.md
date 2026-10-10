---
name: ilk-handover
description: >-
  Hand the current session's work to a successor in Claude Code, Codex,
  Cursor, or another tool. Write a numbered handoff document and deliver it
  through available session messaging or a pasteable starter prompt. Use for
  /ilk-handover, $ilk-handover, or requests to hand this session over;
  not for implementation planning or inbox triage.
---

# Session handover

Transfer the current goal, its constraints, and enough evidence for a cold
successor to continue. After preparing the handoff, stop implementation work.
This does not stop detached runners or transfer their process ownership.

Accept an optional successor tool and session name. Preserve the user's
choice. Do not create a successor session or spawn an agent merely to obtain
an acknowledgement.

## Choose delivery using the tools actually available

- If the host exposes discovery and messaging for existing peer sessions,
  discover candidates for this project, excluding this session. Use a named
  recipient only after confirming it exists. With multiple unnamed candidates,
  ask the user to choose; do not guess from names or idle status.
- Claude Code's `ListAgents` / `SendMessage`, when exposed, may support that
  path. Verify recipient and delivery capabilities; do not assume cross-tool
  delivery or a reply from a cloud session.
- Codex collaboration tools for child agents do not establish access to
  arbitrary existing Codex threads or Claude sessions. Without an actual peer
  messaging capability, use the document and starter prompt below. Do the
  same when no successor is open yet.
- If a named recipient is missing, still prepare the document, report the
  mismatch, and offer the pasteable delivery path. Never substitute a recipient.

## Write the handoff

Default destination: `~/Documents/handoffs/<project>-<topic>-handoff-N.md`.
Use a destination the user specifies instead when provided.

Continue an inherited series at the next unused number. For a new series,
start at 1 or the next unused number if that filename already exists. Never
overwrite a prior handoff; reserve the new filename exclusively when writing
so simultaneous sessions cannot replace each other's output. Link the prior
document when superseding one.

Record the source tool/session identifier when known, repository absolute
path, branch and HEAD, and the time of observations. Distinguish facts checked
now from earlier observations and recommendations. Include evidence paths,
commands, and actual results where they support a claim; do not convert a
local test into hosted verification or human approval.

Include these sections, keeping detail proportional to the work:

1. **Goals and decisions:** the user's goal, standing orders, accepted scope,
   authorizations, and decisions. Preserve material wording.
2. **Binding promises:** each hold or commitment, its counterparty, release
   condition, and required follow-up. Say none if there are none.
3. **Live state:** changed files, commits and whether pushed, tags and whether
   published, releases per host, PR/review state, queued/held plans, and jobs.
   Read current state where available without changing it.
4. **Job survival:** for jobs this session started, give PID/run ID, project,
   command, logs, launch mechanism, and whether they survive session closure.
   Distinguish session-bound jobs from detached/launchd/scheduler-managed jobs;
   label unknown survival rather than assuming. Provide restart instructions
   only where needed, with a check for an already-running job to avoid duplicates.
5. **Open work and next step:** completed verification, remaining work,
   blockers, and the first concrete action. Label judgment calls.
6. **Peer contacts:** only peers actually contacted, what they await, and how
   the successor can reach them or what the user must relay.
7. **Pitfalls:** observed failures, relevant workarounds, and unresolved risks.

At the top, add **Successor prerequisites**: relevant repository/host
instruction files, memory indexes and specific memory files relied upon,
and any inaccessible local files or hosts. Do not assume another tool loads
the source host's memory or rules. Treat source-host rules as context subject
to the successor's instruction hierarchy, not an override of its own rules.
Include only necessary context; never copy credentials or secrets.

Re-read the saved document to ensure paths, next steps, and transfer status
are complete. Creating the document is not proof the successor received it.

## Deliver and stop

**Messaging available:** send the confirmed successor the document path and
first 1–3 actions. Ask it to read the document and reply `took over`. Report
held/refused delivery without claiming success. While awaiting acknowledgement,
stop implementation work and say `Handoff sent; awaiting acknowledgement.`
After an explicit acknowledgement, notify only actual peer contacts that the
successor holds the contact, then say
`Handed over to <name>; successor acknowledged; safe to close this session.`
Forward later peer messages unchanged while this session remains open.

**Pasteable delivery:** if messaging is unavailable, say
`Handoff prepared; successor has not acknowledged.` Give the document link
and explain any peer messages the user must relay. If source-host messaging
exists, notify actual peers that a handoff is prepared for the intended tool
and recipient, with acceptance pending; do not claim it already took over.
Do not claim you can forward messages after session closure.

End with this filled-in starter prompt as the final block. Omit inapplicable
prerequisites; replace every placeholder with concrete values or an explicit
unknown. The document remains the source of truth.

```text
===== START HANDOVER PROMPT =====
You are taking over <project>'s <goal> from <source tool/session>.
1. Work in <absolute repository path>. Read applicable instructions for your host and repository, then read <absolute handoff path> in full.
2. Read the successor prerequisites listed there, including <relevant memory paths or "no additional memory files required">. Report inaccessible prerequisites and differences from the recorded state.
3. Peer delivery: <available contact route or what the user must relay>.
4. First next step: <concrete action>. Preserve these material constraints: <constraints>.
5. Reply "took over" with a brief summary of the state you checked before continuing. Do not duplicate an already-running job.
===== END HANDOVER PROMPT =====
```
