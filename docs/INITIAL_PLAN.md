# Initial infrastructure plan

Status: earlier long-range architecture roadmap. The approved first companion build is
described in [FIRST_BUILD_SCOPE.md](FIRST_BUILD_SCOPE.md) and its measured results in
[BUILD_EVIDENCE.md](BUILD_EVIDENCE.md). This roadmap's tool, scheduler, canvas and
subagent phases remain future work.

All phases follow the fake/live testing contract in docs/ENGINEERING.md. Build the
offline harness in phase 1 and an explicitly selected, one-generation-request live
check in phase 2. Both inject their provider into the same production runtime.

## 1. Core contracts and deterministic execution

Implement AgentSpec, AgentInput, AgentResult, normalized streaming events and an async
AgentRunner with dependency injection. Start with text and a fake provider so the
orchestration can be tested without credentials or paid calls. Include image input in
the contract, typed output validation, cancellation, deadlines and clear max-turn rules.
Exit: deterministic tests for text, structured result, failures and termination.

## 2. Tool pipeline and first real provider

Implement one registry/dispatcher for local tools, typed argument validation and a
bounded continuation loop. Add one inexpensive direct provider adapter selected by a
fresh capability/latency/pricing comparison. Start with one read-only tool.
Exit: a real streamed turn and a tool continuation use the same runtime; malformed tool
arguments, failure, cancellation and loop limits are tested. Paid calls require a cap.

## 3. Codex and provider interchangeability

Integrate documented Codex app-server authentication and events. Preserve its approval
semantics and expose provider-managed execution honestly. Add additional API adapters
only after the same contract tests can exercise them. No credential extraction or
subscription-token proxy. Choose model IDs via current capability discovery/config.
Exit: the same AgentSpec entry point supports direct API and Codex-backed work, with
explicit differences and no duplicate business logic.

## 4. Durable local context and proactive lifecycle

SQLite stores runs, lessons, learner evidence, review due dates and scheduled intents.
Bounded context retrieval composes the next session. An idle local scheduler records
missed triggers and resumes after wake; it invokes the existing runner. No continuous
cloud inference while idle. Quiet hours and snooze are owner-controlled.
Exit: restart/resume, duplicate-trigger protection and next-day recall work in tests.

## 5. Voice and coordinated visuals

Benchmark German speech recognition, synthesis and audio-aware feedback on the target
machine. Begin with text-to-speech and transcription adapters around the existing
runtime, streamed sentence playback and reliable interruption. Use a persistent lesson
canvas for typed sentence blocks, transformations, timelines and exercises. A small
macOS companion shell can be added after the full lesson works; Swift versus a webview
shell remains a measured choice, not a prerequisite for the core.
Exit: a useful 30-minute lesson, interrupted speech without stale visual updates, and
an accurately resumed lesson the next day. Assess actual audio for pronunciation;
transcript correctness is not pronunciation evidence.

## Model evaluation, not fixed vendor allegiance

Compare current Gemini Flash/Lite, DeepSeek Flash and selected OpenRouter alternatives,
with Codex subscription access for suitable work. Evaluate German correctness, latency,
structured visual output, tool reliability, retained context and whole-lesson cost.
Benchmark local speech versus paid speech and native audio. Preserve persona and memory
across routing; no unnecessary call to a second formatter for every spoken response.
Recheck current model IDs, quotas, provider pricing and promotional expiry dates.

## Deferred

General desktop control, WhatsApp/mail workflows, public portfolio reuse, arbitrary
skills installation and recursive subagents. The reusable runtime must accommodate
these without building them now. Keep public portfolio knowledge separate from private
assistant context.

## Resource policy

Begin without Docker, model downloads, always-on services or a vector database. Add
only measured dependencies. Keep model cache centralized and audio retention bounded.
The personal budget, learner history and machine inventory live only in local context.
