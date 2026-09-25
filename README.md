# Aloy

A Mac companion with a quiet draggable particle orb, a current-exchange bubble, a full conversation app, and a native desktop teaching canvas. Python owns one multi-step agent pipeline and canonical SQLite storage; the Swift/AppKit shell owns the microphone, windows, screen capture and playback. **Expand** opens the full app; settings and technical inspection stay out of the compact bubble. See [Chunk 2 verification and owner checks](docs/CHUNK2_REVIEW.md) and the [approved design reference](docs/design/README.md).

## Run locally

Requires macOS 15 or newer on Apple Silicon, Python 3.13, Swift and FFmpeg. For semantic memory indexing, install Ollama and `ollama pull embeddinggemma:latest`; chat still works with SQLite search when Ollama is unavailable.

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

Create `~/Library/Application Support/Aloy/credentials.env` with `GEMINI_API_KEY` and/or `OPENROUTER_API_KEY`, and restrict it with `chmod 600`. An ignored project `.env` remains a fallback for terminal development. The Mac app uses the one runtime under Application Support, where it installs a small wheel of the current source during `mac/build.sh`. The project `.venv` is only a link, so this does not duplicate the speech dependencies. The default is Gemini 3.5 Flash-Lite text, local Silero speech detection, Qwen3 ASR 1.7B, and Chatterbox Multilingual V3 speech. The speech picker contains exactly three choices: Chatterbox local, direct Gemini 3.8 Flash-Lite TTS, and `x-ai/grok-voice-tts-1.0` through OpenRouter. A separate voice picker remembers a selection for each engine. Defaults are the existing warm male Chatterbox reference, Gemini Achird, and Grok Sal (the latter still needs a personal audition). Chatterbox uses the private 24 kHz mono WAV at `~/Library/Application Support/Aloy/models/chatterbox-reference.wav`; additional consented WAV references placed in `~/Library/Application Support/Aloy/models/voices/` appear after restarting Aloy, without downloading another model. Gemini offers its 30 prebuilt voices; OpenRouter Grok offers five. Text-provider choices remain separate. If a selected speech route is unavailable or over budget, Aloy reports the error rather than silently changing voices or charging another provider.

The first Record click asks macOS for microphone access. Aloy shows Recording only after capture starts. Use **Finish & Send** to transcribe/send, or **Cancel recording** to keep the recording locally without sending it. Right-click Replay to hear the last input recording; a normal click replays the last reply. Development rebuilds may prompt for microphone permission again.

While Standard recording is open, local Silero VAD shows when speech starts and when a long pause begins; a pause does not automatically send the turn. The final VAD pass trims only leading/trailing silence before Qwen ASR, keeping pauses and fillers between spoken phrases. Chatterbox warms in the background during recording. Option + Z still sends; Option + X cancels.

Aloy runs when you open the app and remains available as one floating orb while its process is alive. Closing the chat panel leaves the orb running; click it to reopen the panel. This first build has no login item or automatic crash restart yet.

The Codex option uses a pinned local `codex` 0.156.1 app-server, a separate Aloy configuration directory, an empty workspace and tool restrictions. Install it once with `npm install --prefix "$runtime/codex-cli" @openai/codex@0.156.1`. For this Mac, Aloy's isolated home links to the existing local ChatGPT login at `~/.codex/auth.json`; the link and credentials stay outside Git. Alternatively, sign in inside Aloy's `codex-home` with the Codex CLI. The global CLI is untouched.

The standard text agent uses a shared eight-step tool loop for Gemini, OpenRouter GLM 5.3 Flash, Codex dynamic tools, and the offline fake. Its initial tools cover cited web search, durable memory, foreground-window screenshots, bounded screen clips and image/video inspection. Screen capture requires a clear current-turn request and macOS Screen Recording permission. Clips last at most 60 seconds, include system audio but not microphone audio, and show a persistent recording indicator. There is no background monitoring, clicking or typing. The playground offers conversation/provider/vision-route selection, tool toggles, attachments, activity, citations, media and a minimal memory inspector. It is a testing surface, not yet the final chat app.

SQLite keeps the raw transcript, summaries, memories, run steps, assets and spend ledger. LanceDB under `~/Library/Application Support/Aloy/index/lancedb` is a rebuildable search projection; it never replaces SQLite or stores raw audio/video. To rebuild after an embedding model change, run `.venv/bin/python -m aloy.memory_index rebuild`. Indexing failures leave SQLite chat available and queue work for retry.

