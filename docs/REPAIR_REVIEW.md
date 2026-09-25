# First-build repair review

For the newer uncommitted Agent/Memory/Perception foundation, see
[Chunk 1 implementation audit](CHUNK1_REVIEW.md). It records current test evidence,
the upstream maintenance-check blocker and outstanding manual capture acceptance.
The first-build evidence below is historical for paths replaced by Chunk 1.

This review supersedes the earlier implementation claims where they differ. The
first build remains a manually activated companion, not the future hands-free tutor.

## Closed implementation gaps

| Requirement | Implementation and verification |
|---|---|
| Atomic SQLite changes | Numbered packaged SQL migrations; savepoint transactions; injected failures prove rollback of both schema and multi-row writes. |
| Durable recovery | Only the exclusive desktop backend performs crash recovery. Opening another store no longer interrupts active runs. A database constraint prevents concurrent turns in one conversation. |
| Audio consistency | Input/output audio links to runs and messages; playback outcomes persist. Failed/cancelled transcription retains its recording. Pending deletions survive interruption and retry; unreferenced recordings remain visible in storage accounting. |
| One run lifecycle | Text, native audio and pronunciation share `RunLifecycle` for start, outcome, reservation and settlement. Speech still surrounds the same text stream. |
| Cancellation | Operation IDs reject stale UI events; Stop clears playback before sending bookkeeping. Mode/conversation changes cancel active work. Local model work runs in cancellable child processes. |
| Recording controls | Record changes to Finish & Send. Cancel recording retains audio locally without provider dispatch. Right-click Replay to hear the last input recording. Replay normally replays the last response's segments. |
| Orb | Screen-coordinate gesture tracking and an explicit drag flag prevent release from opening the panel. The closed panel stays allocated. One small orb remains. |
| Spending | Atomic persistent reservations cover text, speech, native audio and pronunciation. Failed/interrupted dispatched work retains its allowance when billing is unknown. Deleting conversations cannot reset spending. Pricing includes the published January 2027 Flash/TTS step. |
| Provider checks | Direct model availability preflight; explicit zero retries; a measured generation-dispatch cap. Codex verifies its pinned CLI, feature restrictions, isolated configuration and disabled skills. Compaction is rejected rather than silently accepted. |
| Test correctness | pytest explicitly imports working source even after packaging. Network access is prohibited in ordinary tests. Native gesture/operation checks and full shell compilation run in macOS CI. |
| Diagnostics | Provider errors redact configured credentials. Backend stderr is drained; malformed transport messages produce errors rather than killing the bridge. |

The local spending ceiling is an estimate-based admission rule. It cannot control
billing from other applications, vendor price changes not yet configured, or a
remote provider continuing uncertain work after disconnection. Mixed native-audio
token totals use the highest applicable audio/text rate; absent usage retains the
reserved allowance. It is deliberately conservative, not a billing statement.

## Verification

- 34 offline Python checks pass against source. Native interaction checks pass;
  the full Swift shell compiles.
- Separate real text checks cover Gemini Lite, Gemini Flash, OpenRouter and the
  Codex subscription. Each direct check uses one generation dispatch, synthetic
  input, capped output and no retries. See local evidence for exact final timings.
- Local Qwen ASR transcribed all three synthetic German corpus phrases exactly.
  Warm combined local synthesis/transcription samples were 0.67 and 0.54 seconds;
  first use including model loading took 8.55 seconds.
- Three warmed Gemini Lite + Pocket runs emitted first audio at 1.67, 1.05 and
  1.11 seconds (median 1.11 seconds). This measures the backend audio event, not
  acoustic speaker onset; it excludes microphone/STT time. The corpus is tiny.
- Separate bounded Gemini TTS and Gemini Live checks passed. The final Live check
  reported 697 input and 244 output tokens, estimated at $0.005019, with linked
  recording metadata. Live remains an
  upload-after-recording exchange; ongoing streaming capture is next-round work.
- Human microphone test: capture, local transcription, fake response and audible
  local playback succeeded. The user found transcription reasonably fast and the
  voice odd, but acceptable temporarily. Voice naturalness is not accepted.
- Native replay followed by Stop recorded 4.9 ms inside the production playback
  stop handler. This excludes OS input-delivery latency and is one sample.
- Native drag/release stayed closed; deliberate click opened the panel. Saved
  conversation/audio and provider settings survived restart.

## Quality limits and next round

Pronunciation feedback remains experimental. Synthetic TTS is not a certified
correct-pronunciation reference. Nominal controls elicited corrections, and the
initial short response cap sometimes cut feedback off; its separately bounded
cap was increased. Identical-waveform repetitions and a Miete/Mitte contrast are
recorded locally. Human-labelled controls are still needed before accepting the
feedback as a teaching assessment. No numerical pronunciation score is exposed.

Qwen realtime remains optional and unconfigured without its credentials. Removed
Whisper and Qwen TTS comparison weights were not downloaded again. The one existing
runtime and selected Qwen ASR/Pocket assets remain shared outside the repository.

The owner accepts a press/release shortcut to start recording and the same shortcut
to finish/send as an initial interaction round. Wake-word activation, automatic
turn detection and interruption are required for the intended usable product.
They are not represented as delivered by this repair. Login launch, the orb visual
redesign, learner curriculum/memory, teaching visuals and proactivity remain separate
work. Future voice triggers must enter the existing conversation/operation pipeline.

Official protocol references: [Gemini Interactions](https://ai.google.dev/gemini-api/docs/interactions-overview),
[Codex app-server](https://learn.chatgpt.com/docs/app-server), and
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

## Footprint and retained evidence

No new comparison weights or duplicate runtime were retained. Selected models occupy
about 1.2 GiB and the shared runtime about 1.6 GiB; the current native app is about
320 KiB. Temporary comparison scripts and generated audition audio were removed.
The user microphone recording and reply remain saved. Small local test evidence is
ignored by Git. Peak memory and human-rated naturalness remain unverified targets.
