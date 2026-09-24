# Offline voice and orb revision

This review supersedes the original Pocket voice and 0.6B ASR selection. Paid speech
is a last resort and is never an automatic fallback. Wake words remain deferred.

## Production changes

- Option + Z starts recording, or sends the current recording, on key release.
- Option + X cancels capture/processing/playback through the existing Stop action.
  Cancellation does not send a query; existing local audio retention remains intact.
- The orb retains its 56-point window and uses a original violet/cyan cosmic particle swirl inside a brighter sky-blue sphere.
  Recording is red, speaking mint, processing rotates faster, and errors are amber.
  A cached layer rotates on the macOS compositor; Reduce Motion disables animation. Dragging does
  not open the panel. No browser, external visual library or texture assets are added.
- Local Silero VAD runs before ASR. No detected speech returns a dedicated no-speech
  event, preserves the input recording, and never dispatches an agent generation.
  Detector failures fail closed. This is speech gating after manual recording;
  it is not automatic end-of-turn detection or automatic barge-in.
- ASR and TTS retain the same adapters, subprocess cancellation, SQLite lifecycle
  and sentence queue as the rest of Aloy. The new TTS voices share one weight set.
- Selected downloads have pinned revisions. Comparison assets remain private;
  model download completion and full SHA-256 checks precede model selection.

## Verification

37 offline Python checks pass, including VAD rejection before ASR model loading and
no agent dispatch/history entry for silence. Native press/release checks pass and
Swift compilation passes. Local Silero rejects synthetic silence, low-level noise
and a tone; it also rejects the actual capture previously transcribed as Chinese,
while accepting both available actual speech captures.

### Local comparison results

All model files passed their published full SHA-256 checks. Speech workers run with
HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE enabled. No paid calls were made for this round.

On two existing spoken captures (one English, one German) and the known silent capture:

| ASR | English word edits | German word edits | First speech / warm speech |
|---|---:|---:|---|
| Qwen3-ASR-1.7B, with Aloy/Ayush context hint | 0/8 | 0/8 | 2.07 s / 0.76 s |
| Parakeet TDT 0.6B v3 | 1/8 | 1/8 | 1.69 s / 0.21 s |

Both were behind the same Silero gate and rejected the silent capture. Parakeet
misheard the name and replaced German möchte with musste. Qwen was retained for
this use case; these tiny, differently configured samples do not establish a global
ranking. Qwen ASR peak MLX allocation in a separate local run was 3.02 GiB.

Qwen TTS 1.7B CustomVoice auditions use one shared weight set:

| Voice | First English synthesis (includes load) | Warm German synthesis |
|---|---:|---:|
| Ryan | 9.83 s | 2.98 s |
| Aiden | 6.59 s | 2.71 s |

Both English clips transcribed back without word edits; Aiden's German clip also
matched, while Ryan's yielded two ASR spelling differences. This is an intelligibility
check, not a validated pronunciation or human naturalness judgment. Ryan remains the
initial selection; both are available in the voice picker. Owner listening feedback
is pending. First-use latency is materially higher than the old lightweight voice.

The complete installed offline STT -> fake agent -> Qwen TTS pipeline succeeded for
both actual spoken captures. The known silent capture emitted only no_speech in
0.21 seconds and no agent/audio response. Warm capture-to-generated-audio took 2.10
seconds; cold took 11.00 seconds. These use a fake text provider and therefore do not
include real LLM latency or acoustic speaker onset. They are not evidence that the
three-second conversational target is met with every provider.

Native UI inspection confirmed one 56-point orb, drag without opening, deliberate
panel opening/closing, the new voice picker and accessible state labels. The initial
compositor implementation incorrectly rotated AppKit's root layer,
causing clipping and disappearance. The repair keeps the root fixed and rotates a
centred, circularly clipped child containing a cached retina particle image. Native
regression checks exercise real layer transforms at five-degree intervals through
360 degrees, plus state changes. Compilation and 37 offline Python tests pass.
The owner has been asked to physically test the new Option + Z / X combinations;
confirmation is pending.

## September 25 voice follow-up

The owner found both local Qwen voices too stiff for sustained conversation. A
bounded comparison generated German with the MLX Chatterbox Multilingual V3 model,
conditioned on one existing synthetic male voice sample. Its model and required
S3TokenizerV2 occupy about 3.2 GB together. A 3.48-second warm sample took 3.52
seconds to synthesize; the first generation took 5.24 seconds after a 2.09-second
model load. Qwen ASR recovered the intended words from the sample. That checks basic
intelligibility, not naturalness or suitability for long German lessons. Because it
ran only about as fast as its audio and carried a 3.2 GB footprint, the temporary
comparison weights were removed. The short private audition remains available.

The existing Gemini Interactions TTS adapter generated two comparable 10-second
samples using `gemini-3.8-flash-lite-tts`. Full synthesis took 7.18 and 7.97 seconds.
The owner liked both and preferred the warm male Achird voice, which is now the voice
used by the explicit Gemini speech option. The app's selected speech engine did not
silently switch from offline to paid. Google documents `stream=True` for incremental
raw PCM audio; the current Aloy adapter still waits for a complete WAV per sentence,
so its measured synthesis time is not a first-audio latency claim. Streaming playback
would need a compatible native audio queue and cancellation/accounting tests.

At Google's standard paid rate through December 2026, Gemini Flash-Lite TTS output
costs $0.54 per hour of generated speech, plus a small text-input charge. The output
rate doubles in January 2027. OpenRouter lists the same Gemini output rate. A two-hour
daily speaking workload therefore costs about $32.40 for 30 days at the current rate,
before text-model usage. Aloy's existing $30 monthly cap must remain enforced; the
paid voice cannot cover that workload continuously under the cap. Neither an idle orb
nor local STT adds TTS charges. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing),
[Gemini streaming TTS](https://ai.google.dev/gemini-api/docs/speech-generation),
[OpenRouter listing](https://openrouter.ai/google/gemini-3.8-flash-lite-tts/).

The native orb now keeps one green speaking appearance across gaps between audio
chunks, turns red only for recording and stays blue while awaiting the first audio.
Option + Z and Option + X remain the same one-hand shortcuts. Native state tests,
38 offline Python checks and lint pass. The rebuilt app and Python backend launched.

## Storage and reproducibility

Selected models occupy about 5.2 GiB; the native bundle is about 372 KiB. Old 0.6B ASR,
Pocket and Parakeet comparison weights, incomplete downloads and temporary downloader
scripts were removed. User recordings remain intact. Small private evidence and voice
auditions are ignored by Git.

The comparison harness uses production adapters: scripts/check_local_speech.py.
Inputs and output evidence belong outside Git. Model registry entries allow explicit
future comparisons, but default downloads/pruning retain only the selected three.
