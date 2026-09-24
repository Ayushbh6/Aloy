# Aloy agent instructions

Keep responses concise. Read README.md, docs/ENGINEERING.md and docs/INITIAL_PLAN.md
before work. If present, also read local MEMORY.md, .local/CONTEXT.md and
.local/HANDOVER.md. These ignored files contain private owner context; do not publish them.

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
- Current scope is environment setup and planning. Do not infer approval to implement
  the whole application, download models, deploy services or enable background jobs.

## Data and workflow

Public repository: never commit credentials, recordings, learner profiles, screen
captures, personal memory, account data, local machine diagnostics or databases.
Keep source changes narrow and preserve unrelated work. Do not reset, stash or discard.
Research/read-only inspection is allowed. Sending messages or other external actions
requires explicit authorization. No application framework implies permission to act.
