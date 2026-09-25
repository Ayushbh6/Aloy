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

The historical offline STT -> fake agent -> Qwen TTS pipeline succeeded for
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
intelligibility, not naturalness or suitability for long German lessons. The owner
liked this short sample, so the pinned model and S3TokenizerV2 were restored for
longer trials through Aloy's speech adapter. Two German lesson snippets generated
7.00 and 7.44 seconds of audio in 13.21 and 10.49 seconds, respectively, on this
Mac. Qwen ASR recovered both scripts without word edits. The fake-provider-to-
Chatterbox path also saved an output asset in an isolated conversation. Chatterbox
does not stream audio in the current MLX implementation, so the first audible
sound waits for the whole sentence. Chatterbox is now the selected local voice
for the 120-hour monthly planning case. Its latency remains a limitation for
natural back-and-forth; owner feedback on longer auditions may change the choice.

The existing Gemini Interactions TTS adapter generated two comparable 10-second
samples using `gemini-3.8-flash-lite-tts`. Full synthesis took 7.18 and 7.97 seconds.
The owner liked both and preferred the warm male Achird voice for that paid
comparison. It was later removed from Standard mode when Chatterbox became the
owner's sole installed offline voice, then restored as an explicit paid choice.
Google documents `stream=True` for incremental
raw PCM audio; the current Aloy adapter still waits for a complete WAV per sentence,
so its measured synthesis time is not a first-audio latency claim. Streaming playback
would need a compatible native audio queue and cancellation/accounting tests.

