# Voice activation

Option + Z records/sends and Option + X cancels. Both are global Carbon hotkeys,
activated on release. Option + Space remains available to Codex. Both
shortcut and button call the existing toggleRecording function: start/finish capture,
existing recorded transport, local STT, canonical agent stream and selected TTS.
No new provider pipeline or speech dependencies are added. Starting while speaking
uses the existing cancellation and operation-ID machinery. The orb indicates capture
in red and panel visibility is unaffected. Registration errors are explicit.

Native tests exercise held-key repeat and unmatched releases. Python regression
checks cover the reused backend. The owner verified the earlier Option + Space flow. The replacement Option + Z / X
combinations require their own physical check; native tests cover independent key
state and held-key repetition. See OFFLINE_VOICE_REVIEW.md for current speech checks.
Both Python and native GitHub CI jobs passed; a bounded Gemini smoke check passed
with exactly one generation request.

## Wake-word feasibility

A custom Hey Aloy detector is possible while the app runs and microphone access is
explicitly armed. It cannot guarantee activation from system sleep or when Aloy is
quit. Responsiveness and false triggers must be measured on this Mac; no superiority
over Siri has been established.

- [Porcupine](https://picovoice.ai/docs/porcupine/) explicitly supports macOS arm64,
  local wake-word inference and custom phrases generated through its console. It
  requires a valid AccessKey and applicable account entitlement. No account or key
  was created as part of this feature.
- [openWakeWord](https://github.com/dscripka/openWakeWord) supports custom training
  and ONNX inference. Its documentation focuses on Linux/Windows; macOS arm64 and
  our Python 3.13 environment need an installation/inference check before selection.
  Code is Apache-2.0; bundled pretrained models are CC BY-NC-SA 4.0. No weights
  downloaded yet.

A future detector should emit the same activation event as the keyboard. Continuous
PCM capture, pre-roll, echo cancellation, VAD and turn-end handling are a separate
speech extension. Wake-word recognition is not provided by VAD. Test false triggers
against TV/speaker playback, quiet speech, accents, pauses and device changes. Keep
listening opt-in and provide an obvious disarm control. Do not replace the own-agent
runtime with another agent framework to obtain wake-word detection.
