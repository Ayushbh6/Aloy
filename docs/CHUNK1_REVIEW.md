# Chunk 1 implementation audit — 2026-09-25

Scope: the approved Agent, Memory and Perception Foundation plan, based on
`be8f863`. Code is implemented and the app builds. **Final acceptance is not yet
complete:** the current GLM structured-maintenance live check is blocked by
upstream HTTP 429 responses, and physical Mac interaction/capture checks remain
owner-run. Earlier successful maintenance checks do not supersede this current
result. No computer-use UI automation was performed during this audit.

## Requirement traceability

| Plan requirement | Implementation and evidence |
| --- | --- |
| One custom agent pipeline | `agent.py`: streamed `AgentRunner`; `ChatAgent` delegates to it. Fake, Gemini, OpenRouter and Codex use the same context, policy, tools, persistence and ledger. Live continuation passes for all three remote routes. |
| Typed contracts, limits, schemas | `contracts.py`: AgentSpec/Input, ProviderTurn, tool contracts, expanded events, max steps, policy and output schema. JSON Schema validation rejects malformed calls and outputs. Offline limit, unknown-tool, schema and deadline tests. |
| Provider sessions | Context revision, registry/policy/schema hash, history count and age gate reuse. Media starts fresh. Offline tests verify reuse and invalidation. Codex dynamic tools run with experimental capability; shell/browser/MCP remain disabled. |
| Web search | Separately metered Gemini grounded interaction; normalized persisted sources, citations and query-count charging. Live search returned citations. Reservation covers eight search queries; this is a conservative allowance, not control over Google's internal query count. |
| Durable memory | SQLite evidence/category/scope/confidence, explicit remember/forget, stable-fact extraction, credential screening, deduplication/revision. Tool writes must be grounded in the current user input; a model cannot authorize forgetting. Offline structured extraction passes; current remote maintenance check is blocked. |
| Continued conversations | Central 30,000-token target, latest eight completed exchanges verbatim, rolling versioned summaries and bounded memories/history retrieval. Failed/in-progress messages never duplicate the current input. Required compaction failure prevents main dispatch. Raw messages remain intact. |
| LanceDB alongside SQLite | Pinned 0.39.0; installed Ollama `embeddinggemma:latest`, 768 dimensions and digest fingerprint. Full-message overlapping chunks, vector/FTS reciprocal-rank fusion, SQLite exact/substring/timeout-bounded regex. No raw media index. |
| Outbox and rebuild | SQLite commits first. Process-locked idempotent drain, immediate canonical validation of search results, pending work survives failures. Rebuild snapshots SQLite plus an outbox watermark so concurrent changes stay pending. Model changes require explicit rebuild. Actual local Ollama/Lance rebuild and recall passed. |
| Screen perception | ScreenCaptureKit foreground-window snapshot or 1–60-second clip; system audio enabled, microphone disabled, persistent elapsed indicator. Current-turn authorization, negative-request rejection, one capture of each kind per run; no control actions or continuous monitoring. Native build passes; actual permission/target/audio checks are still manual. |
| Media understanding | Strict timestamped observations through Gemini Flash or Qwen Omni. Private bounded transcoding, four attachment maximum, direct Codex image input, selected vision-route evidence for Codex audio/video. Live synthetic images/videos pass on both vision routes; direct Codex image passes. |
| Playground | Separate SwiftUI-hosted NSWindow with conversations, provider/vision selection, tool toggles, prompt/attachments, send/cancel, timeline/errors/durations and inspectable payloads, sources/media and memory list/search/delete. Native event/state tests pass. Existing panel/orb retained. |
| Bridge and artifacts | v2 events, v1 actions retained. Read-only Expand hydration does not cancel active work and restores partial text. Versioned artifact persistence/event seam tested; no generative renderer. |
| Cancellation and recovery | Cancellable runner task and streams/tools/helpers/speech; capture cancellation packet reaches native task. Runs, steps and provider calls become interrupted on crash recovery. Uncertain dispatched reservations remain charged. Offline cancellation and recovery tests pass. |
| Spend and routing | Separate main/maintenance/web_search/vision call records and Gemini/OpenRouter ledger attribution. No duplicate run charge, no invented Codex dollars or unknown usage. No automatic retries or provider fallback. GLM 5.3 Flash and both specified vision IDs retained. |
| Preserved boundaries | Three Standard speech engines and voice pickers, Option–Z/Option–X, private Application Support runtime/media, no new speech weights, no wake word, no click/type controls. Unrelated `docs/feature_analysis_v1.md` left untouched and untracked. |

## Gaps repaired during review

- Replaced the remaining legacy agent logic with an actual compatibility wrapper;
  streamed deltas/tool activity now arrive before provider completion.
- Removed double accounting; unknown usage stays unknown. Helper cancellations
  and context/maintenance steps are recorded with the correct outcome.
