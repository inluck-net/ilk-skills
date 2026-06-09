# Handoff: WeChatRelay production hardening + cold-start provisioning debug

> Companion to `2026-06-08-wechatrelay-device-debug.md`. That one covered the
> first device bring-up (BUG-8/9/10/11). This one covers the **production
> hardening + QR provisioning** batches and, in particular, the **cold-start
> provisioning bug** — a multi-layer *runtime* defect the loop shipped
> compile-green that took ~6 device cycles to root-cause. The lessons sharpen
> `/ilk-plan` for `device-manual` work specifically.

## Context

Three more loop batches after v1: **server-quality**, **v1.1-extensions**
(both `loop-verified`, shipped genuinely-working — 58 pytest), then
**hardening** (Caddy TLS/`wss` + shared-secret token) and **qr-provision**
(scan/deep-link config + persistence). The hardening/QR batches were
`device-manual`; their code compiled, unit-tested the pure parts, and shipped —
but the actual phone→VPS flow had stacked runtime bugs.

The VPS deploy itself surfaced infra realities a plan can't know: pip pinned to
an unreachable Aliyun mirror (`-i https://pypi.org/simple`); port 443 held by a
dockerized `ss-v2ray` proxy (stopped, reversible); Caddy stuck in a renew-loop
on stale cert state (cleared); Aliyun Security Group as the real ingress gate.
All human-resolved. Then the app side.

## The cold-start bug — debugging timeline (the expensive part)

Symptom: provision the phone (deep link carrying host/port/tls/token) → app
launches, foreground service starts → **but never registers at the VPS**.
Nondeterministic: one run connected-then-dropped, the next did nothing.

| # | Hypothesis | Test | Finding |
|---|---|---|---|
| 1 | Deep-link cold-start not captured | logcat | Android DID deliver the intent (`NewIntentItem`); a *warm* re-fire also didn't apply → not a capture problem |
| 2 | Listener registered after the awaited initial connect drops the `configure` event | move listeners before `await connectFromPrefs()` | Now it connected — then **dropped** (online→offline). Progress + a new failure |
| 3 | Two concurrent `connectFromPrefs` (initial→localhost + configure→vps) race on the shared tunnel | add a token-guard to skip the initial when no token | Next run: **nothing** connected (empty server journal). Nondeterministic |
| 4 | (got logs at last) read the actual flow | `adb logcat \| grep connectWith` | **`connectWith: host=localhost ... token=EMPTY`** then `SocketException ... address=localhost`. The bg isolate had **no config** |

Root cause (4 layers, all invisible to analyze/build/unit-test):
1. **Cross-isolate `SharedPreferences` staleness** — the UI isolate writes the
   provisioned prefs; the **background-service isolate has a separate prefs
   cache** and never saw them → re-read returned defaults (`localhost`, empty token).
2. The `configure` event payload **omitted token + tls**, and the listener
   **ignored the payload** and re-read its (stale) prefs.
3. The `on('configure')` listener was registered **after** the awaited initial
   connect → a provisioning event during cold-start startup was dropped.
4. Initial-connect + configure-connect **raced** on the one shared tunnel.

Fix (small, once understood): `configure` carries the **full params**; the bg
isolate **connects from the payload**, never re-reading cross-isolate prefs;
listeners registered **before** the initial connect; connect **coalesced**
(single in-flight, re-runs with latest params); initial connect only when a
token is already saved; reconnect DRY'd into one helper used by all paths
(deep-link / scan / button). **Verified:** `pm clear` → cold-start deep-link →
`wss` connect on first launch → 4159-char article over the hardened link.

## Why it was expensive (and what would have made it cheap)

- **~6 device cycles** (build ≈ 2–3 min each + install 200 MB + provision +
  inspect). The *fix* was ~30 lines; *root-causing* was the cost.
