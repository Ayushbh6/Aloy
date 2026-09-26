# Continuous voice sessions

Implemented September 25, 2026. This supersedes the manual Standard-mode controls
in earlier voice reviews. Explicit Gemini Live remains manual record/send.

## What changes

Option + Z opens a session, plays a local activation cue, then a time/day greeting.
The microphone stays open until Option + Z closes it with a goodbye, or Option + X
cancels immediately. Greetings/goodbyes are cached locally per text, engine and voice.
Silero detects speech locally; about 128 ms of confirmed speech interrupts playback
and cancels the previous task. An utterance is submitted after 960 ms of detected
silence. A 400 ms prefix preserves onset; internal hesitations remain intact.
Unfinished speech is saved locally on close/cancel, not submitted. Ambient silence
has bounded memory; utterances are capped at 120 seconds.

The native AVAudioEngine shares microphone and playback for macOS voice processing
(echo cancellation, noise processing and AGC). Input is converted once to mono 16 kHz
PCM before local VAD and Qwen ASR. Device configuration failure closes the session
with an error. This does not identify the speaker or reliably reject YouTube speech.

Gemini TTS keeps one synthesis request for the whole reply, preserving prosody, but
streams its 24 kHz PCM as it arrives. Native playback starts with a 240 ms audio
cushion and records underruns. Chatterbox and Grok retain whole-reply synthesis and
use the same duplex output engine during sessions. No sentence-by-sentence TTS,
voice fallback, new model download or second orchestration framework was introduced.

The existing AgentRunner now starts speech as soon as the answer completes, while
post-answer memory extraction continues. Extraction no longer delays speech dispatch.
One custom pipeline, SQLite plus LanceDB, existing provider choices and German tuition
remain intact. The broader Socrates/full-access harness is a separate implementation.

## Evidence

All private recording tests stayed local. Four existing completed-turn inputs were
replayed through real local VAD and ASR: exactly one utterance each. Two transcripts
were identical; two differed in punctuation or a hesitation filler. The same four
inputs then passed the production bridge -> real local ASR -> fake AgentRunner ->
synthetic streamed speech path, with one input/output pair per turn. No private
recording or transcript was submitted to a provider.

A bounded synthetic comparison used identical 33-word German input, Gemini
`gemini-3.8-flash-lite-tts`, Achird voice, style and output cap. Each route had a new
client and preflight; this was one sequential pair, not a repeated warm/cold benchmark.

| Measurement | Whole file | Streaming |
| --- | ---: | ---: |
| Speech function entry to first delivered audio | 6.287 s | 0.940 s |
| Speech function entry to saved audio | 6.287 s | 4.820 s |
| Native render-clock onset replaying captured packet pacing | Not measured | 0.998 s |

Streaming delivered audio 5.35 seconds earlier in that pair. Native replay of the
actual PCM and arrival schedule finished without buffer underruns. Playback was
silenced for unattended testing; this measures rendering, not acoustic onset or
human-perceived voice quality.

A separate synthetic Gemini-quality main-answer -> Gemini TTS check delivered first
audio at 4.433 s and saved the whole reply at 7.463 s. Replaying those packets through
native output started rendering at 4.528 s without underruns. This excludes microphone,
endpointing, ASR and personal retrieval; it used an empty synthetic conversation and
fake maintenance. It is not comparable to historical personal turns of different length.

The initial integrated check exhausted an overly small 128-token test cap (including
reasoning), correctly produced no speech, and was recorded as a failure. One explicit
follow-up with a 1,024-token cap passed. Total: five generation dispatches across the
comparison, failed check and corrected check; SDK retries and fallbacks disabled.
Successful checks estimated $0.004625 together; the failed request also retains its
separate conservative ledger charge. These are estimates, not billing records.

Historical evidence across four personal turns: context 65–73 ms, main-provider
stage 3.32–4.93 s, post-answer extraction 0.86–1.71 s, and speech reservation to saved
audio 5.58–8.46 s. The last interval was a proxy, not isolated synthesis time. There
were no original microphone-stop or playback-onset timestamps. Recording duration
was never used to infer those timestamps. Removing extraction from the speech
critical path is verified by a test that holds extraction open until speech arrives.

Local replay ASR on segmented inputs measured 0.46–0.60 s after prewarming. Cold ASR
prewarming took 1.91 s in that replay; first VAD load took 0.96 s and lazy compilation
added work on first feed. Session start now warms one silent VAD frame before readiness;
ASR prewarms while the greeting is produced. These figures are local replay observations,
not full end-to-end benchmarks. Native cancellation tests exercise immediate stop and
stale-buffer rejection, not end-to-end microphone-to-interruption latency.

## Instrumentation and limits

Migration 015 adds metadata-only voice timings: session readiness, speech detection,
endpoint sample position, saved input, ASR start/end, context start/end, provider
preflight/dispatch/first token/completion, speech queue/preflight/dispatch/first audio,
speech completion, saved audio, native mic open/close, render onset, drain and underruns.
Records carry operation/run IDs where available. Pre-run ASR and endpoint events belong
to the session operation and are ordered before their run. Native render clocks are
proxies for acoustic output; do not subtract different clock sources across sleep.

Still unverified without the owner: physical microphone permission/capture on the
rebuilt app, echo rejection in this room, competing-video rejection, headset/device
changes, German pronunciation and natural conversational pause preference. VAD can
mistake other speech for the owner and can submit during a learner pause longer than
its threshold. No speaker verification, semantic turn detector or wake word is claimed.
A stalled network can still exhaust the buffer; the new underrun trace exposes that.

Final checks: 102 Python tests, Ruff lint/format, dependency check, native interaction
and Playground suites, full app build and graceful restart passed. The installed
backend owns the desktop lock and includes the new timing migration.

## Repeatable checks

- Offline: `python -m pytest -q`, Ruff, native interaction/Playground suites.
- Synthetic speech paths: `PYTHONPATH=src .venv/bin/python -m aloy.voice_smoke --mode fake`.
- Explicit paid comparison: add `--mode live --scenario compare --output <ignored-dir>`
  (two TTS requests, 65-second per-route timeout, $0.25 outer reservation).
- Explicit paid integrated check: `--mode live --scenario pipeline` (one main request,
  one TTS request, no tools, no personal retrieval, fake maintenance, same reservation).
- Native stream checks: compile `mac/VoiceAudio.swift mac/tests/voice/main.swift` with
  `swiftc -swift-version 5 -parse-as-library -framework AVFoundation` and run locally.
  Optional arguments `<stream.pcm> <trace.json>` replay provider timing without microphone
  access. Audio-device tests are deliberately separate from device-independent CI.

Private measurements, synthetic PCM, replay scripts and preservation hashes stay in
`.local/speech-session-evidence/`. All 10 pre-existing audio files retain their hashes.

## Research basis

[LiveKit agents](https://github.com/livekit/agents/tree/df6455d7c74a59003d913d9a20b204319d28e093/livekit-agents/livekit/agents/voice)
provided reference patterns for overlapping stages, interruption and audio buffering.
Aloy reuses those ideas in its own runner; it does not add LiveKit orchestration.
[Gemini speech documentation](https://ai.google.dev/gemini-api/docs/speech-generation)
specifies streaming Interactions PCM versus unary WAV output.
[Apple voice processing](https://developer.apple.com/documentation/avfaudio/avaudioionode/setvoiceprocessingenabled(_:))
provides the native duplex processing API. Open-source VAD is not equivalent to
LiveKit's hosted background-voice cancellation or to reliable speaker identification.
