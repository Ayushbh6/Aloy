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

## Proposed canonical path

Input validation -> context assembly -> provider invocation -> normalized events ->
validated tool dispatch -> continuation decision -> validated result -> persistence.

All text, image, voice transcript, scheduled wake and eventual child-agent entry points
call the same runtime. Speech and UI subscribe to typed events. They never call an LLM
independently for tutoring. Provider-native audio sessions may need a transport adapter;
its lifecycle, state, authority and accounting still belong to the canonical runtime.

One response can carry speakable text and typed visual instructions. Playback and
visuals share sequence identifiers and cancellation. No arbitrary model-generated
JavaScript execution in the teaching UI.

## Proposed public contracts (not implemented)

AgentSpec: name, system_prompt, model, tools, mcp_servers, skills, output_format,
max_turns, policy and budget. AgentInput: text, images, conversation reference and
optional audio evidence. AgentResult: output, structured output, usage, stop reason,
run reference. Separate reusable agent definition from per-run inputs.

Model capabilities explicitly cover images, audio, structured output, tools and
streaming. Unsupported requests fail clearly. No silent image loss or model substitution.
Define max_turns as an Aloy model/continuation cycle limit. Provider-managed backends
must declare any stronger or weaker enforcement semantics; never claim the limit bounds
hidden remote reasoning or tool steps.

## Cross-cutting invariants

- Explicit run lifecycle, durable outcomes and structured errors.
- Cancellation and deadlines propagate through provider, tool, speech and child work.
- Retry transient safe operations within limits. Never replay a side effect blindly.
- Validate every tool input and output and enforce permission before dispatch.
- Memory records distinguish observed evidence, learner self-report and model inference.
- Local raw evidence can support bounded retrieval; never resend unlimited full history.
- Usage and cost recorded centrally, with provider-specific price metadata and spend caps.
- External text, screenshots, skills and tool results are data, not authority escalation.
- Logs omit credentials and private content by default; raw audio retention is opt-in.

## Change gate

Inspect existing ownership before adding a function. Extend canonical contracts and
implementation, update affected adapters, and test real boundary behavior. Cover tool
continuations, malformed outputs, cancellation, budget exhaustion and provider errors.
Run lint, format and relevant tests. Add CI once meaningful runtime tests exist.
Document remaining limitations honestly. Do not add abstractions without an immediate use.
