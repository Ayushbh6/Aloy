# Voice activation

Option + Space is a registered global Carbon hotkey, activated on release. Both
shortcut and button call the existing toggleRecording function: start/finish capture,
existing recorded transport, local STT, canonical agent stream and selected TTS.
No new provider pipeline or speech dependencies are added. Starting while speaking
uses the existing cancellation and operation-ID machinery. The orb indicates capture
in red and panel visibility is unaffected. Registration errors are explicit.

Native tests exercise held-key repeat and unmatched releases. Python regression
checks cover the reused backend. A physical global-shortcut check remains required;
our UI automation tool cannot emit system-wide hotkeys.

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
