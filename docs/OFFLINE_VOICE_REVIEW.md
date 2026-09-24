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

## Storage and reproducibility

Selected models occupy about 5.2 GiB; the native bundle is about 372 KiB. Old 0.6B ASR,
Pocket and Parakeet comparison weights, incomplete downloads and temporary downloader
scripts were removed. User recordings remain intact. Small private evidence and voice
auditions are ignored by Git.

The comparison harness uses production adapters: scripts/check_local_speech.py.
Inputs and output evidence belong outside Git. Model registry entries allow explicit
future comparisons, but default downloads/pruning retain only the selected three.
