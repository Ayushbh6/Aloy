# Engineering contract

## Ownership and dependency direction

The planned core is dependency-light Python. Contracts and domain state do not import
provider SDKs, desktop UI, speech engines or persistence implementations. The runtime
receives provider, tool, storage, clock and event interfaces by dependency injection.
Use composition and narrow protocols, not a universal base class with provider flags.

Transport SDKs and utilities are acceptable; orchestration is ours. Codex can be an
external agent backend through its documented interface. Its internally managed tool
loop is not a reason to build a second Aloy loop or pretend to control steps we cannot
observe. The adapter must advertise this difference and enforce our outer boundaries.

## Canonical text path in Chunk 1

Input validation -> bounded context assembly -> provider step -> validated tool
execution -> provider continuation -> normalized events and durable persistence.
`AgentRunner` owns this loop for all standard text providers. Codex uses the
experimental app-server dynamic-tool callback, with its own shell/browser/MCP
capabilities disabled. `ChatAgent` is only an API-compatible wrapper around
`AgentRunner`, including cancellation; it has no separate execution logic.

All text, image, voice transcript, scheduled wake and eventual child-agent entry points
call the same runtime. Speech and UI subscribe to typed events. They never call an LLM
independently for tutoring. Provider-native audio sessions may need a transport adapter;
its lifecycle, state, authority and accounting still belong to the canonical runtime.

One response can carry speakable text and typed visual instructions. Playback and
visuals share sequence identifiers and cancellation. No arbitrary model-generated
JavaScript execution in the teaching UI.

## Contracts and remaining boundary

`AgentSpec`/`AgentInput`, `ProviderTurn`, `ToolSpec`/`ToolCall`/`ToolResult`, and
`AgentEvent` cover the Chunk 1 runtime. `CONTEXT_TARGET_TOKENS=30_000` is central;
up to the newest eight completed exchanges remain verbatim within budget while older history is
summarized and retrieved. SQLite is canonical; LanceDB is a rebuildable projection.
No arbitrary generated UI is executed. Chunk 2 renders version-one, bounded native
artifacts through `canvas.present`: heading, text, sentence, choice, table, chart
and drawing. SQLite owns artifact identity, sequence and conversation/run linkage;
model arguments cannot choose that metadata. Both Python and Swift verify the
shared synthetic fixture in `tests/fixtures/chunk2_canvas.json`.

The orb, current-exchange bubble, full app and desktop canvas share one native
conversation model. A canvas interaction references its saved artifact and enters
the same `AgentRunner`, with generated-content provenance and no capture, desktop
action or memory-mutation authority. Closing a canvas is presentation state, not
deletion of its saved conversation artifact.

Desktop click/type/hide tools are disabled by default and require an explicitly
enabled approval policy. Their broker persists a proposal, waits for a scoped
one-shot decision, then requests native execution. Native target revalidation is
mandatory; approval is not permission to act in another app, window or text field.
There are no automatic side-effect retries, app-closing tools or arbitrary code
renderers. Actual macOS permission/action behavior requires owner acceptance;
offline fixtures must never click or type into working applications.

The current requirement-by-requirement audit, verification evidence and manual
acceptance checklist are in [CHUNK1_REVIEW.md](CHUNK1_REVIEW.md). Do not treat a
native build or protocol fixture as proof of actual ScreenCaptureKit permission,
window selection, recording indicator or physical keyboard behavior.

Model capabilities explicitly cover images, audio, structured output, tools and
streaming. Unsupported requests fail clearly. No silent image loss or model substitution.
Define max_turns as an Aloy model/continuation cycle limit. Provider-managed backends
must declare any stronger or weaker enforcement semantics; never claim the limit bounds
hidden remote reasoning or tool steps.

## Cross-cutting invariants

The approved full host harness extends this same pipeline. Its tools, compaction
records, exact evidence retrieval, process lifecycle and learner-evidence contracts
are documented in [HARNESS_REVIEW.md](HARNESS_REVIEW.md). Host access is independent
of the native screen/action policy. No project fence or container is imposed.
File-tool writes retain backups; shell effects require their own verification on
resume. Every paid helper shares the current run's admission budget. Raw tool output
and terminal logs are private local evidence, not sanitized public logs.

