# A2A protocol fit assessment (planner ↔ worker)

**Date:** 2026-06-17
**Status:** Assessed — **do NOT adopt for planner↔worker now.** Keep as a possible
future enhancement only at the system's outer boundary.

## Question

Should the [A2A (Agent2Agent) protocol](https://a2a-protocol.org/) be used between
ilk's planner (`/ilk-plan`) and worker (the `ilk-loop` runner) to make them
collaborate better?

## What A2A is

Open protocol (Google → Linux Foundation; v1.0 early 2026; 150+ orgs incl.
Microsoft, AWS, Salesforce, SAP). It solves cross-vendor / cross-org agent
**discovery + task delegation + peer collaboration over the network.** Three pieces:

- **Agent Card** — a signed discovery document at `/.well-known/agent.json`
  advertising identity, endpoint, capability flags, auth requirements, skills.
- **Task** — the unit of communication, with a stateful lifecycle:
  `submitted → working → completed`, plus `failed / canceled / input-required /
  auth-required / rejected`. In-memory, streamed.
- **Transport** — SSE streaming + webhook push for long-running work; produces
  structured artifacts.

Positioning vs MCP (industry consensus): **MCP = an agent *using tools* (instant
calls); A2A = agents *partnering on tasks* as peers (long-running, async,
clarification).** A2A only becomes *necessary* when agents are built by different
vendors or live in different orgs / process boundaries.

## Why it does NOT fit ilk's planner ↔ worker

ilk's planner↔worker is a **file-as-bus** pipeline inside a **single trust
domain** — no sockets / RPC / network anywhere:

- Plans live as MASTER + sub-plan markdown (YAML frontmatter `status` /
  `current_step`) under `~/.ilk-data/projects/<key>/plans/`.
- Status flows back via one sentinel (`last-exit.json`) + JSONL
  (`.ilk-loop.log`).
- The scheduler FIFO-drains across projects into slot homes
  (`~/.claude-worker-N`).

Against that, A2A is a mismatch:

1. **No cross-vendor / cross-org discovery need.** Planner and worker are the
   *same* claude CLI binary, the same skills (symlinked), the same machine, with
   fixed path conventions. Agent Card discovery is pure overhead.
2. **A2A's Task lifecycle is a weaker version of what we already have.** Our
   two-level MASTER + sub-plan + per-step `current_step` state machine is on-disk
   and resumable; A2A tasks are in-memory + SSE, so **process death loses
   state** — the exact opposite of ilk's "detached process, resume from disk"
   design center.
3. **Status-back already has a better carrier.** For unattended / overnight /
   cross-session runs, file polling (sentinel + JSONL + watchdog) is more robust
   than a long-lived SSE connection (a dropped connection is gone; a file
   persists). Every hard-won lesson (scheduler wedges, false-stops, BOM, cp936
   crashes) lives on this file path; a network protocol resets that battle
   experience.
4. **New failure surface + cost conflict.** A2A server / client / auth /
   endpoints / SSE are new moving parts that clash with the minimalist "detached
   window + files" style and with the worker's cheap-provider + MCP-whitelist
   cost discipline.

**One-liner:** ilk planner→worker is a **resumable batch pipeline coordinated by
persisted files inside one trust domain**; A2A is a **network protocol for online
peer collaboration across trust domains.** Mismatch.

## When A2A WOULD be worth it (future triggers — at the boundary, not internal)

- **Expose the ilk worker as a service** so external heterogeneous agents
  (someone else's orchestrator, the Kira platform, a customer's agent) can
  delegate tasks in — wrap ilk in an A2A server + Agent Card rather than make
  them learn our `~/.ilk-data` file format.
- **planner needs to call an external third-party *autonomous agent*** (not a
  tool) as a step.
- **multi-machine / multi-org execution** (today it's single-machine,
  multi-slot).

**Rule of thumb:** while it's "same machine, same binary, same skills, files can
reach" → keep the file bus. A2A is the tax you pay only when a real process / org
/ vendor boundary appears.

## Sources

- [Linux Foundation — A2A project launch](https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents)
- [a2aproject/A2A (GitHub)](https://github.com/a2aproject/A2A)
- [A2A and MCP (official docs)](https://a2a-protocol.org/latest/topics/a2a-and-mcp/)
- [MCP vs A2A: When to Use Each — StackOne](https://www.stackone.com/blog/mcp-vs-a2a-protocol/)
- [The Agent Protocol Stack: MCP vs A2A vs AG-UI — dev.to](https://dev.to/jubinsoni/the-agent-protocol-stack-mcp-vs-a2a-vs-ag-ui-when-to-use-what-6dn)