At Google's standard paid rate through December 2026, Gemini Flash-Lite TTS output
costs $0.54 per hour of generated speech, plus a small text-input charge. The output
rate doubles in January 2027. OpenRouter lists the same Gemini output rate. A two-hour
daily speaking workload therefore costs about $32.40 for 30 days at the current rate,
before text-model usage. Aloy's existing $30 monthly cap must remain enforced; the
paid voice cannot cover that workload continuously under the cap. At the safer
planning case of 120 hours of generated speech per month, output alone would cost
about $64.80 through December 2026 and $129.60 from January 2027. Neither an idle
orb nor local STT adds TTS charges. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing),
[Gemini streaming TTS](https://ai.google.dev/gemini-api/docs/speech-generation),
[OpenRouter listing](https://openrouter.ai/google/gemini-3.8-flash-lite-tts/).

For 120 monthly hours of generated speech, local Chatterbox has no speech API
charge. At an illustrative 600–750 spoken characters per minute, ElevenLabs
Conversational v3 or Flash/Turbo at $0.05 per 1,000 characters would cost about
$216–270 for speech alone. OpenRouter's Gemini route lists the same $6 per million
audio-output-token rate as Google's standard route, so switching the endpoint
does not lower that price. Its Grok Voice TTS listing is $15 per million
characters, or roughly $65–81 at the same speaking density; German voice quality
has not been auditioned here. [ElevenLabs API pricing](https://elevenlabs.io/pricing/api),
[OpenRouter Grok pricing](https://openrouter.ai/x-ai/grok-voice-tts-1.0).

The native orb now keeps one green speaking appearance across gaps between audio
chunks, turns red only for recording and stays blue while awaiting the first audio.
Option + Z and Option + X remain the same one-hand shortcuts. Native state tests,
41 offline Python checks and lint passed at that stage. The rebuilt app and Python backend launched.

## Storage and reproducibility

Selected ASR, VAD, Chatterbox and tokenizer files occupy about 5.3 GiB. Qwen TTS,
old 0.6B ASR, Pocket and Parakeet comparison weights, incomplete downloads and
temporary downloader scripts were removed. User recordings remain intact. Small
private evidence and retained microphone captures are ignored by Git. Synthetic
audition files and orb screenshots were removed after the voice choice.

The comparison harness uses production adapters: scripts/check_local_speech.py.
Inputs and output evidence belong outside Git. Model registry entries allow explicit
future comparisons, but default downloads/pruning retain only the selected four
model components (one TTS voice, its tokenizer, ASR and VAD).

## Single-voice and latency follow-up

The owner confirmed Chatterbox as the best offline voice audition and requested
that other offline TTS engines be removed. Pocket and Qwen TTS adapters and model registry
entries were removed. Pocket TTS, PyTorch and their unused dependencies were
uninstalled from Aloy's own runtime, reducing that environment from about 1.6
GiB to 647 MiB. ASR, Silero VAD, Chatterbox and its tokenizer remain; user audio
and conversations were not pruned. Gemini Live remains a separate explicit mode.

The Standard-mode picker now also exposes paid speech explicitly: direct Gemini
3.8 Flash-Lite/Flash TTS, the same models through OpenRouter, and OpenRouter Grok
Voice TTS. Achird is the selected Gemini voice; Grok's Leo voice has not been
reviewed for German teaching. A short synthetic, one-dispatch live check passed
for each route on 25 September 2026. These checks confirm audio generation and
persistence, not user acceptance of voice quality or sustained latency. Paid
routes reserve against Aloy's $30 monthly estimate before dispatch and never
replace local Chatterbox automatically. OpenRouter Gemini returns PCM, which
Aloy wraps as WAV; Grok returns MP3 and stays compressed in storage.

Chatterbox and ASR prewarm while the microphone is open. In one adapter check,
Chatterbox loading and reference conditioning took 2.53 seconds; a following
short German reply took 3.65 seconds to generate 2.8 seconds of speech. With
both speech workers prewarmed, a separate capture-to-fake-reply check took 5.61
seconds from recording end to a playable file. The direct constituent timings
were 0.45 seconds for FFmpeg conversion, 0.99 seconds for warm ASR and 2.21
seconds for a short Chatterbox reply. These vary with model startup and Mac
memory pressure and exclude a real text-provider call. A local
CFG-weight comparison (0.3 versus 0) showed no consistent speed gain, so the
accepted voice settings were retained. This does not meet the original median
three-second response-to-first-audio target; no claim of streaming is made.

Silero now monitors PCM from the open microphone and reports a speech start
after two positive 32 ms frames. A pause state requires about 1.2 seconds of
quiet, but never sends automatically; Option + Z remains the only send action.
The final VAD gate still rejects silence before ASR, and only the outside of
the detected speech span is trimmed, with a 250 ms extra margin. All pauses
and fillers between the first and last detected speech remain in the audio
passed to ASR. Existing real captures produced no speech for the silence
control and speech/pause events for English and German samples. A repeated
real capture separated by two seconds of silence yielded speech, pause, speech,
pause, without ending capture. A later one-pass VAD/ASR change kept the known
silence control empty and recovered both English and German captures without
word edits; the warm German transcription took 0.71 seconds in that check.
Two isolated prewarmed fake-provider turns on the final one-pass decoder reached
first playable audio 3.54 and 3.26 seconds after submission, excluding a real
provider and speaker onset.
The 46 offline Python checks, native interaction tests, build, and dependency
checks pass. Physical microphone activity monitoring in the rebuilt app still
needs owner validation.

## Current Standard speech picker — 25 September 2026

This section supersedes the six-choice paid picker described above. The owner
requested only Chatterbox local, direct Gemini 3.8 Flash-Lite TTS, and OpenRouter
`x-ai/grok-voice-tts-1.0` for Standard speech output. The panel has a separate
voice dropdown and persists a selection for each engine. The initial voices are
the existing warm male Chatterbox WAV, Gemini Achird, and Grok Sal. Gemini's 30
prebuilt IDs and the five Grok IDs offered through OpenRouter are selectable.
Chatterbox has one installed reference; adding consented local WAV references
offers more choices without another model download. Grok Sal still needs the
owner's voice and German-language audition. Gemini Live stays a distinct mode.

At commit `95dd7cf`, 54 offline Python tests, Ruff, Swift compilation and native
menu inspection passed. A local Chatterbox generation returned 1.36 seconds of
WAV audio. Separate bounded one-dispatch real checks passed for direct Gemini
Achird (4.3 seconds, $0.000242 estimated) and OpenRouter Grok Sal (0.89 seconds,
$0.000240 estimated). These tiny synthetic checks establish request and playback-
asset routing, not long-form quality, cost at 120 hours, or end-to-end latency.
No additional model weights were downloaded; the existing dedicated model cache
remained about 5.3 GiB. Earlier local comparisons are historical, not the
current voice lineup.