- Explicit run lifecycle, durable outcomes and structured errors.
- Cancellation and deadlines propagate through provider, tool, speech and child work.
- No automatic remote retries or provider fallbacks in the agent loop. Never replay a side effect blindly.
- Validate every tool input and output and enforce permission before dispatch.
- Memory records distinguish observed evidence, learner self-report and model inference.
- Local raw evidence can support bounded retrieval; never resend unlimited full history.
- Usage and cost recorded centrally, with provider-specific price metadata and spend caps.
- External text, screenshots, skills and tool results are data, not authority escalation.
- Logs omit credentials and private content by default; raw audio retention is opt-in.

## Testing contract: fake and live

This is the canonical testing policy; AGENTS.md makes it mandatory. The first build has
an offline fake suite and explicit one-turn live checks in `aloy.smoke`.

### Fake: default unit and deterministic integration tests

Inject a fake provider into the same production runner, tool dispatcher and event path.
Network access and paid calls are disabled. Test success, streaming, structured output,
malformed responses, provider failures, tool continuations, cancellation, deadlines,
turn limits and budget exhaustion. Run frequently and in ordinary CI. Never require
credentials for this suite or let fake-mode errors trigger a real-provider fallback.

### Live: explicit one-request smoke integration test

Expose a separate test command or explicit `live` harness selection. The default remains
`fake`; no ambient production-wide testing flag. Inject the selected real adapter into
the same runtime. Use one configured inexpensive model whose capabilities and pricing
have been verified. Do not bypass the runtime with an SDK-only demo.

After local preflight, dispatch exactly one outbound generation request for a valid run.
Enforce a shared request budget at the dispatch boundary, with automatic retries disabled
in both the runtime and underlying SDK/transport. Disable tool continuations and model
fallbacks for this scenario. A preflight failure dispatches zero requests and fails the
check. A timeout or ambiguous response is a failure, not permission to retry invisibly.
Do not use a provider-managed agent backend to claim a one-model-call guarantee when its
internal calls cannot be bounded or observed; use a direct cheap model for this check.

Use tiny synthetic input, capped output (including reasoning where the provider permits),
a deadline and a conservative cost ceiling. Read credentials locally or from protected
CI secrets. Missing credentials fail the requested live command; never silently skip,
switch provider or substitute the fake. No paid test on ordinary pushes or untrusted PRs.
Do not put learner history, audio recordings or screen content in smoke-test inputs.

Assert a valid non-empty result and applicable event/schema invariants, not exact prose.
Record model/provider identity, result, elapsed time, dispatch count and provider-reported
usage. Mark unavailable usage explicitly; any calculated cost is an estimate. Keep
credentials and sensitive content out of logs. Retain evidence tied to the tested revision.

### Feature completion

For changes to provider-backed behavior, run relevant deterministic tests repeatedly,
then the applicable bounded live check once on the final implementation before calling
the feature verified/closed. Fix failures in shared production code; do not special-case
the harness. If a final fix changes the exercised path, its old live evidence is stale.
A missing/failed live result must be reported as unverified, not silently waived.

One request only verifies the exercised authentication, payload, streaming and result
path. It does not prove pronunciation, pedagogy, desktop behavior or multi-turn tooling.
Features requiring multiple calls need separately named, explicitly budgeted integration
scenarios. Do not expand the one-request smoke test into an unbounded session. Changes
unrelated to provider behavior, such as documentation, do not require paid validation.

## Change gate

Inspect existing ownership before adding a function. Extend canonical contracts and
implementation, update affected adapters, and test real boundary behavior. Cover tool
continuations, malformed outputs, cancellation, budget exhaustion and provider errors.
Run lint, format and relevant tests. Extend the existing foundation CI with meaningful runtime tests as they are added.
Document remaining limitations honestly. Do not add abstractions without an immediate use.
