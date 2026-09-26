# Full-access harness review

The approved harness is implemented inside the existing `AgentRunner`. Aloy remains
a voice-first German companion, with general file and terminal capabilities. There
is no second agent framework, project-root fence, container or separate speech stack.
SQLite owns history, tasks, goals, checkpoints, tool evidence and learning evidence;
LanceDB remains a rebuildable retrieval index.

## Tools and use

Ten core tools join the existing web, memory, media and canvas tools:

| Tool | Behaviour |
| --- | --- |
| `read` | Line-numbered text, SHA-256 and pagination; native image/video bytes for a compatible active model. Audio returns an asset for `media.inspect`. |
| `glob`, `grep` | Full host paths, hidden and ignored files included, stable path ordering and continuation cursors. Git internals excluded from discovery; direct reads remain possible. |
| `edit` | Create/write or unique exact replacement; whole-file overwrite requires the read hash. |
| `apply_patch` | Create, update or delete multiple files with exact contexts, stale-file checks, private backups and change records. |
| `terminal` | Real host shell, cwd, environment, optional PTY, output log, timeout and session ID. |
| `terminal_control` | List, poll, read output, write stdin, interrupt or stop an owned process. |
| `context_retrieve` | Browse tasks/goals, exact or hybrid history search, dates, pagination and full evidence inspection. |
| `capability_search`, `capability_control` | Discover and activate skills, goal/task/learning operations and explicitly configured stdio MCP tools. |

In Settings, **Files and terminal: Full Mac access** is enabled by the approved
migration. It can be disabled independently of the existing screen/action policy.
Normal macOS permissions still apply. Tool output and document instructions cannot
grant authority; canvas selections do not authorize host tools. Full host access
means commands themselves are powerful, including network access; it is not a claim
that arbitrary shell commands can be sandboxed by the separate desktop-action policy.

Per-call limits keep results usable: 20 MB read, 14k text characters, 1,000 lines,
60-second native video clips, 12k terminal output bytes per poll, 64 MB terminal log,
15-second searches and a default 15-minute terminal deadline. Larger files/clips can
be explicitly processed in sections through the terminal. Standard runs allow 32
model cycles / 10 minutes and an estimated $0.50 run allowance inside the existing
monthly ledger. A Codex managed turn permits at most 31 dynamic callbacks; its hidden
model calls cannot be counted by Aloy. These are bounded executions, not an unattended
infinite background scheduler.

## Compression and recovery

Recent messages stay verbatim while they fit the working budget. Older history is
compressed into a validated checkpoint with constraints, decisions, exact outstanding
requests and next steps. Original messages are never deleted by compression.
Oversized messages are explicitly excerpted with original-message references.
Tool details are saved before shortening their model-facing projection. Accumulated
remote continuations are periodically rebuilt from that local projection.

The checkpoint record is created before the model call and committed atomically with
the summary. A failed compactor retains previous obligations and exact recent user
excerpts, with an explicit fallback marker. If its projection must shrink, the prior
checkpoint remains reachable. This preserves recoverability, not a guarantee that
any summary perfectly retains every nuance. `context_retrieve` can inspect `m:`,
`s:`, `e:`, `c:`, `t:`, `g:` and `l:` references for original messages, run steps,
tool evidence, checkpoints, tasks, goals and learning evidence, respectively.

Tasks save objectives, constraints, unresolved requests, progress and next steps;
goals can link multiple tasks. Reopening a conversation supplies saved task state
and the previous run's action references. Startup marks abandoned runs, processes
and in-progress checkpoints interrupted. It never automatically repeats uncertain
side effects. Terminal logs survive restart; a live shell's memory/stdin session does
not. File-tool backups and before/after hashes support recovery; a multi-file patch
can be partially applied on a filesystem failure, so inspect its journal before
continuing. Terminal mutations are logged as commands/output, not automatically
backed up as file-tool edits are.

## German teaching foundation

`goal.update`, `task.update`, `learning.record` and `learning.search` are discoverable
capabilities. Learning records separate observed answers, self-report, inference and
pronunciation evidence from general chat summaries. An observed answer must quote
an actual user message. Pronunciation requires the corresponding input recording
and a successful `media.inspect` audio observation for the same bytes, with the
inspection reference saved. Transcript text alone cannot satisfy this requirement.
Practice is not recorded as mastery. Curriculum, validated proficiency assessment,
review scheduling and proactive teaching remain the next product slice.

