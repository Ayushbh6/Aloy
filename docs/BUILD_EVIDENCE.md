# First-build evidence — 2026-09-24

This is a bounded synthetic check on a local Apple Silicon Mac. It does not measure human speech accuracy or establish exam-teaching quality. No private learner recording was used.

## Runtime and providers

- Offline fake suite: ordered history, provider switch, prompt isolation, failure, cancellation, Live bypass of STT, storage cleanup, durable spend, cache pruning and JSON transport passed.
- OpenRouter `deepseek/deepseek-v4.1-flash`: one capped direct smoke passed in about 1.95 s. Later checks, including the final packaged-runtime check, hit the provider's shared upstream 429 limit; fallback and retries stayed disabled. Its current availability is unverified. Gemini Lite is the default for reliability.
- Gemini `gemini-3.5-flash-lite` and `gemini-3.8-flash`: separate one-request Interactions API checks passed in about 3.86 s and 3.32 s. Two-turn stateful continuation and entry from a different provider's local transcript also passed.
- Codex `gpt-6-sol`: app-server one-turn subscription check passed in about 3.37 s, with a separate two-turn continuity check passed. Its hidden internal model-call count is not observable.
- After moving the single Python environment, pinned Codex CLI and local credentials into macOS Application Support, final one-turn checks passed through the packaged runtime for Gemini Lite (2.88 s) and Codex (3.93 s). The final OpenRouter check failed with the same upstream 429; it was not retried.
- The real Standard pipeline (OpenRouter text plus Gemini Flash-Lite TTS) produced saved text and speech in about 6.23 s total. The Gemini Lite plus Pocket pipeline reached its first local audio event in 4.73 s and finished three speech segments in 5.30 s. The three-second first-audio target was missed.
- Native `gemini-3.8-live` processed a synthetic recorded German sentence through the actual bridge in 9.93 s, saving input/output transcripts and output WAV. It opens a bounded Live session after recording ends.
- Gemini 3.8 Flash-Lite TTS produced 3.28 s of German audio in 4.69 s. A synthetic, correctly spoken German phrase sent to audio-aware Gemini pronunciation feedback received “No clear pronunciation issue was audible.” This is one control, not a validated pronunciation evaluation.

## Offline speech and footprint

| Candidate | Result on synthetic audio | Decision |
|---|---|---|
| Qwen3 ASR 0.6B 8-bit | Exact text on two German phrases and one English phrase; first load 3.62 s, subsequent phrases 0.49/0.24 s | Keep as default |
| Whisper large-v3-turbo MLX | Same exact text; first load 3.20 s, subsequent phrases 1.99/1.96 s | Remove weights |
| Pocket TTS German `juergen` | 3.28 s German sample generated in 1.69 s at first use within the comparison process | Keep as local default |
| Qwen3 TTS CustomVoice 0.6B 8-bit | 5.28 s German sample generated in 8.63 s at first use within the comparison process | Remove weights |

These are tiny synthetic samples, and the TTS timings were measured sequentially in one process, so they are directional only. Human voice quality and pronunciation with the owner's microphone still need a listening check.

The selective comparison cache reached about 4.5 GiB. Pruning unselected Aloy weights reclaimed 3.34 GiB; the selected Qwen ASR and Pocket assets now occupy about 1.2 GiB. The one Python environment is about 1.3 GiB, the pinned local Codex CLI about 325 MiB, and the built Mac app about 260 KiB. The environment and CLI now reside under Application Support, with ignored links in the repo rather than duplicate copies. These are approximate allocated sizes. Do not confuse the deleted comparison weights with user recordings, which are retained until explicitly deleted.

The explicit provider checks and short audio experiments remain far below the $3 comparison ceiling by local estimates; this is not a provider invoice. The cost ledger survives conversation deletion, warns at $20, and refuses estimated requests beyond $30. Gemini Flash-Lite TTS pricing doubles on January 1, 2027; the estimator applies that dated step.

## Remaining limits

The native desktop shell compiled and launched normally from Finder/`open` with one small orb. Clicking it opened the panel in Ready state; a fake-provider, text-only conversation completed through the visible UI, and both its history and provider/speech selections survived a restart. A second fake-provider turn generated a 1.28-second local Pocket WAV; the AppKit panel moved through Speaking back to Ready. This verifies generation and playback lifecycle, but a human still needs to judge what was audible. The first direct launch attempt stalled while Python loaded from Documents; packaging the runtime under Application Support resolved it. The first microphone click reached the macOS permission path, which timed out UI automation and created only a disposable 28-byte placeholder; that file was removed. The shell now checks authorization and the recorder's Boolean success before reporting Recording. Microphone permission, audible speaker quality and interruption latency still need a human Mac smoke check. The owner deferred the orb's requested murmuration redesign to the next UI round. Qwen realtime was optional and not enabled without Model Studio credentials. Pronunciation feedback has no human-labelled controls yet. German curriculum, progress memory, coordinated teaching visuals and proactivity are deliberately outside this build.
