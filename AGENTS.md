# Aloy agent instructions

Keep responses concise. Read README.md, docs/ENGINEERING.md and docs/INITIAL_PLAN.md
before work. If present, also read local MEMORY.md, .local/CONTEXT.md and
.local/HANDOVER.md. These ignored files contain private owner context; do not publish them.

Use Python 3.13 with standard venv and pip. Development pins live in requirements-dev.txt.
Do not introduce a parallel dependency workflow.

## Non-negotiable architecture

- Write our own orchestration functions and classes. No LangChain, LlamaIndex,
  agent frameworks or equivalent orchestration dependencies. Ordinary SDKs,
  protocol clients, validation, transport, database and testing libraries are allowed.
- Apply DRY and SOLID with small cohesive modules and explicit typed contracts.
- One canonical agent execution pipeline serves all personas, providers, entry points
  and future subagents. Fix and extend that pipeline; never create a shadow loop.
- Shared functions own context assembly, tool dispatch, validation, persistence,
  cancellation, retries, usage accounting and event emission. Adapters translate
  provider protocols and must not implement independent orchestration.
- Agents are configured by persona/name, system prompt, model, tools, MCP servers,
  skills, input/images, output format and max turns. Configuration is validated.
- Subagent spawning is FUTURE scope. When added, use the same runtime with inherited
  authority ceilings, shared budget accounting, cancellation and bounded depth.
- Every implementation change includes appropriate checks and meaningful behavior
  tests for changed runtime behavior. No duplicate code path to make a test pass.
- The owner explicitly approved implementing the first companion and speech foundation
  in docs/INITIAL_PLAN.md. Curriculum, generative lesson visuals, tools, screen context,
  wake words and background jobs remain deferred until separately scoped.
- Keep Aloy's local footprint lean. Download only selected weight files into its dedicated
  cache. After comparisons, remove losing models, stale temporary files and unneeded build
  artifacts; preserve conversations and user recordings. Never prune unrelated caches.
- Keep the one Mac runtime under Application Support; the project `.venv` is a link.
  `mac/build.sh` installs the current wheel there before compiling the shell. Do not
  put credentials in the app bundle or add a second speech environment.
- Orb redesign is now authorized: keep the existing 56-point footprint, use a dense
  quiet particle sphere, and make recording/processing/speaking/error states legible.

## Required testing policy

- Follow the canonical two-mode testing contract in docs/ENGINEERING.md.
- Default `fake` mode is deterministic, offline and free; explicit `live` mode runs
  one bounded cheap-provider generation request through the same production pipeline.
- Select the mode in the test harness through dependency injection, never a parallel
  implementation or production-wide testing flag. Ordinary CI must remain offline.
- Before closing a provider-backed feature, relevant unit tests AND the applicable
  live integration check must pass. Missing credentials or a skipped/failed live check
  are an unverified result, never a fake-backed success. Documentation-only changes
  do not require spending money on a live call.
- Enforce the live request cap across SDK retries, continuations and model fallbacks.
  Use synthetic input, output/cost limits, a timeout and secret-safe usage evidence.
- One live call verifies only its exercised path. Multi-turn/tool scenarios need
  separately bounded integration tests; do not silently expand the one-call check.

## Data and workflow

Public repository: never commit credentials, recordings, learner profiles, screen
captures, personal memory, account data, local machine diagnostics or databases.
Keep source changes narrow and preserve unrelated work. Do not reset, stash or discard.
Research/read-only inspection is allowed. Sending messages or other external actions
requires explicit authorization. No application framework implies permission to act.

## Repair invariants

- Read docs/REPAIR_REVIEW.md for current verification; older BUILD_EVIDENCE is historical.
- Keep SQLite mutations in the explicit transaction helper. New migrations are numbered
  package SQL files. Only the exclusive desktop backend invokes recovery.
- All paid paths reserve and settle through the shared ledger; incomplete dispatched work
  retains a conservative allowance. Never report a zero cost merely because usage is absent.
- Test source via pytest's configured pythonpath; mac/build.sh installs a wheel, so the
  environment may also contain an older installed package during development.
- Tag asynchronous transport events with operation IDs. A stop/mode change invalidates old
  playback and events. Cancel recording retains the audio locally; Finish & Send transcribes.
- Wake words, automatic turn detection and interruption are required for the eventual
  product. The next interaction round may start with a toggle-to-record keyboard shortcut;
  do not claim these features are delivered by the current manual recording controls.

## Current activation priority

One-hand shortcuts only: Option + Z records/sends; Option + X cancels. Option + Space
belongs to Codex and must not be registered by Aloy. Prioritize agent functionality
and its supporting capabilities. Hey Aloy voice activation remains planned for a
soon-following phase, explicitly deferred for now. Do not implement or download
wake-word components until that phase is requested.

Speech quality work is offline-first. Paid speech is a last resort, never an automatic
fallback. Detect speech before ASR; no-speech captures must never reach the agent.
Compare candidates on actual local recordings and human voice auditions; do not claim
a globally best model from a tiny benchmark. Keep only the selected model weights.