Optional local configuration lives at `Application Support/Aloy/harness.json`:

```json
{
  "skills": [{"name": "my-workflow", "description": "Local workflow", "path": "/absolute/SKILL.md"}],
  "mcp": [{"name": "my-server", "description": "Local tools", "command": "/absolute/server", "args": []}]
}
```

No server is installed or enabled implicitly. Discovery loads metadata first; an
explicit activation loads instructions or the selected tool schema. MCP supports
stdio JSON-RPC and paginated tool catalogs. Cancellation stops its connection;
reactivation reconnects without replaying the previous call. This initial MCP client
does not implement remote HTTP transport, resources, sampling or server-initiated
operations. Skills and MCP responses remain subordinate to the current request.

## Verification

Offline tests exercise real disposable files, hash conflicts, ignored-file search,
pagination, PTY stdin, process cancellation including shell-exited descendants,
MCP catalog pagination/cancellation, goals, checkpoint fallback/reopening, exact
retrieval, learning provenance and remote-context rebuilding. They run through
production implementations, with no network or private recordings.

Final local checks: 115 Python tests, Ruff lint/format, dependency integrity, native
interaction/Playground tests and the full native build passed. Graceful restart
passed. The installed wheel independently passed the offline PDF flow; its backend
holds the desktop lock, has migrations 016/017 and enables all ten core tools.
All ten pre-existing audio files in the preservation manifest are hash-identical.

Bounded synthetic live checks passed:

- Gemini: terminal PDF text extraction and rendering, `read` of text and both page
  images, correct page-specific red square / blue triangle answers (5 dispatches).
- Codex: the same full PDF workflow with images returned by Aloy's dynamic `read`
  callback. Its duplicate built-in image viewer is disabled. One managed turn,
  7 dynamic calls; hidden model requests are not reported as a known count.
- Gemini video: native `read` of a synthetic clip and correct red-to-blue order
  (3 dispatches). Native video is not advertised on the Codex route.
- Checkpoint maintenance: one OpenRouter structured-generation call preserved the
  exact outstanding grammar request and one-question-at-a-time constraint.

The Codex checks exposed two too-small test call budgets and then its accidentally
enabled duplicate image viewer; the corrected adapter passed. A first video test
incorrectly required exactly one read and was corrected to require successful native
delivery. These failed attempts remain in the local evidence and spend ledger.
OpenRouter native image/video transport is supported only when its selected model's
catalog advertises that modality; it has protocol coverage, not a new live media
claim from these Gemini/Codex checks. Cost figures are ledger estimates; Codex's
subscription route is not an API billing estimate.

```sh
PYTHONPATH=src .venv/bin/python -m aloy.harness_smoke --mode fake --scenario pdf
PYTHONPATH=src .venv/bin/python -m aloy.harness_smoke --mode fake --scenario video
# Explicit paid checks; each invocation is capped and isolated from personal history:
PYTHONPATH=src .venv/bin/python -m aloy.harness_smoke --mode live --scenario pdf --provider gemini
PYTHONPATH=src .venv/bin/python -m aloy.harness_smoke --mode live --scenario checkpoint
```

PDF smoke requires Poppler; video smoke requires FFmpeg. No claim is made that
native build tests verify physical microphone acoustics or the user's next real
teaching session. Existing speech evidence remains in `VOICE_SESSION_REVIEW.md`.

## Reference designs

- [OpenCode compaction](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/compaction.ts): protect recent context, prune old tool details before summarizing.
- [DeepSeek compaction](https://github.com/deepseek-ai/deepseek-harness/blob/main/docs/subsystems/compaction.md): durable lifecycle markers and original evidence retained behind a smaller working projection.
- [Codex](https://github.com/openai/codex): real process sessions, bounded output and native dynamic-tool image results.
- The owner's copied Socrates V2 design: the ten-tool surface, goal/task distinction,
  explicit retrieval and capabilities loaded on demand. Its former path restrictions
  are superseded by the owner's full-host-access decision.

Reference clones and private Socrates snapshots remain in the ignored local reference
folder. They are design references, not additional runtime dependencies or merged
agent loops.
