# Case study: WeChatRelay — what the loop shipped "green" but was broken

> A field log from building WeChatRelay v1 (Flutter Android app + FastAPI
> server) with `/ilk-plan` + `/ilk-run`, then verifying on a real Huawei P60
> (HarmonyOS 4.2). Source of concrete improvement proposals for `/ilk-plan`
> and `/ilk` (ilk-run). Humans apply the changes — this doc is the evidence.

## What happened (timeline)

5 master batches, planned + run via the loop:

1. `wechatrelay-v1` — scaffold, websocket-transport, android-p2-fetch, vps-ocr,
   resilience-and-ui, openclaw-integration. **Shipped green.**
2. `wechatrelay-v1-bugfix` — settings-persist-and-wire, home-status-and-reconnect,
   repo-hygiene. **Shipped green.**
3. `wechatrelay-v1-p2-capture` — isolate-bridge-fetch, mediaprojection-capture.
   **Shipped green.**
4. `wechatrelay-server-quality` — ocr-quality, multi-device, task-history+robustness,
   docs+primer. **Shipped green.**
5. `wechatrelay-v1.1-extensions` — batch-fetch, result-archive, monitoring, CLI, CI.
   **Shipped green.**

All 20 sub-plans reported `shipped`. Then manual verification (server `pytest`
re-run + real-device e2e) found **8 distinct bugs the loop's gates passed**:

| # | Bug | Surface | Why the loop missed it |
|---|---|---|---|
| 1 | Test-state leak: module-level singletons (`registry`/`_tasks`/`_device_sockets`) not reset between tests | server | per-file `local_checks` (`pytest <one file>`) — bug only shows in the full suite |
| 2 | ws round-trip test broke when a later sub-plan added OCR-on-result | server | same — the OCR sub-plan's gate didn't run the transport test |
| 3 | `usesCleartextTraffic` missing → `ws://` blocked on Android 9+ | app | `flutter analyze`/`build apk` pass; only fails at runtime on a device |
| 4 | `main.dart` hardcoded `localhost:8000`, never read the settings UI | app | compiles + builds; Settings screen "works" in isolation |
| 5 | Background-service isolate crash-looped + connect-event race | app | runtime-only; analyze/build green |
| 6 | Fetch bridge called `FlutterBackgroundService()` in the **service** isolate → `MissingPluginException` on `sendData` | app | runtime-only; correct-looking code |
| 7 | MediaProjection capture called `acquireLatestImage()` before any frame existed → "No image captured" (comment even claimed "wait up to 5s" — there was no wait) | app/native | runtime-only; native code can't be loop-tested |
| 8 | `ACTION_VIEW` on `mp.weixin.qq.com` offers a chooser of {Huawei browser, 微信读书}, **not 微信 (WeChat)** — the "Intent 唤起微信" design assumption is false | design | only discoverable on a real device with the real app set installed |

**Pattern:** every bug fell into one of two buckets — (a) **integration** bugs
hidden by per-file gates, or (b) **runtime/device/platform** bugs that
`analyze`/`build`/`compile` fundamentally cannot catch. The server batches
(gated by a full `pytest` that boots the live app) were genuinely correct;
the app/native batches shipped *compile-green but broken*.

## What worked (keep)

- **Splitting master batches by verification reachability.** Putting
  pytest-verifiable server work in its own batch made it trustworthy to run
  unattended overnight (it really worked on wake-up). Isolating device/native
  work into its own batch set the right expectation: "ships as scaffolding,
  needs a human+device pass."
- **The watchdog** ran 3 queued masters overnight, auto-promoting on clean ship,
  and would have blocked (not loop-burned) on `stuck-no-progress`. No incidents.
- **Prescriptive architecture** in the device sub-plans (exact APIs, the
  bg→UI isolate bridge design) reduced — though didn't eliminate — blind-code
  errors.
- **`-RunLocalChecks`** + the "last step = FULL suite" rule we added mid-way
  caught regressions #1/#2 on the *next* batch.

## Proposed improvements

### To `/ilk-plan` (and `decomposition-principles.md`)

**P1 — Tag every sub-plan with a `verification_tier`.** Add a required
frontmatter field:
```yaml
verification_tier: loop-verified | compile-only | device-manual
```
- `loop-verified` — a runtime gate proves correctness in-loop (pytest boots the
  app, a real HTTP/CLI smoke runs). Trustworthy when `shipped`.
- `compile-only` — only `analyze`/`build`/`tsc` runs. Ships scaffolding; a human
  must verify behavior.
- `device-manual` — correctness needs a physical device / GUI / external app.
ilk-plan sets this per sub-plan; the step-5 proposal table gains a `Tier` column
so the user sees up front what will and won't be trustworthy.

**P2 — Dependency rule: never queue a sub-plan whose runtime correctness
depends on a `compile-only`/`device-manual` sub-plan that hasn't been
human-verified.** (Don't build blind on blind.) In this project, queuing more
capture-dependent work behind `mediaprojection-capture` would have stacked
unverified on unverified. Server batches were correctly kept independent of the
capture.

**P3 — Default the LAST step of every sub-plan to the FULL test suite**, not
just the new file, whenever the change touches a shared module. Add "per-file-
only gate on a shared module" to the §8 anti-pattern lint. (Bugs #1/#2.)

**P4 — Batch by tier + recommend a run mode.** ilk-plan should group
`loop-verified` sub-plans into autonomous batches and `compile-only`/
`device-manual` ones into supervised/human-paired batches, and say so in the
MASTER rollout section. This was done by hand here and was the single biggest
win — codify it.

**P5 — For blind platform work, require a "runtime failure-mode checklist" in
the Manual section.** Enumerate the specific things a human must check that the
build can't: isolate boundaries, frame/timing on async capture, permission/
consent timing, intent resolution, cross-process channels. (Bugs #5–#8 were all
"compiles, wrong at runtime" in ways a checklist would have pre-flagged.)

**P6 — "Restart affected long-running services after the loop changes their
code."** A dev server started before the loop kept serving stale code, so manual
verification hit removed/renamed endpoints (HTTP 405). Add to env_prereqs / a
post-ship note for sub-plans that modify a running service.

### To `/ilk` (ilk-run) and status/feedback

**R1 — Report `verification_tier` in the ship summary + `/ilk-status`.** Today
all 20 sub-plans showed an identical `shipped`. The human had no signal that 7
of them were compile-only-and-actually-broken. The end-of-run summary should
list `device-manual`/`compile-only` shipped sub-plans as an explicit
"NEEDS HUMAN VERIFICATION" TODO block.

**R2 — `/ilk-feedback` should down-weight `clean-success` when the batch
contained `compile-only`/`device-manual` sub-plans** — i.e. classify as
`shipped-unverified`, not `clean-success`, so the postmortem tells the truth.
(Mirrors decomposition-principles §11 but makes it visible in the taxonomy.)

**R3 — Keep the watchdog block-on-stuck behavior; it behaved correctly.**

## One-line takeaway

The loop is excellent at *constructive, runtime-gated* work (the server batches
were perfect) and dangerous at *blind platform/UI/device* work, where "shipped"
means "compiled." Make the plan say which is which, run the trustworthy half
unattended, and pair with a human + device for the rest.
