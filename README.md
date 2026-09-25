# Aloy

A small Mac companion with a draggable orb, saved text conversations, local speech, and an explicit Gemini Live audio mode. The first text agent is deliberately simple: system prompt plus the selected conversation's ordered history and the new message produce one streamed answer. Python owns the agent and storage; the Swift/AppKit shell owns the microphone, windows and playback.

## Run locally

Requires macOS on Apple Silicon, Python 3.13, Swift and FFmpeg.

```sh
runtime="$HOME/Library/Application Support/Aloy/runtime"
mkdir -p "$runtime"
python3.13 -m venv "$runtime/venv"
ln -s "$runtime/venv" .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e '.[speech]'
.venv/bin/python -m aloy.models vad qwen-asr chatterbox chatterbox-tokenizer
zsh mac/build.sh
open mac/build/Aloy.app
```

Create `~/Library/Application Support/Aloy/credentials.env` with `GEMINI_API_KEY` and/or `OPENROUTER_API_KEY`, and restrict it with `chmod 600`. An ignored project `.env` remains a fallback for terminal development. The Mac app uses the one runtime under Application Support, where it installs a small wheel of the current source during `mac/build.sh`. The project `.venv` is only a link, so this does not duplicate the speech dependencies. The default is Gemini 3.5 Flash-Lite text, local Silero speech detection, Qwen3 ASR 1.7B, and Chatterbox Multilingual V3 speech. Chatterbox needs one private 24 kHz mono WAV voice reference at `~/Library/Application Support/Aloy/models/chatterbox-reference.wav`; keep that file local and use a voice you have permission to use. OpenRouter DeepSeek V4.1 Flash, Gemini 3.8 Flash, Gemini Flash-Lite TTS (Achird), and text-only playback are selectable. If a selected provider is unavailable, Aloy reports the error rather than silently changing models.

The first Record click asks macOS for microphone access. Aloy shows Recording only after capture starts. Use **Finish & Send** to transcribe/send, or **Cancel recording** to keep the recording locally without sending it. Right-click Replay to hear the last input recording; a normal click replays the last reply. Development rebuilds may prompt for microphone permission again.

Aloy runs when you open the app and remains available as one floating orb while its process is alive. Closing the chat panel leaves the orb running; click it to reopen the panel. This first build has no login item or automatic crash restart yet.

The Codex option uses a pinned local `codex` 0.156.1 app-server, a separate Aloy configuration directory, an empty workspace and tool restrictions. Install it once with `npm install --prefix "$runtime/codex-cli" @openai/codex@0.156.1`. For this Mac, Aloy's isolated home links to the existing local ChatGPT login at `~/.codex/auth.json`; the link and credentials stay outside Git. Alternatively, sign in inside Aloy's `codex-home` with the Codex CLI. The global CLI is untouched.

## Checks and data

```sh
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/pytest -q
.venv/bin/python -m aloy.smoke                    # offline fake, default
.venv/bin/python -m aloy.smoke --mode live --provider gemini
.venv/bin/python -m aloy.smoke --mode live --provider openrouter
.venv/bin/python -m aloy.smoke --mode live --provider codex
```

Each direct-provider live smoke command uses one synthetic, capped text request with retries and fallback disabled. Codex is a separately labelled provider-managed turn. Normal tests never use the network. Recordings, transcripts, run status and an independent spend ledger live under `~/Library/Application Support/Aloy`. Deleting a conversation removes its recordings and messages, while retaining the spend ledger. Persistent reservations prevent concurrent work from bypassing the estimated $30 monthly allowance; uncertain dispatched work remains conservatively accounted. Aloy warns at $20. These are estimates and reservations, not billing records. Synthetic smoke conversations are removed after each check while their spending remains accounted.

Selected weights are larger than the original lightweight build; see the voice review for measured storage and latency. `.venv/bin/python -m aloy.models --prune` removes unselected weights from Aloy's dedicated cache. It does not touch other Hugging Face caches or the user's files. The one Python environment and pinned Codex CLI are also needed to run this build.

Experimental pronunciation feedback accepts a short mono 16 kHz WAV recording with `python -m aloy.pronunciation path.wav 'expected German phrase'`. It sends the actual audio to Gemini through the Interactions API and stores the result locally. It is coaching feedback, not a validated score.

## Current boundary

Live audio is a bounded, click-to-record native audio exchange after recording stops. The first build does not stream microphone frames while you speak. The earlier Pocket-based build (now replaced) emitted first audio in a median 1.11 seconds across three tiny synthetic samples; this excludes microphone/STT time and is not an acoustic latency measurement. German curriculum, learner mastery, generative lesson visuals, screen context, wake words and proactive reminders remain future work. See [engineering rules](docs/ENGINEERING.md), [implementation plan](docs/INITIAL_PLAN.md), and the current [repair review](docs/REPAIR_REVIEW.md).

## Global voice shortcut

With Aloy running, press and release **Option + Z** to record from any app.
Press and release it again to send; the selected speech engine reads the reply.
The panel stays closed. The orb is red while capturing. Using the shortcut during
playback interrupts that reply and starts a new recording. Hold/repeat generates
only one action on release. Record / Finish & Send use the same native functions.
A shortcut conflict produces an alert rather than silently failing. This shortcut
requires Aloy to be running; it does not wake a sleeping Mac or launch a quit app.
Microphone permission is required; no accessibility key-monitoring permission is used.
The existing five-minute recording limit still applies.

Voice wake research is in [VOICE_ACTIVATION.md](docs/VOICE_ACTIVATION.md).

Option + X cancels a recording or stops a reply without sending a query. Cancelled
recordings remain local under the existing retention policy. Offline speech uses a
Silero gate before ASR; silence yields “No speech detected” and no agent dispatch.
The compact particle orb is red when recording, mint when speaking, and amber on
errors, with faster internal motion while processing.

See [offline voice review](docs/OFFLINE_VOICE_REVIEW.md) for the latest model comparison
and acceptance evidence.
