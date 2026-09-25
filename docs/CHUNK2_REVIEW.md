# Chunk 2 — companion experience

Status: implementation and integration review complete; owner acceptance pending.
Native compilation, deterministic checks and the bounded Gemini canvas path pass.
This document does not claim physical desktop-action or voice verification.

## Approved experience

- One quiet floating particle orb, without a solid shell or hard rim. All states
  share the approved Thinking curvature; distinguish activity through restrained
  motion rather than spiky deformation. Preserve the compact 56-point footprint.
- Clicking the orb opens a compact, rounded current-query/current-answer bubble.
  Session lists, model controls and technical inspection belong in the full app.
- Expand opens a polished session-based chat app. Conversation, streaming text,
  attachments and saved artifacts remain continuous across surfaces.
- A desktop canvas presents interactive explanations and pointing/drawing over
  other applications. Never close another app simply to clear drawing space.
- Generated content uses bounded, validated native components, not executable
  model-generated JavaScript, shell commands or arbitrary HTML.
- Memory gets its own searchable, inspectable and deletable surface. Technical
  activity, provider/voice settings and tool controls remain available on demand.
- Click/type/hide proposals require a specific approval and target revalidation.
  Screen, web, memory and generated visual content never grant authority.

## Implementation and review workflow

One parallel batch of three GPT-6 Luna agents at extra-high reasoning effort owns
native companion/actions, full-app/canvas, and backend contracts respectively.
Each receives one implementation phase and one review/fix phase. The orchestrator
then owns remaining integration and fixes without further delegation rounds.

Preserve the canonical Python runner, SQLite authority/Lance projection, one
Application Support runtime, three Standard speech choices and per-engine voices,
Option-Z record/send and Option-X cancel. No new model downloads, framework
migration, wake word, continuous monitoring, recursive runtime agents, deployment,
commit or push are part of this slice.

## Verification completed

- **94 offline Python tests passed**; Ruff lint/format, dependency checks and
  tracked diff whitespace checks passed.
- Native gesture/orb/bubble/action checks and the separate Playground state/
  strict-artifact harness passed. Shared artifact/action fixtures cross the Python
  and Swift boundary. The Playground target requires `-parse-as-library` in CI.
- `zsh mac/build.sh` rebuilt the app and installed the current Python wheel into
  the existing single Application Support runtime. No application was launched,
  force-quit, clicked, typed into or captured during this review.
- Synthetic NSHostingView renders were inspected in light and dark appearance
  for the orb states, bubble, full app and desktop canvas. The render harness is
  `mac/tests/visual/main.swift`; generated PNGs remain ignored in
  `mac/build/visual-review/`. Earlier ImageRenderer scroll-content blanks were
  not present in the final NSHostingView renders.
- The three agents each had one implementation phase and one review/fix phase.
  All stopped before final parent-owned fixes and verification; there were no
  additional delegation rounds.

## Parent-owned integration repairs

- Enabled canvas for fresh native settings as well as migrated saved tool lists.
- Matched particle radius/diameter scaling and resolved native drawing colors in
  the window's appearance; removed hardcoded white dark-mode control backgrounds.
- Replaced the remaining framed canvas card with centered borderless content,
  a click-through desktop focus veil and full-display normalized drawing marks.
  This leaves underlying applications open. Marks are display-relative, not
  persistent semantic anchors attached to moving controls in other applications.
- Rejected late source/media events after cancellation; retained saved artifacts
  for reopening while keeping presentation visibility separate.
- Added last-moment policy checks and cancellation-ticket cleanup for native
  actions. Exact-window focus preparation and delayed execution are separate,
  with a second target/field/expiry check before clicking or inserting text.
- Raised the main output ceiling from the text-era 512 to 2,048 tokens so tool
  arguments have room; added incomplete-provider diagnostics without logging
  generated or private content. Existing explicitly bounded harnesses retain
  their own smaller limits.

## Acceptance coverage and remaining owner checks

- Deterministic production-path tests for artifact validation, persistence,
  conversation ownership, reopening and interactions.
- Approval denial, timeout, cancellation, stale-response and target checks.
- Native model tests, shortcut/gesture regression checks and full compilation.
- Synthetic bounded provider check for the new tool path, with no fallback or
  automatic retry. Keep any failed or skipped path explicitly unverified.
- Owner inspection of visual fidelity, orb motion, bubble positioning across
  screens, canvas controls, conversation continuity, memory/settings and voice.
- Owner checks actual Accessibility permission and approve/deny/cancel behavior
  in a disposable non-sensitive target application. Automated test runs must not
  click or type into the owner's working apps.

Chunk 1's upstream GLM maintenance 429 and outstanding physical capture/voice
acceptance remain separate unresolved items until freshly verified.

## Reproduce the synthetic canvas check

```sh
PYTHONPATH=src .venv/bin/python -m aloy.chunk2_smoke --mode fake
# Explicit paid check: synthetic input, two dispatches maximum, no capture/actions.
PYTHONPATH=src .venv/bin/python -m aloy.chunk2_smoke --mode live --provider gemini
```

The live harness reserves $0.10 against the actual provider ledger, uses a
temporary synthetic conversation store, and settles the measured or conservative
cost. No helper/maintenance/speech calls or automatic retries run in this check.
The first Gemini attempt reached the two-step limit rather than returning a final
answer; that attempt is a failure, not evidence of a working live canvas path.
A second bounded attempt ended without a completed interaction. After schema
clarification and raising the text-era output budget to 2,048 tokens, the final
Gemini check passed in 6.95 seconds: exactly two dispatches, one persisted native
artifact, and a final response. Measured token usage was 1,769/124 followed by
2,184/12 input/output tokens; estimated cost of that successful run was $0.0015259.
Earlier failed attempts remain separate failures and were settled conservatively;
the successful-run cost is not a claim about total review spend. No cap was raised
above two dispatches, and no automatic retry or provider substitution occurred.

## Owner acceptance scenarios

Quit the previous Aloy process yourself and reopen the newly built app. Building
does not replace a process already running, and this review does not automate your
desktop.

1. Click/drag the orb. Confirm the airy Thinking curvature remains consistent in
   Quiet, Listening, Thinking, Speaking and Needs you. Check Reduce Motion and
   both macOS appearances. There must be no plastic shell or spiky error state.
2. Ask a short question with Option–Z; click the orb during the response. The
   bubble shows only that exchange. Expand without cancelling or duplicating it.
   Option–X stops recording, playback and pending work.
3. Ask “Show German verb-second word order on the canvas, with a sentence I can
   rearrange and two answer choices.” Confirm it appears above a non-sensitive
   app; transform/choose, dismiss and reopen it from the conversation. Resize,
   switch conversations and check that content never leaks across sessions.
4. Create/rename/search a session; inspect an attachment and saved artifact.
   Search and delete a disposable memory. Verify all three Standard speech
   engines retain their individual voice selection in Settings.
5. Desktop actions start disabled. In a disposable target such as an empty
   TextEdit document, explicitly enable only the action you intend to test and
   the approval policy. Request screen inspection first so the target is known.
   Deny once, approve one harmless insertion/click, then test Option–X while an
   approval is pending. Change the target window/field and confirm stale actions
   are rejected. Hiding must never close or discard the target app's document.

Do not use real credentials, unsaved important documents, messages or purchases
for action acceptance. Actual macOS Accessibility/Screen Recording grants, focus,
multi-monitor behavior and perceived motion/voice quality remain human checks.