- Excluded failed/active turns from context, added summary FTS, and fixed complete
  message indexing, full-tail chunking, stale-memory visibility and rebuild races.
- Centralized credential rejection and prevented retrieved/tool text from granting
  capture, arbitrary memory-write or memory-delete authority.
- Added structured media evidence and bounded video/audio preparation. Qwen's
  initial schema rejection and timeout were repaired with explicit schema guidance,
  supported-parameter routing and reasoning disabled on the vision-only request.
- Fixed a bridge argument-name collision that prevented capture requests from
  being emitted; added a real-wire cancellation regression test.
- Fixed Expand cancelling active work, missing already-streamed text, repeated
  sends, stale attachment state, hidden tool payloads and media-player resets.
- Updated native CI compilation to include Playground and ScreenCapture sources.

## Verification

- Python: **80 offline tests passed**, including network guard and actual LanceDB
  tests with synthetic embeddings; Ruff lint/format and dependency checks pass.
- Native: `mac/build.sh` passes; orb/shortcut/interaction tests and separate
  Playground event/state tests pass. The build installs the current wheel into
  the existing single runtime. It does not relaunch a running app.
- Actual local Ollama embedding plus Lance rebuild/query: passed, 768 dimensions.
- Synthetic live results through production code:

| Scenario | Result |
| --- | --- |
| Gemini tool call + continuation | Passed, two dispatches |
| OpenRouter GLM tool call + continuation | Passed, two dispatches |
| Codex dynamic-tool callback | Passed, one provider-managed turn; usage recorded, dollar cost unknown |
| Gemini grounded web search | Passed, one helper dispatch and cited sources |
| Gemini image / two-second video | Both passed, one helper dispatch each |
| Qwen Omni image / two-second video | Both passed after request repair, one helper dispatch each |
| Codex direct synthetic image | Passed, one provider-managed turn |
| GLM strict extraction + compaction | **Blocked: upstream 429**, no fallback; rerun required |

Media fixtures contain solid colors and a synthetic tone, not private screen or
microphone content. These checks validate protocol/routing/schema behavior, not
general perception accuracy, human voice quality or physical capture permissions.
Automatic memory extraction and long-conversation live compaction cannot receive
final sign-off until the maintenance scenario passes.

Reproduce one named check (each is an explicit bounded spend decision):

```sh
PYTHONPATH=src .venv/bin/python -m aloy.chunk1_smoke --mode live --scenario maintenance
```

Available scenarios: `tools-gemini`, `tools-openrouter`, `tools-codex`, `maintenance`,
`web`, `image-gemini`, `video-gemini`, `image-openrouter`, `video-openrouter`,
`image-codex`. Default mode is fake; ordinary pytest is offline.

## Owner acceptance checklist

1. Quit the old Aloy process, then open `mac/build/Aloy.app`. Click **Expand**.
   Expect a separate Playground, not a redesigned compact panel. Send a prompt,
   close/reopen or expand during streaming: no cancellation, duplication or lost text.
2. In one conversation, introduce a synthetic fact, then switch Gemini → GLM →
   Codex and ask a follow-up. Expect the same history and inspectable tool results.
   Search the web and check that source links and run durations appear.
3. Say “Remember that my test project is Apricot.” Open a new conversation and ask
   for the test project. Find it in Memory, then forget/delete it and confirm that
   the durable record disappears. Raw history remains until its conversation is deleted.
4. Put a non-sensitive window in front, then ask “Check my screen.” Grant macOS
   Screen Recording permission if prompted. Expect that window—not an arbitrary
   monitor or background window—and an image preview with grounded observations.
5. Play a non-sensitive short video with sound and ask “Record this video for five
   seconds and describe it.” Verify the visible timer, saved clip, system audio,
   timestamped observations and **no microphone sound**. Repeat and press Option–X:
   recording/indicator must stop and no late answer or playback should resume.
6. Try an ambiguous request without capture authorization; expect a clarification,
   not recording. Try disabled tools and a denied screen permission; expect explicit
   errors without substitution. Inspect a small attachment with each vision route.
7. Check Option–Z record/send, Option–X cancel, and all three Standard speech engines
   with their saved voice choices. Restart and verify conversation persistence.
   Delete only a disposable test conversation; its associated media should disappear.

After the blocked live maintenance check passes, exercise automatic extraction
(a naturally stated preference without “remember”) and a long synthetic conversation
that triggers compaction. Confirm summary coverage advances while old messages are
still present and follow-up questions remain coherent.

## References and intentional exclusions

Provider contracts were checked against [Gemini Interactions](https://ai.google.dev/gemini-api/docs/streaming),
[Codex app-server](https://developers.openai.com/codex/app-server/), and
[OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs).
Structured-output enforcement varies by endpoint; Aloy validates locally and does
not silently repair or retry malformed output.

Chunk 2 owns the polished full chat shell/Memory page, generative UI renderer,
Clicky-style overlays, and approval-gated clicking/typing. Hey Aloy remains deferred.
