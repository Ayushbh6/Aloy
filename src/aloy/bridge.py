"""Versioned JSON-lines transport for the thin macOS shell."""

import asyncio
import json
import re
import sys
import uuid
import wave
from contextlib import aclosing
from pathlib import Path

from aloy import AgentConfig, ChatAgent, ConversationStore
from aloy.budget import MONTHLY_LIMIT_USD, MONTHLY_WARNING_USD, tts_cost
from aloy.contracts import Usage
from aloy.live import MAX_SECONDS, GeminiLive, _pcm_from_wav
from aloy.providers import MODEL_PRESETS, load_local_env, make_provider
from aloy.speech import MLXTranscriber, make_synthesizer, to_16k_wav

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
        self.transcriber = MLXTranscriber()
        self.synthesizers = {}
        self.active: asyncio.Task | None = None
        self.epoch = 0

    def emit(self, kind: str, **data) -> None:
        print(json.dumps({"v": VERSION, "event": kind, **data}, ensure_ascii=False), flush=True)

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

    async def speak(
        self, queue: asyncio.Queue, conversation_id: str, engine: str, epoch: int
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
                reservation = (
                    tts_cost(len(sentence) / 2, len(sentence)) if engine == "gemini" else 0.0
                )
                if self.store.monthly_spend() + reservation > MONTHLY_LIMIT_USD:
                    self.emit("speech_error", error="Monthly estimated spend limit reached")
                    return False
                data = await synthesizer.synthesize(sentence)
                if epoch != self.epoch:
                    return False
                with wave.open(__import__("io").BytesIO(data), "rb") as reader:
                    duration = reader.getnframes() / reader.getframerate()
                estimate = tts_cost(duration, len(sentence)) if engine == "gemini" else 0.0
                asset = self.store.save_audio(
                    conversation_id,
                    data,
                    direction="output",
                    extension="wav",
                    duration_seconds=duration,
                    provider=engine,
                    estimated_usd=estimate,
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
            self.emit("speech_error", error=f"{type(exc).__name__}: {str(exc)[:160]}")
            return False

    async def run_turn(
        self,
        conversation_id: str,
        text: str,
        provider_name: str,
        speech_engine: str | None,
        epoch: int,
    ) -> None:
        queue = asyncio.Queue()
        speech_task = (
            asyncio.create_task(self.speak(queue, conversation_id, speech_engine, epoch))
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
            agent = ChatAgent(config, self.store, make_provider(provider_name, self.store))
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
                        self.emit("error", error=event.error, run_id=event.run_id)
            if speech_task:
                if buffer.strip():
                    await queue.put(buffer.strip())
                await queue.put(None)
                speech_ok = await speech_task
                failed = failed or not speech_ok
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
            self.emit("error", error=f"{type(exc).__name__}: {str(exc)[:160]}")

    async def transcribe_and_run(
        self,
        conversation_id: str,
        path: Path,
        provider_name: str,
        speech_engine: str | None,
        epoch: int,
    ) -> None:
        try:
            if not path.resolve().is_relative_to(self.store.root.resolve()):
                raise ValueError("Recording is outside Aloy's data folder")
            original = path.read_bytes()
            self.store.save_audio(conversation_id, original, direction="input", extension="m4a")
            wav = to_16k_wav(path)
            path.unlink(missing_ok=True)
            tmp = self.store.root / "tmp" / f"{uuid.uuid4()}.wav"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(wav)
            try:
                text = await self.transcriber.transcribe(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            if not text:
                raise RuntimeError("No speech was detected")
            self.emit("transcript", text=text)
            await self.run_turn(conversation_id, text, provider_name, speech_engine, epoch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.emit("error", error=f"{type(exc).__name__}: {str(exc)[:160]}")

    async def run_live(self, conversation_id: str, path: Path, epoch: int) -> None:
        run_id = None
        try:
            if not path.resolve().is_relative_to(self.store.root.resolve()):
                raise ValueError("Recording is outside Aloy's data folder")
            wav = to_16k_wav(path)
            _, duration = _pcm_from_wav(wav)
            reservation = duration * 0.005 / 60 + MAX_SECONDS * 0.018 / 60 + 0.05
            if self.store.monthly_spend() + reservation > MONTHLY_LIMIT_USD:
                raise RuntimeError("Monthly estimated spend limit reached")
            history = self.store.completed_history(conversation_id)
            run_id = self.store.begin_run(
                conversation_id, "gemini-live", "gemini-3.8-live", "[voice input]"
            )
            self.emit("live_processing", run_id=run_id, conversation_id=conversation_id)
            self.store.save_audio(
                conversation_id,
                path.read_bytes(),
                direction="input",
                extension="m4a",
                run_id=run_id,
                duration_seconds=duration,
            )
            path.unlink(missing_ok=True)
            result = await GeminiLive().exchange(history, wav)
            if epoch != self.epoch:
                raise asyncio.CancelledError
            self.store.update_user_text(run_id, result.input_text)
            estimate = duration * 0.005 / 60 + result.output_seconds * 0.018 / 60
            asset = self.store.save_audio(
                conversation_id,
                result.output_wav,
                direction="output",
                extension="wav",
                run_id=run_id,
                duration_seconds=result.output_seconds,
            )
            self.store.finish_run(
                run_id, "completed", result.output_text, Usage(estimated_usd=estimate)
            )
            self.emit("transcript", text=result.input_text)
            self.emit("started", run_id=run_id, conversation_id=conversation_id)
            self.emit("delta", text=result.output_text, run_id=run_id)
            self.emit(
                "completed",
                run_id=run_id,
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
            )
            self.emit("turn_done", conversation_id=conversation_id)
        except asyncio.CancelledError:
            if run_id:
                self.store.finish_run(run_id, "cancelled")
            raise
        except Exception as exc:
            if run_id:
                self.store.finish_run(run_id, "failed", error=str(exc))
            self.emit("error", error=f"{type(exc).__name__}: {str(exc)[:160]}")
        finally:
            path.unlink(missing_ok=True)

    async def handle(self, request: dict) -> None:
        if request.get("v") != VERSION:
            self.emit("error", error="Unsupported transport version")
            return
        request_id = request.get("id")
        action = request.get("action")
        try:
            if action == "new":
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
                    spend_usd=round(self.store.monthly_spend(), 4),
                )
            elif action == "set_setting":
                key, value = request["key"], request["value"]
                allowed = {
                    "provider": set(MODEL_PRESETS),
                    "speech": {"pocket", "gemini", "none"},
                    "mode": {"standard", "live"},
                }
                if key not in allowed or value not in allowed[key]:
                    raise ValueError("Unsupported setting")
                self.store.set_setting(key, value)
                self.emit("settings_saved", id=request_id)
            elif action == "stop":
                await self.stop()
            elif action in ("send", "recorded"):
                await self.stop()
                provider_name = request.get("provider", "gemini")
                if provider_name not in MODEL_PRESETS:
                    raise ValueError("Unknown provider")
                speech_engine = request.get("speech", "pocket")
                if speech_engine not in (None, "gemini", "pocket"):
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
            self.emit("error", id=request_id, error=f"{type(exc).__name__}: {str(exc)[:160]}")


async def main() -> None:
    bridge = Bridge()
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
        bridge.store.close()


if __name__ == "__main__":
    asyncio.run(main())