- **Observability was the bottleneck.** For most cycles there were **no app
  logs** (HarmonyOS hid Flutter stdout under unexpected tags; the worker's code
  had almost no logging at decision points). The bug only cracked once a
  `debugPrint('connectWith: host=… token=…')` existed and surfaced
  `token=EMPTY`. Every cycle before that was guessing.
- **The bugs were stacked across two batches** (`app-wss-and-token` +
  `app-qr-provision-and-persist`, shipped in *different* batches) and
  **interacted** — fixing one exposed the next. They were never verified on a
  device until both had shipped.

## Lessons → concrete `/ilk-plan` improvements

**P7 — `device-manual` sub-plans must ship with observability.** Require, as an
AC, `debugPrint`/structured logs at every decision point the human verifier
will need: which config was read, which branch taken, connection target,
success/failure with the error. "No logs" turned a 30-line fix into a 6-cycle
hunt. Add to `decomposition-principles.md`: *a device-manual sub-plan whose only
diagnostics are "it works or it doesn't" is under-specified.*

**P8 — `device-manual` runtime failure-mode checklist (concrete).** The earlier
case study proposed a checklist (P5); here's the content this saga proves it
must contain, for any sub-plan touching connectivity/lifecycle/IPC:
- **Cross-isolate / cross-process shared state** — `SharedPreferences` (and
  similar) caches are **per-isolate**; a write in one isolate is NOT visible to
  another. Pass state in the message payload; don't re-read shared storage across
  an isolate boundary.
- **Event-listener registration ordering** — register `on(event)` listeners
  **before** any `await` that could let the event fire first (dropped-event class;
  this bit us 3× across BUG-8/9 and here).
- **Cold-start vs warm-start** — deep links / intents behave differently on a
  fresh process vs a running one. Test **both**, from a true `pm clear`.
- **Concurrent connect/reconnect on shared resources** — coalesce; never run two
  connects on one socket/tunnel.
- **Permission/consent timing** (foreground vs background; OEM background-launch
  blocks — Huawei).
- **OEM divergence** — Huawei/HarmonyOS: no Google backup, background-launch
  interception, hidden log tags, custom USB debug bridge.

**P9 — Verify `device-manual` sub-plans incrementally; never stack them.** Two
device-manual sub-plans shipped in separate batches and their bugs compounded.
`/ilk-plan` should recommend: after a batch containing `device-manual` sub-plans
ships, **do the human+device pass before planning the next batch that builds on
it.** Stacking unverified device work multiplies the debugging surface.

**P10 — Budget the asymmetry.** For `device-manual` work, the human cost is
**root-causing**, not coding, and each iteration is minutes (build+flash). Plans
should (a) front-load observability (P7) and the checklist (P8) to cut
iterations, and (b) set expectations that a device-manual sub-plan = a
human debugging session, not a "review the diff" — size batches accordingly.

## What worked (keep)

- **`verification_tier` + `shipped-unverified`** classified the hardening/QR
  runs correctly and listed exactly the sub-plans needing a device pass — no
  false `clean-success`. This is the single best guardrail; it set the agenda.
- **Loop-verified server work was flawless** (58 pytest, all genuinely working):
  token auth, provision URI build/parse + CLI, OCR, multi-device, monitoring.
  The split (server=loop-verified, app/deploy=device-manual) held perfectly.
- **Pure-function-as-loop-gate**: `parseProvisionUri` shipped with a real unit
  test (loop-verified) even though the scan flow around it is device-manual.
  Extracting the pure core is the right pattern.
- The **deep link doubled as a test harness** (`adb shell am start -d
  'wechatrelay://provision?...'`) — provisioning without typing/scanning made
  each device cycle scriptable. Plans for device features should include such a
  non-UI provisioning/trigger path.

## One-line takeaway

The loop builds correct *code*; it cannot see *runtime* — isolate boundaries,
event ordering, cold vs warm, OEM quirks. For `device-manual` work, make the
plan buy down the human's root-causing cost up front (observability + a concrete
runtime checklist) and verify it on a device before stacking more on top.
