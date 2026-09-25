"""Versioned JSON-lines transport for the thin macOS shell."""

import asyncio
import base64
import json
import re
import sys
import uuid
import wave
from contextlib import aclosing
from contextvars import ContextVar
from pathlib import Path

from aloy import AgentConfig, ChatAgent, ConversationStore
from aloy.budget import MONTHLY_WARNING_USD
from aloy.contracts import Usage
from aloy.errors import safe_error
from aloy.lifecycle import RunLifecycle
from aloy.live import MAX_SECONDS, GeminiLive, _pcm_from_wav
from aloy.providers import MODEL_PRESETS, load_local_env, make_provider
from aloy.speech import MLXTranscriber, make_synthesizer, to_16k_wav
from aloy.vad import StreamingVoiceDetector

OPERATION = ContextVar("operation", default=None)
VERSION = 1
PROMPT = (
    "You are Aloy, Ayush's companion. Be warm, natural and concise. "
    "Write for speech: short sentences, one question at a time, no Markdown. "
    "When German is relevant, explain clearly at roughly A1 level."
)


class Bridge:
    def __init__(self) -> None:
        load_local_env()
        self.store = ConversationStore()
        if self.store.settings().get("speech") not in (None, "chatterbox", "none"):
            self.store.set_setting("speech", "chatterbox")
        self.transcriber = MLXTranscriber()
        self.voice_monitor = StreamingVoiceDetector()
        self.synthesizers = {}
        self.warm_task: asyncio.Task | None = None
        self.active: asyncio.Task | None = None
        self.epoch = 0

    def emit(self, kind: str, **data) -> None:
        print(
            json.dumps(
                {"v": VERSION, "event": kind, "operation_id": OPERATION.get(), **data},
                ensure_ascii=False,
            ),
            flush=True,
        )

    async def stop(self) -> None:
        self.epoch += 1
        if self.active and not self.active.done():
            self.active.cancel()
            try:
                await self.active
            except asyncio.CancelledError:
                pass
        self.active = None
        self.emit("stopped")

    async def warm_speech(self, engine: str | None) -> None:
        try:
            tasks = [self.transcriber.prewarm()]
            if engine == "chatterbox":
                synthesizer = self.synthesizers.get("chatterbox")
                if synthesizer is None:
                    synthesizer = make_synthesizer("chatterbox")
                    self.synthesizers["chatterbox"] = synthesizer
                tasks.append(synthesizer.prewarm())
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
            self.emit("speech_warm")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.emit("speech_error", error=safe_error(exc))

    async def speak(
        self, queue: asyncio.Queue, conversation_id: str, engine: str, epoch: int, run_ref: dict
    ) -> bool:
        try:
            while True:
                sentence = await queue.get()
                if sentence is None:
                    return True
                synthesizer = self.synthesizers.get(engine)
                if synthesizer is None:
                    synthesizer = make_synthesizer(engine)
                    self.synthesizers[engine] = synthesizer
                data = await synthesizer.synthesize(sentence)
                with wave.open(__import__("io").BytesIO(data), "rb") as reader:
                    duration = reader.getnframes() / reader.getframerate()
                if epoch != self.epoch:
                    return False
                run_id = run_ref["id"]
                asset = self.store.save_audio(
                    conversation_id,
                    data,
                    direction="output",
                    extension="wav",
                    duration_seconds=duration,
                    provider=engine,
                    estimated_usd=0.0,
                    run_id=run_id,
                    message_id=self.store.message_id(run_id, "assistant"),
                    accounted=True,
                )
                self.emit(
                    "audio",
                    path=asset["path"],
                    asset_id=asset["id"],
                    text=sentence,
                    conversation_id=conversation_id,
                )
                queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.emit("speech_error", error=safe_error(exc))
            return False

    def validate_recording(self, path: Path):
        if not path.resolve().is_relative_to(
            (self.store.root / "tmp").resolve()
        ) or path.suffix.lower() not in {".wav", ".m4a"}:
            raise ValueError("Recording must be a WAV or M4A in Aloy's capture folder")

    def record_input_outcome(self, conversation_id: str, asset_id: str, status: str):
        if self.store.audio_asset(asset_id)["run_id"]:
            return
        with RunLifecycle(
            self.store, conversation_id, "local-recording", "none", "[untranscribed recording]", 0
        ) as run:
            self.store.link_audio(asset_id, run.run_id, "user")
            self.store.exclude_run_input(run.run_id, status)
            run.finish(status)

    async def run_turn(
        self,
        conversation_id: str,
        text: str,
        provider_name: str,
        speech_engine: str | None,
        epoch: int,
        input_asset: str | None = None,
    ) -> None:
        run_ref = {}
        provider = None
        queue = asyncio.Queue()
        speech_task = (
            asyncio.create_task(self.speak(queue, conversation_id, speech_engine, epoch, run_ref))
            if speech_engine
            else None
        )
        buffer = ""
        failed = False
        try:
            config = AgentConfig(
                system_prompt=PROMPT,
                provider=provider_name,
                model=MODEL_PRESETS[provider_name],
            )
            provider = make_provider(provider_name, self.store)
            agent = ChatAgent(config, self.store, provider)
            async with aclosing(agent.stream(conversation_id, text)) as events:
                async for event in events:
                    if epoch != self.epoch:
                        return
                    if event.kind == "delta":
                        self.emit("delta", text=event.text, run_id=event.run_id)
                        buffer += event.text
                        while match := re.search(r"[.!?](?:\s|$)", buffer):
                            sentence = buffer[: match.end()].strip()
                            buffer = buffer[match.end() :]
                            if sentence and speech_task:
                                await queue.put(sentence)
                    elif event.kind == "started":
                        run_ref["id"] = event.run_id
                        if input_asset:
                            self.store.link_audio(input_asset, event.run_id, "user")
                        self.emit("started", run_id=event.run_id, conversation_id=conversation_id)
                    elif event.kind == "completed":
                        self.emit(
                            "completed",
                            run_id=event.run_id,
                            text=event.text,
                            spend_usd=round(self.store.monthly_spend(), 4),
                            budget_warning=self.store.monthly_spend() >= MONTHLY_WARNING_USD,
                        )
                    elif event.kind == "failed":
                        failed = True
                        if speech_task:
                            speech_task.cancel()
                        self.emit("error", error=event.error, run_id=event.run_id)
            if speech_task and not failed:
                if buffer.strip():
                    await queue.put(buffer.strip())
                await queue.put(None)
                speech_ok = await speech_task
                failed = failed or not speech_ok
            if run_ref:
                self.store.attach_run_audio(run_ref["id"])
            if epoch == self.epoch:
                self.emit("turn_done", conversation_id=conversation_id, failed=failed)
        except asyncio.CancelledError:
            if speech_task:
                speech_task.cancel()
                try:
                    await speech_task
                except asyncio.CancelledError:
                    pass
            raise
        except Exception as exc:
            self.emit("error", error=safe_error(exc))
        finally:
            if speech_task:
                if not speech_task.done():
                    speech_task.cancel()
                await asyncio.gather(speech_task, return_exceptions=True)
            if provider and hasattr(provider, "close"):
                await provider.close()
            if input_asset and not run_ref:
                self.record_input_outcome(conversation_id, input_asset, "failed")

    async def transcribe_and_run(
        self,
        conversation_id: str,
        path: Path,
        provider_name: str,
        speech_engine: str | None,
        epoch: int,
    ) -> None:
        input_asset = None
        try:
            self.validate_recording(path)
            original = path.read_bytes()
            input_asset = self.store.save_audio(
                conversation_id,
                original,
                direction="input",
                extension="wav" if original.startswith(b"RIFF") else "m4a",
            )
            wav = await asyncio.to_thread(to_16k_wav, path)
            _, duration = _pcm_from_wav(wav)
            self.store.set_audio_duration(input_asset["id"], duration)
            path.unlink(missing_ok=True)
            tmp = self.store.root / "tmp" / f"{uuid.uuid4()}.wav"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(wav)
            try:
                text = await self.transcriber.transcribe(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            if not text:
                self.record_input_outcome(conversation_id, input_asset["id"], "cancelled")
                self.emit("no_speech", message="No speech detected — nothing sent")
                return
            self.emit("transcript", text=text)
            await self.run_turn(
                conversation_id, text, provider_name, speech_engine, epoch, input_asset["id"]
            )
        except asyncio.CancelledError:
            if input_asset:
                self.record_input_outcome(conversation_id, input_asset["id"], "cancelled")
            raise
        except Exception as exc:
            if input_asset:
                self.record_input_outcome(conversation_id, input_asset["id"], "failed")
            self.emit("error", error=safe_error(exc))
        finally:
            if input_asset:
                path.unlink(missing_ok=True)

    async def run_live(self, conversation_id: str, path: Path, epoch: int) -> None:
        saved_input = False
        try:
            self.validate_recording(path)
            wav = await asyncio.to_thread(to_16k_wav, path)
            _, duration = _pcm_from_wav(wav)
            history = self.store.completed_history(conversation_id)
            # Reserve audio's bounded duration plus worst-case text context/output.
            reservation = duration * 0.005 / 60 + MAX_SECONDS * 0.018 / 60
            reservation += (sum(len(m.text) for m in history) + len(PROMPT)) * 1.5 / 1e6 + 0.05
            with RunLifecycle(
                self.store,
                conversation_id,
                "gemini-live",
                "gemini-3.8-live",
                "[voice input]",
                reservation,
            ) as run:
                self.emit("live_processing", run_id=run.run_id, conversation_id=conversation_id)
                self.store.save_audio(
                    conversation_id,
                    path.read_bytes(),
                    direction="input",
                    extension="wav" if path.suffix.lower() == ".wav" else "m4a",
                    run_id=run.run_id,
                    duration_seconds=duration,
                    message_id=self.store.message_id(run.run_id, "user"),
                )
                saved_input = True
                path.unlink(missing_ok=True)
                run.dispatched()
                result = await GeminiLive().exchange(history, wav, system_prompt=PROMPT)
                if epoch != self.epoch:
                    raise asyncio.CancelledError
                self.store.update_user_text(run.run_id, result.input_text)
                asset = self.store.save_audio(
                    conversation_id,
                    result.output_wav,
                    direction="output",
                    extension="wav",
                    run_id=run.run_id,
                    duration_seconds=result.output_seconds,
                )
                # Retain the conservative reservation when modality usage is unavailable.
                run.complete(
                    result.output_text,
                    Usage(result.input_tokens, result.output_tokens, result.estimated_usd),
                )
                self.emit("transcript", text=result.input_text)
                self.emit("started", run_id=run.run_id, conversation_id=conversation_id)
                self.emit("delta", text=result.output_text, run_id=run.run_id)
                self.emit(
                    "completed",
                    run_id=run.run_id,
                    text=result.output_text,
                    spend_usd=round(self.store.monthly_spend(), 4),
                    budget_warning=self.store.monthly_spend() >= MONTHLY_WARNING_USD,
                )
                self.emit(
                    "audio",
                    path=asset["path"],
                    asset_id=asset["id"],
                    text=result.output_text,
                    conversation_id=conversation_id,
                    run_id=run.run_id,
                )
                self.emit("turn_done", conversation_id=conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.emit("error", error=safe_error(exc))
        finally:
            if saved_input:
                saved_input = True
                path.unlink(missing_ok=True)

    def retain_cancelled_recording(self, conversation_id: str, path: Path, duration: float):
        self.validate_recording(path)
        with RunLifecycle(
            self.store, conversation_id, "local-recording", "none", "[cancelled recording]", 0
        ) as run:
            asset = self.store.save_audio(
                conversation_id,
                path.read_bytes(),
                direction="input",
                extension="m4a",
                run_id=run.run_id,
                message_id=self.store.message_id(run.run_id, "user"),
                duration_seconds=duration,
            )
            self.store.exclude_run_input(run.run_id)
            run.finish("cancelled")
        path.unlink(missing_ok=True)
        self.emit("recording_retained", asset_id=asset["id"], conversation_id=conversation_id)

    async def handle(self, request: dict) -> None:
        if not isinstance(request, dict):
            self.emit("error", error="Transport request must be an object")
            return
        OPERATION.set(request.get("operation_id"))
        if request.get("v") != VERSION:
            self.emit("error", error="Unsupported transport version")
            return
        request_id = request.get("id")
        action = request.get("action")
        try:
            if action in {"new", "select", "delete"}:
                await self.stop()
            if action == "metric":
                self.store.record_client_metric(request["kind"], float(request["value"]))
            elif action == "vad_start":
                await asyncio.to_thread(
                    self.voice_monitor.start,
                    request["session_id"],
                    int(request["sample_rate"]),
                )
            elif action == "vad_audio":
                payload = request["pcm"]
                if len(payload) > 100_000:
                    raise ValueError("Microphone frame exceeds transport limit")
                change = await asyncio.to_thread(
                    self.voice_monitor.feed,
                    request["session_id"],
                    base64.b64decode(payload, validate=True),
                )
                if change:
                    self.emit("speech_activity", session_id=request["session_id"], state=change)
            elif action == "vad_end":
                self.voice_monitor.stop(request["session_id"])
            elif action == "prewarm_speech":
                engine = request.get("speech")
                if engine not in (None, "chatterbox"):
                    raise ValueError("Unknown speech engine")
                if self.warm_task is None or self.warm_task.done():
                    self.warm_task = asyncio.create_task(self.warm_speech(engine))
            elif action == "cancel_recording":
                self.retain_cancelled_recording(
                    request["conversation_id"],
                    Path(request["path"]),
                    float(request.get("duration", 0)),
                )
            elif action == "playback":
                self.store.mark_playback(request["asset_id"], request["status"])
            elif action == "new":
                conversation_id = self.store.create_conversation(PROMPT)
                self.emit(
                    "conversation", id=request_id, conversation_id=conversation_id, messages=[]
                )
            elif action == "list":
                self.emit("conversations", id=request_id, items=self.store.list_conversations())
            elif action == "select":
                conversation_id = request["conversation_id"]
                self.emit(
                    "conversation",
                    id=request_id,
                    conversation_id=conversation_id,
                    messages=self.store.messages(conversation_id),
                    audio=self.store.audio_assets(conversation_id),
                )
            elif action == "delete":
                self.store.delete_conversation(request["conversation_id"])
                self.emit("deleted", id=request_id)
            elif action == "storage":
                self.emit(
                    "storage",
                    id=request_id,
                    bytes=self.store.storage_bytes(),
                    retained_orphans=len(self.store.orphan_audio()),
                    spend_usd=round(self.store.monthly_spend(), 4),
                )
            elif action == "set_setting":
                key, value = request["key"], request["value"]
                allowed = {
                    "provider": set(MODEL_PRESETS),
                    "speech": {"chatterbox", "none"},
                    "mode": {"standard", "live"},
                }
                if key not in allowed or value not in allowed[key]:
                    raise ValueError("Unsupported setting")
                await self.stop()
                if key == "speech":
                    if value == "none" and self.warm_task and not self.warm_task.done():
                        self.warm_task.cancel()
                        await asyncio.gather(self.warm_task, return_exceptions=True)
                    for engine, synthesizer in list(self.synthesizers.items()):
                        if engine != value and hasattr(synthesizer, "close"):
                            await synthesizer.close()
                            del self.synthesizers[engine]
                self.store.set_setting(key, value)
                self.emit("settings_saved", id=request_id)
            elif action == "stop":
                await self.stop()
            elif action in ("send", "recorded"):
                await self.stop()
                provider_name = request.get("provider", "gemini")
                if provider_name not in MODEL_PRESETS:
                    raise ValueError("Unknown provider")
                speech_engine = request.get("speech", "chatterbox")
                if speech_engine not in (None, "chatterbox"):
                    raise ValueError("Unknown speech engine")
                conversation_id = request["conversation_id"]
                if action == "send":
                    self.active = asyncio.create_task(
                        self.run_turn(
                            conversation_id,
                            request["text"],
                            provider_name,
                            speech_engine,
                            self.epoch,
                        )
                    )
                else:
                    if request.get("mode") == "live":
                        self.active = asyncio.create_task(
                            self.run_live(conversation_id, Path(request["path"]), self.epoch)
                        )
                    else:
                        self.active = asyncio.create_task(
                            self.transcribe_and_run(
                                conversation_id,
                                Path(request["path"]),
                                provider_name,
                                speech_engine,
                                self.epoch,
                            )
                        )
            else:
                raise ValueError("Unknown action")
        except Exception as exc:
            self.emit("error", id=request_id, error=safe_error(exc))


async def main() -> None:
    # One desktop backend owns recovery and conversation mutation at a time.
    import fcntl

    from aloy.storage import data_root

    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / "desktop.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Aloy backend is already running") from None
    bridge = Bridge()
    bridge.store.recover()
    bridge.emit(
        "ready",
        root=str(bridge.store.root),
        providers=MODEL_PRESETS,
        settings=bridge.store.settings(),
    )
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                await bridge.handle(json.loads(line))
            except (ValueError, TypeError) as exc:
                bridge.emit("error", error=f"Invalid request: {exc}")
    finally:
        await bridge.stop()
        if bridge.warm_task and not bridge.warm_task.done():
            bridge.warm_task.cancel()
            await asyncio.gather(bridge.warm_task, return_exceptions=True)
        await bridge.transcriber.close()
        for synthesizer in bridge.synthesizers.values():
            if hasattr(synthesizer, "close"):
                await synthesizer.close()
        bridge.store.close()


if __name__ == "__main__":
    asyncio.run(main())