Attachments support PNG/JPEG, MP4/MOV and WAV/MP3/M4A (up to four per turn).
Video/audio inspection accepts clips up to 60 seconds and prepares a bounded
private copy before sending it to the explicitly selected vision service.
Codex receives images directly; audio/video become timestamped evidence through
the selected vision route. Captured media stays local unless the current request
asks Aloy to inspect it; inspection sends that selected media to its named API.
Deleting a memory removes it from durable recall, not the permanent raw transcript;
delete its conversation to remove that conversation's messages and media as well.

See [Chunk 1 review and acceptance checks](docs/CHUNK1_REVIEW.md) for current
verification and the remaining hands-on checks. No UI automation is needed to run
the offline suite or the synthetic provider checks below.

## Checks and data

```sh
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/pytest -q
.venv/bin/python -m aloy.smoke                    # offline fake, default
.venv/bin/python -m aloy.smoke --mode live --provider gemini
.venv/bin/python -m aloy.smoke --mode live --provider openrouter
.venv/bin/python -m aloy.smoke --mode live --provider codex
PYTHONPATH=src .venv/bin/python -m aloy.speech_smoke --mode fake --option gemini-lite
PYTHONPATH=src .venv/bin/python -m aloy.speech_smoke --mode live --option gemini-lite
PYTHONPATH=src .venv/bin/python -m aloy.speech_smoke --mode live --option router-grok --voice sal
PYTHONPATH=src .venv/bin/python -m aloy.chunk1_smoke --mode fake
PYTHONPATH=src .venv/bin/python -m aloy.chunk1_smoke --mode live --scenario tools-gemini
```

Each direct-provider live smoke command uses one synthetic, capped text request with retries and fallback disabled. Codex is a separately labelled provider-managed turn. The separate speech smoke accepts any paid picker ID and makes one short synthesis request through the normal speech, storage and spending path. Normal tests never use the network. Recordings, transcripts, run status and an independent spend ledger live under `~/Library/Application Support/Aloy`. Deleting a conversation removes its recordings and messages, while retaining the spend ledger. Persistent reservations prevent concurrent work from bypassing the estimated $30 monthly allowance; uncertain dispatched work remains conservatively accounted. Aloy warns at $20. These are estimates and reservations, not billing records. Synthetic smoke conversations are removed after each check while their spending remains accounted.

The separate `chunk1_smoke` harness explicitly permits two model dispatches for
tool continuation or extraction-plus-compaction; `web` and image/video scenarios
make one real helper request with a scripted main agent. Use `--help` for the
named scenarios. Each invocation isolates synthetic content from personal history,
uses a deadline and reserves against the real spend ledger. No automatic reruns.

Selected weights are larger than the original lightweight build; see the voice review for measured storage and latency. `.venv/bin/python -m aloy.models --prune` removes unselected weights from Aloy's dedicated cache. It does not touch other Hugging Face caches or the user's files. The one Python environment and pinned Codex CLI are also needed to run this build.

Experimental pronunciation feedback accepts a short mono 16 kHz WAV recording with `python -m aloy.pronunciation path.wav 'expected German phrase'`. It sends the actual audio to Gemini through the Interactions API and stores the result locally. It is coaching feedback, not a validated score.

## Current boundary

Live audio is a bounded, click-to-record native audio exchange after recording stops; microphone frames in Standard mode are monitored locally for speech activity but are not streamed to the Live provider. The current local Chatterbox path has not met the three-second stop-to-first-audio target. Screen perception is now implemented, but native recording still needs a manual permission/cancellation check on this build. German curriculum, learner mastery, generative lesson visuals, computer actions, wake words and proactive reminders remain future work. See [engineering rules](docs/ENGINEERING.md), [implementation plan](docs/INITIAL_PLAN.md), and the current [repair review](docs/REPAIR_REVIEW.md).

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
# Development restarts

After local changes, run `zsh mac/restart.sh` to gracefully quit this checkout's
Aloy app, wait for its backend lock to release, rebuild, and reopen. The helper
never force-quits or kills unrelated processes; a shutdown timeout aborts the
restart. Normal quitting is also available via **Quit Aloy / Command–Q** while
Aloy is active. Quitting cancels current work and preserves saved conversations
and recordings.
