# First companion build

The first build implements the user-approved text and speech foundation without an agent framework. Aloy's own `ChatAgent` assembles the full selected conversation history, dispatches one provider response, uses the shared RunLifecycle to record the run, and emits the same streamed events consumed by `reply()` and the desktop bridge. It has no tools or continuation loop.

```python
from aloy import AgentConfig, ChatAgent, ConversationStore
from aloy.providers import GeminiProvider

store = ConversationStore()
agent = ChatAgent(
    config=AgentConfig(
        name="Aloy",
        system_prompt="Speak clearly and briefly.",
        provider="gemini",
        model="gemini-3.5-flash-lite",
    ),
    store=store,
    provider=GeminiProvider(store),
)
reply = await agent.reply(conversation_id, "Hello")
```

Text adapters are OpenRouter, direct Gemini Interactions API, Codex app-server and the offline fake. Gemini and Codex may retain provider session references, while SQLite remains canonical. Provider changes carry the same local text history. Context overflow asks for a new conversation rather than truncating history.

SQLite migrations own conversations, ordered messages, runs, audio metadata, provider sessions, settings and a cost ledger independent of deletable conversations. Audio lives as private files in macOS Application Support. The Swift/AppKit shell speaks JSON-lines v1 to the Python bridge; it contains no model logic. Default Standard mode is microphone to Silero speech detection to Qwen ASR 1.7B to `ChatAgent` to sentence-level Qwen TTS 1.7B. The optional paid speech engine is Gemini 3.8 Flash-Lite TTS. Stop cancels the active bridge task and clears local playback.

The explicit Live audio mode is a native `gemini-3.8-live` session. It bypasses the text agent's STT/TTS route but shares conversation identity, run status, recordings, transcripts, cancellation and the spend ledger. The current shell uploads each completed click-to-record utterance; continuous microphone streaming and barge-in during capture remain future work. Model Studio Qwen realtime comparison was optional and was not enabled without credentials.

Future German teaching features attach to existing seams: the lesson canvas consumes versioned response events; learner evidence and review plans belong behind new repository interfaces; a scheduler or keyboard/wake trigger starts a run through the canonical conversation service; tools, skills, MCP, image inputs and subagents extend validated agent configuration and the same execution pipeline. The first build intentionally does not declare those capabilities implemented.

The offline fake suite runs in CI. Selected live text checks dispatch one synthetic capped request through the production path; Codex is separately labelled as a provider-managed turn. TTS, Live and pronunciation have separately bounded checks. See [REPAIR_REVIEW.md](REPAIR_REVIEW.md) for current verification and limits and [ENGINEERING.md](ENGINEERING.md) for the change gate.
