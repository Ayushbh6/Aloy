"""Versioned JSON-lines transport for the thin macOS shell."""

import asyncio
import base64
import json
import re
import sys
import uuid
from contextlib import aclosing
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aloy import AgentConfig, ConversationStore
from aloy.agent import AgentRunner
from aloy.budget import MONTHLY_WARNING_USD
from aloy.canvas import validate_action_arguments
from aloy.contracts import AgentInput, Usage
from aloy.errors import safe_error
from aloy.lifecycle import RunLifecycle
from aloy.live import MAX_SECONDS, GeminiLive, _pcm_from_wav
from aloy.maintenance import FakeMaintenance, MaintenanceService
from aloy.memory_index import MemoryIndex
from aloy.providers import MODEL_PRESETS, load_local_env, make_provider
from aloy.speech import MLXTranscriber, make_synthesizer, to_16k_wav
from aloy.speech_catalog import (
    BY_ID,
    resolve_voice,
    selectable_options,
    speech_cost,
    speech_reservation,
)
from aloy.tools import TOOL_SPECS, VISION_MODELS, ToolRegistry
from aloy.vad import StreamingVoiceDetector

OPERATION = ContextVar("operation", default=None)
VERSION = 2
PROMPT = (
    "You are Aloy, Ayush's companion. Be warm, natural and concise. "
    "Write for speech: short sentences, one question at a time, no Markdown. "
    "When German is relevant, explain clearly at roughly A1 level."
)
DEFAULT_TOOLS = tuple(spec.name for spec in TOOL_SPECS if spec.permission != "action")


def clear_capture_request(text: str) -> bool:
    """Only an explicit current-turn request authorizes foreground capture."""
    if re.search(
        r"(?i)\b(?:don't|do not|never|without|no)\b.{0,40}"
        r"\b(?:capture|record|screenshot|screen|video)\b",
        text,
    ):
        return False
    return bool(
        re.search(
            r"(?i)^\s*(?:(?:please|kindly)\s+|(?:can|could|would)\s+you\s+|"
            r"i\s+want\s+you\s+to\s+)*"
            r"(check|look at|inspect|record|capture|see|watch)\b.*"
            r"\b(screen|window|video|clip)\b",
            text,
        )
    )


class Bridge:
    def __init__(self) -> None:
        load_local_env()
        self.store = ConversationStore()
        self.index = MemoryIndex(self.store)
        self.registry = ToolRegistry()
        self.index_task: asyncio.Task | None = None
        self.capture_waiters: dict[str, asyncio.Future] = {}
        self.action_waiters: dict[str, dict] = {}
        self.active_text = ""
        if self.store.settings().get("speech") not in (None, *BY_ID):
            self.store.set_setting("speech", "chatterbox")
        for engine in BY_ID:
            key = f"voice:{engine}"
            if key in self.store.settings():
                try:
                    resolve_voice(engine, self.store.settings()[key])
                except ValueError:
                    self.store.set_setting(key, BY_ID[engine].voice)
        self.transcriber = MLXTranscriber()
        self.voice_monitor = StreamingVoiceDetector()
        self.synthesizers = {}
        self.warm_task: asyncio.Task | None = None
        self.active: asyncio.Task | None = None
        self.epoch = 0

    def emit(self, event: str, **data) -> None:
        print(
            json.dumps(
                {"v": VERSION, "event": event, "operation_id": OPERATION.get(), **data},
                ensure_ascii=False,
            ),
            flush=True,
        )

    async def stop(self, announce: bool = True) -> None:
        self.epoch += 1
        for pending in tuple(self.action_waiters.values()):
            if pending["phase"] in {"requested", "executing"}:
                self.emit(
                    "agent_event",
                    agent_kind="action_cancelled",
                    conversation_id=pending.get("conversation_id"),
                    run_id=pending["run_id"],
                    data={
                        "proposal_id": pending["proposal_id"],
                        "operation_id": pending["operation_id"],
                        "reason": "operation_stopped",
                    },
                )
                pending["cancel_notified"] = True
        for waiter in self.capture_waiters.values():
            if not waiter.done():
                waiter.cancel()
        self.capture_waiters.clear()
        if self.active and not self.active.done():
            self.active.cancel()
            try:
                await self.active
            except asyncio.CancelledError:
                pass
        self.active = None
        if announce:
            self.emit("stopped")

    async def capture(self, kind: str, seconds: int, conversation_id: str) -> dict:
        capture_id = str(uuid.uuid4())
        waiter = asyncio.get_running_loop().create_future()
        self.capture_waiters[capture_id] = waiter
        self.emit(
            "capture_requested",
            capture_id=capture_id,
            kind=kind,
            seconds=seconds,
            conversation_id=conversation_id,
        )
        try:
            return await asyncio.wait_for(waiter, timeout=max(25, seconds + 25))
        except BaseException:
            self.emit("capture_cancelled", capture_id=capture_id)
            raise
        finally:
            self.capture_waiters.pop(capture_id, None)

    async def search_memories(self, query: str, mode: str = "hybrid") -> list[dict]:
        if mode not in {"hybrid", "fts", "exact", "substring", "regex"}:
            raise ValueError("Unsupported memory search mode")
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("Memory search query must be 1 to 200 characters")
        rows = []
        if mode == "hybrid" and self.index:
            try:
                async with asyncio.timeout(0.8):
                    rows = await self.index.search(query, 50)
            except Exception:
                rows = []

        def canonical_memories(items):
            results, seen = [], set()
            for row in items:
                if row.get("kind") != "memory":
                    continue
                memory_id = row.get("id", "").removeprefix("memory:").split(":", 1)[0]
                if not memory_id or memory_id in seen:
                    continue
                seen.add(memory_id)
                try:
                    item = self.store.memory(memory_id)
                except KeyError:
                    continue
                if item["status"] == "active":
                    results.append(item)
            return results

        results = canonical_memories(rows)
        if mode == "hybrid" and not results:
            results = canonical_memories(self.store.search_text(query, mode="fts", limit=50))
        elif mode != "hybrid":
            results = canonical_memories(self.store.search_text(query, mode=mode, limit=50))
        return results[:50]

    async def request_action(self, call, run_id, operation_id, publish) -> dict:
        if not operation_id or len(operation_id) > 200:
            return {"status": "stale", "error": "operation_id_required"}
        arguments = validate_action_arguments(call.name, call.arguments)
        proposal_id = str(uuid.uuid4())
        # Keep the tool name canonical across the approval bridge and native shell.
        action = call.name
        target = {"bundle_id": arguments["bundle_id"]}
        parameters = {}
        for key, value in arguments.items():
            if key == "bundle_id":
                continue
            if key == "window_id":
                target[key] = value
            else:
                parameters[key] = value
        proposal = {
            "proposal_id": proposal_id,
            "operation_id": operation_id,
            "run_id": run_id,
            "tool_call_id": call.id,
            "action": action,
            "target": target,
            "parameters": parameters,
            "expires_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        }
        loop = asyncio.get_running_loop()
        pending = {
            **proposal,
            "conversation_id": self.store.run(run_id)["conversation_id"],
            "decision": loop.create_future(),
            "result": loop.create_future(),
            "phase": "requested",
        }
        step_id = self.store.begin_step(run_id, "action", action, proposal)
        finished = False

        def finish_action_step(status, result):
            nonlocal finished
            if not finished:
                self.store.finish_step(step_id, status, result)
                finished = True

        self.action_waiters[proposal_id] = pending
        notify_cancel = False
        try:
            publish("action_requested", proposal)
            async with asyncio.timeout(30):
                decision = await pending["decision"]
                if decision != "approve":
                    result = {"status": "denied", "proposal_id": proposal_id}
                    finish_action_step("cancelled", result)
                    return result
                pending["phase"] = "executing"
                publish("action_execute", proposal)
                response = await pending["result"]
            status = response.get("status") if isinstance(response, dict) else None
            if status not in {"completed", "denied", "stale", "cancelled", "failed"}:
                outcome = {"status": "failed", "error": "invalid_native_action_result"}
                finish_action_step("failed", outcome)
                return outcome
            result = response.get("result", {})
            if not isinstance(result, dict) or len(json.dumps(result, ensure_ascii=False)) > 2_000:
                outcome = {"status": "failed", "error": "invalid_native_action_result"}
                lifecycle_status = "failed"
            else:
                outcome = {**result, "status": status, "proposal_id": proposal_id}
                lifecycle_status = (
                    "completed"
                    if status == "completed"
                    else "failed"
                    if status == "failed"
                    else "cancelled"
                )
            finish_action_step(lifecycle_status, outcome)
            return outcome
        except TimeoutError:
            notify_cancel = True
            outcome = {
                "status": "stale",
                "error": "action_proposal_expired",
                "proposal_id": proposal_id,
            }
            finish_action_step("cancelled", outcome)
            return outcome
        except asyncio.CancelledError:
            notify_cancel = True
            finish_action_step("cancelled", {"status": "cancelled", "proposal_id": proposal_id})
            raise
        except Exception as exc:
            outcome = {
                "status": "failed",
                "error": "action_broker_failed",
                "detail": type(exc).__name__,
                "proposal_id": proposal_id,
            }
            finish_action_step("failed", outcome)
            return outcome
        finally:
            if not finished:
                finish_action_step(
                    "failed",
                    {
                        "status": "failed",
                        "error": "action_broker_ended_without_result",
                        "proposal_id": proposal_id,
                    },
                )
            if self.action_waiters.get(proposal_id) is pending:
                self.action_waiters.pop(proposal_id, None)
                if notify_cancel and not pending.get("cancel_notified"):
                    publish("action_cancelled", {**proposal, "reason": "cancelled_or_expired"})

    async def drain_index(self):
        try:
            while await self.index.drain(25):
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Canonical SQLite and its pending outbox are intact when Ollama is offline.
            pass

    async def warm_speech(self, engine: str | None, voice: str | None = None) -> None:
        try:
            tasks = [self.transcriber.prewarm()]
            if engine == "chatterbox":
                selected = resolve_voice(engine, voice)
                key = (engine, selected)
                synthesizer = self.synthesizers.get(key)
                if synthesizer is None:
                    synthesizer = make_synthesizer(engine, selected)
                    self.synthesizers[key] = synthesizer
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
        self,
        queue: asyncio.Queue,
        conversation_id: str,
        engine: str,
        voice: str,
        epoch: int,
        run_ref: dict,
    ) -> bool:
        try:
            while True:
                sentence = await queue.get()
                if sentence is None:
                    return True
                key = (engine, voice)
                synthesizer = self.synthesizers.get(key)
                if synthesizer is None:
                    synthesizer = make_synthesizer(engine, voice)
                    self.synthesizers[key] = synthesizer
                option = BY_ID[engine]
                reservation_id = None
                if option.provider != "local":
                    await synthesizer.preflight()
                    reservation_id = self.store.reserve(
                        speech_reservation(option, sentence), provider=option.provider
                    )
                try:
                    if reservation_id:
                        self.store.mark_dispatched(reservation_id)
                    audio = await synthesizer.synthesize(sentence)
                    estimate = speech_cost(option, sentence, audio.duration_seconds)
                    if reservation_id:
                        self.store.settle(reservation_id, estimate)
                finally:
                    if reservation_id:
                        self.store.settle(reservation_id)
                if epoch != self.epoch:
                    return False
                run_id = run_ref["id"]
                asset = self.store.save_audio(
                    conversation_id,
                    audio.data,
                    direction="output",
                    extension=audio.extension,
                    duration_seconds=audio.duration_seconds,
                    provider=engine,
                    estimated_usd=estimate,
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
        speech_voice: str | None = None,
        tools: tuple[str, ...] | None = None,
        media_ids: tuple[str, ...] = (),
        vision_route: str | None = None,
        capture_authorized: bool = False,
        operation_id: str = "",
        canvas_interaction: dict | None = None,
    ) -> None:
        operation_id = operation_id or OPERATION.get() or str(uuid.uuid4())
        selected_voice = resolve_voice(speech_engine, speech_voice) if speech_engine else None
        run_ref = {}
        provider = None
        queue = asyncio.Queue()
        speech_task = (
            asyncio.create_task(
                self.speak(queue, conversation_id, speech_engine, selected_voice, epoch, run_ref)
            )
            if speech_engine
            else None
        )
        buffer = ""
        failed = False
        try:
            settings = self.store.settings()
            if tools is None and "tools" in settings:
                tools = tuple(json.loads(settings["tools"]))
            vision_route = vision_route or settings.get("vision_route", "gemini")
            config = AgentConfig(
                system_prompt=PROMPT,
                provider=provider_name,
                model=MODEL_PRESETS[provider_name],
                tools=tools if tools is not None else DEFAULT_TOOLS,
                max_steps=8,
                timeout_seconds=180,
                policy=settings.get("agent_policy", "read_only"),
            )
            provider = make_provider(provider_name, self.store)
            maintenance = (
                FakeMaintenance(self.store)
                if provider_name == "fake"
                else MaintenanceService(self.store)
            )
            agent = AgentRunner(
                config,
                self.store,
                provider,
                registry=self.registry,
                index=self.index,
                maintenance=maintenance,
                capture=self.capture,
                vision_route=vision_route,
                action_broker=self.request_action,
            )
            request = AgentInput(
                conversation_id,
                text,
                media_ids,
                capture_authorized,
                operation_id,
                canvas_interaction=canvas_interaction,
                input_origin="canvas_interaction" if canvas_interaction else "user",
            )
            async with aclosing(agent.stream(request)) as events:
                async for event in events:
                    if epoch != self.epoch:
                        return
                    if event.kind == "delta":
                        self.active_text += event.text
                        self.emit("delta", text=event.text, run_id=event.run_id)
                        buffer += event.text
                        # Keep text streaming, but synthesize the reply as one
                        # utterance. Independent sentence requests outrun playback
                        # and lose prosody; replay hid that by having every clip ready.
                    elif event.kind == "started":
                        self.active_text = ""
                        run_ref["id"] = event.run_id
                        if input_asset:
                            self.store.link_audio(input_asset, event.run_id, "user")
                        self.emit(
                            "started",
                            run_id=event.run_id,
                            conversation_id=conversation_id,
                            user_text=text,
                        )
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
                    elif event.kind in {
                        "context",
                        "memory",
                        "tool_started",
                        "tool_result",
                        "source",
                        "media",
                        "artifact",
                        "artifact_presented",
                        "action_requested",
                        "action_execute",
                        "action_cancelled",
                    }:
                        self.emit(
                            "agent_event",
                            agent_kind=(
                                "artifact_presented" if event.kind == "artifact" else event.kind
                            ),
                            run_id=event.run_id,
                            conversation_id=conversation_id,
                            data=event.data,
                        )
            if speech_task and not failed:
                if buffer.strip():
                    await queue.put(buffer.strip())
                await queue.put(None)
                speech_ok = await speech_task
                failed = failed or not speech_ok
            if run_ref:
                self.store.attach_run_audio(run_ref["id"])
                if self.index_task is None or self.index_task.done():
                    self.index_task = asyncio.create_task(self.drain_index())
            if epoch == self.epoch:
                self.emit("turn_done", conversation_id=conversation_id, failed=failed)
                self.emit("conversations", items=self.store.list_conversations())
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
            self.emit("turn_done", conversation_id=conversation_id, failed=True)
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
        speech_voice: str | None = None,
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
                conversation_id,
                text,
                provider_name,
                speech_engine,
                epoch,
                input_asset["id"],
                speech_voice,
                capture_authorized=clear_capture_request(text),
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
        request_id = request.get("id")
        action = request.get("action")
        operation_id = request.get("operation_id")
        if action in {"send", "recorded", "canvas_interact"} and not operation_id:
            operation_id = request_id or str(uuid.uuid4())
        if operation_id is not None and (
            not isinstance(operation_id, str) or not operation_id or len(operation_id) > 200
        ):
            self.emit("error", id=request_id, error="Invalid operation ID")
            return
        OPERATION.set(operation_id)
        if request.get("v") not in {1, VERSION}:
            self.emit("error", error="Unsupported transport version")
            return
        try:
            if action in {"new", "select", "delete"}:
                await self.stop(announce=False)
            if action == "capture_result":
                waiter = self.capture_waiters.get(request.get("capture_id"))
                if waiter and not waiter.done():
                    if request.get("error"):
                        waiter.set_exception(RuntimeError(str(request["error"])[:300]))
                    else:
                        captured = {"path": request["path"]}
                        target = request.get("target")
                        if target is not None:
                            required = {"bundle_id", "window_id", "window_width", "window_height"}
                            if (
                                not isinstance(target, dict)
                                or not required <= target.keys()
                                or set(target) != required
                            ):
                                raise ValueError("Invalid capture target metadata")
                            bundle_id = target["bundle_id"]
                            window_id = target["window_id"]
                            width, height = target["window_width"], target["window_height"]
                            if (
                                not isinstance(bundle_id, str)
                                or len(bundle_id) > 255
                                or isinstance(window_id, bool)
                                or not isinstance(window_id, int)
                                or not 0 < window_id <= 4_294_967_295
                                or isinstance(width, bool)
                                or not isinstance(width, (int, float))
                                or not 0 < width <= 20_000
                                or isinstance(height, bool)
                                or not isinstance(height, (int, float))
                                or not 0 < height <= 20_000
                            ):
                                raise ValueError("Invalid capture target metadata")
                            captured["target"] = {
                                "bundle_id": bundle_id,
                                "window_id": window_id,
                                "window_width": float(width),
                                "window_height": float(height),
                            }
                        waiter.set_result(captured)
            elif action == "action_decision":
                if set(request) - {
                    "v",
                    "action",
                    "id",
                    "operation_id",
                    "proposal_id",
                    "run_id",
                    "decision",
                }:
                    raise ValueError("Unexpected action decision fields")
                proposal_id = request.get("proposal_id")
                pending = self.action_waiters.get(proposal_id)
                decision = request.get("decision")
                if (
                    pending
                    and pending["phase"] == "requested"
                    and request.get("run_id") == pending["run_id"]
                    and request.get("operation_id") == pending["operation_id"]
                    and decision in {"approve", "deny"}
                    and not pending["decision"].done()
                ):
                    expires = datetime.fromisoformat(pending["expires_at"])
                    if expires <= datetime.now(UTC):
                        pending["decision"].set_result("deny")
                        self.emit(
                            "agent_event",
                            agent_kind="action_cancelled",
                            run_id=pending["run_id"],
                            data={
                                "proposal_id": proposal_id,
                                "operation_id": pending["operation_id"],
                                "reason": "expired",
                            },
                        )
                    else:
                        pending["decision"].set_result(decision)
                else:
                    self.emit("action_response_rejected", id=request_id, proposal_id=proposal_id)
            elif action == "action_result":
                if set(request) - {
                    "v",
                    "action",
                    "id",
                    "operation_id",
                    "proposal_id",
                    "run_id",
                    "status",
                    "result",
                }:
                    raise ValueError("Unexpected action result fields")
                proposal_id = request.get("proposal_id")
                pending = self.action_waiters.get(proposal_id)
                result = request.get("result", {})
                if (
                    pending
                    and pending["phase"] == "executing"
                    and request.get("run_id") == pending["run_id"]
                    and request.get("operation_id") == pending["operation_id"]
                    and request.get("status")
                    in {"completed", "denied", "stale", "cancelled", "failed"}
                    and isinstance(result, dict)
                    and len(json.dumps(result, ensure_ascii=False)) <= 2_000
                    and not pending["result"].done()
                ):
                    pending["result"].set_result({"status": request["status"], "result": result})
                else:
                    self.emit("action_response_rejected", id=request_id, proposal_id=proposal_id)
            elif action == "memory_list":
                self.emit("memory_list", id=request_id, items=self.store.memories())
            elif action == "memory_search":
                mode = request.get("mode", "hybrid")
                self.emit(
                    "memory_search",
                    id=request_id,
                    items=await self.search_memories(request["query"], mode),
                )
            elif action == "memory_delete":
                self.store.forget(request["memory_id"])
                self.emit("memory_deleted", id=request_id, memory_id=request["memory_id"])
            elif action == "run_steps":
                self.emit(
                    "run_steps",
                    id=request_id,
                    items=self.store.steps(request["run_id"]),
                    sources=self.store.sources(request["run_id"]),
                )
            elif action == "artifact_save":
                if request.get("v") != 2:
                    raise ValueError("Artifacts require bridge v2")
                kind = request.get("kind")
                payload = request.get("payload")
                if (
                    not isinstance(kind, str)
                    or not kind
                    or len(kind) > 80
                    or not isinstance(payload, dict)
                    or len(json.dumps(payload)) > 32_000
                ):
                    raise ValueError("Invalid artifact")
                run = self.store.run(request["run_id"])
                artifact = self.store.save_artifact(run["id"], kind, payload, version=1)
                self.emit(
                    "agent_event",
                    agent_kind="artifact",
                    run_id=run["id"],
                    conversation_id=run["conversation_id"],
                    data=artifact,
                )
            elif action == "attach_media":
                if request.get("v") != 2:
                    raise ValueError("Media attachments require bridge v2")
                path = Path(request["path"])
                if not path.is_file() or path.stat().st_size > 120_000_000:
                    raise ValueError("Attachment is missing or too large")
                suffixes = {
                    ".png": ("image", "image/png"),
                    ".jpg": ("image", "image/jpeg"),
                    ".jpeg": ("image", "image/jpeg"),
                    ".mp4": ("video", "video/mp4"),
                    ".mov": ("video", "video/quicktime"),
                    ".wav": ("audio", "audio/wav"),
                    ".mp3": ("audio", "audio/mpeg"),
                    ".m4a": ("audio", "audio/mp4"),
                }
                kind, mime = suffixes[path.suffix.lower()]
                asset = self.store.save_media(
                    request["conversation_id"], path.read_bytes(), kind=kind, mime_type=mime
                )
                self.emit("media_attached", id=request_id, media=asset)
            elif action == "metric":
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
                if engine is not None and engine not in BY_ID:
                    raise ValueError("Unknown speech engine")
                voice = resolve_voice(engine, request.get("voice")) if engine else None
                if self.warm_task is None or self.warm_task.done():
                    self.warm_task = asyncio.create_task(self.warm_speech(engine, voice))
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
                    "conversation",
                    id=request_id,
                    conversation_id=conversation_id,
                    messages=[],
                    items=self.store.list_conversations(),
                )
            elif action == "list":
                self.emit("conversations", id=request_id, items=self.store.list_conversations())
            elif action == "rename":
                item = self.store.rename_conversation(request["conversation_id"], request["title"])
                self.emit("conversation_renamed", id=request_id, item=item)
                self.emit("conversations", items=self.store.list_conversations())
            elif action == "conversation_search":
                self.emit(
                    "conversation_search",
                    id=request_id,
                    items=self.store.search_conversations(request["query"]),
                )
            elif action == "canvas_interact":
                if request.get("v") != 2 or set(request) - {
                    "v",
                    "action",
                    "id",
                    "operation_id",
                    "conversation_id",
                    "artifact_id",
                    "run_id",
                    "block_id",
                    "interaction",
                    "value",
                }:
                    raise ValueError("Invalid canvas interaction request")
                await self.stop(announce=False)
                conversation_id = request["conversation_id"]
                interaction = self.store.resolve_canvas_interaction(
                    conversation_id,
                    request.get("artifact_id", ""),
                    request.get("run_id", ""),
                    request.get("block_id", ""),
                    request.get("interaction", ""),
                    request.get("value", ""),
                )
                settings = self.store.settings()
                provider_name = settings.get("provider", "gemini")
                if provider_name not in MODEL_PRESETS:
                    raise ValueError("Unknown provider")
                self.active = asyncio.create_task(
                    self.run_turn(
                        conversation_id,
                        "Please evaluate my selected lesson answer.",
                        provider_name,
                        settings.get("speech", "chatterbox"),
                        self.epoch,
                        tools=(
                            tuple(json.loads(settings["tools"])) if "tools" in settings else None
                        ),
                        vision_route=settings.get("vision_route", "gemini"),
                        capture_authorized=False,
                        operation_id=operation_id or "",
                        canvas_interaction={
                            key: interaction[key]
                            for key in (
                                "artifact_id",
                                "conversation_id",
                                "run_id",
                                "block_id",
                                "interaction",
                                "value",
                            )
                        },
                    )
                )
                self.emit(
                    "canvas_interaction_started",
                    id=request_id,
                    conversation_id=conversation_id,
                )
            elif action in {"select", "inspect_conversation"}:
                conversation_id = request["conversation_id"]
                latest = self.store.db.execute(
                    "SELECT id,status FROM runs WHERE conversation_id=? "
                    "ORDER BY started_at DESC LIMIT 1",
                    (conversation_id,),
                ).fetchone()
                self.emit(
                    "conversation",
                    id=request_id,
                    conversation_id=conversation_id,
                    messages=self.store.messages(conversation_id),
                    audio=self.store.audio_assets(conversation_id),
                    media=self.store.media_assets(conversation_id),
                    active_run=latest["id"] if latest and latest["status"] == "running" else None,
                    active_text=self.active_text
                    if latest and latest["status"] == "running"
                    else "",
                    steps=self.store.steps(latest["id"]) if latest else [],
                    sources=self.store.sources(latest["id"]) if latest else [],
                    artifacts=self.store.canvas_artifacts(conversation_id),
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
                if key == "tools" and request.get("v") == 2:
                    chosen = json.loads(value)
                    if not isinstance(chosen, list):
                        raise ValueError("Tools must be a list")
                    self.registry.specs(tuple(chosen))
                elif key.startswith("voice:"):
                    resolve_voice(key.removeprefix("voice:"), value)
                else:
                    allowed = {
                        "provider": set(MODEL_PRESETS),
                        "speech": set(BY_ID),
                        "mode": {"standard", "live"},
                        "vision_route": set(VISION_MODELS),
                        "agent_policy": {"read_only", "approval_required"},
                    }
                    if key not in allowed or value not in allowed[key]:
                        raise ValueError("Unsupported setting")
                await self.stop()
                if key == "speech" or key.startswith("voice:"):
                    if self.warm_task and not self.warm_task.done():
                        self.warm_task.cancel()
                        await asyncio.gather(self.warm_task, return_exceptions=True)
                    for cache_key, synthesizer in list(self.synthesizers.items()):
                        if hasattr(synthesizer, "close"):
                            await synthesizer.close()
                        del self.synthesizers[cache_key]
                self.store.set_setting(key, value)
                self.emit("settings_saved", id=request_id)
            elif action == "stop":
                await self.stop()
            elif action in ("send", "recorded"):
                await self.stop(announce=False)
                provider_name = request.get("provider", "gemini")
                if provider_name not in MODEL_PRESETS:
                    raise ValueError("Unknown provider")
                speech_engine = request.get("speech", "chatterbox")
                if speech_engine is not None and speech_engine not in BY_ID:
                    raise ValueError("Unknown speech engine")
                speech_voice = (
                    resolve_voice(speech_engine, request.get("voice")) if speech_engine else None
                )
                conversation_id = request["conversation_id"]
                enabled_tools = request.get("tools")
                if enabled_tools is not None:
                    if request.get("v") != 2 or not isinstance(enabled_tools, list):
                        raise ValueError("Tool selection requires bridge v2")
                    self.registry.specs(tuple(enabled_tools))
                media_ids = tuple(request.get("media_ids", []))
                if len(media_ids) > 4 or not all(isinstance(value, str) for value in media_ids):
                    raise ValueError("Attach at most four media files per turn")
                for media_id in media_ids:
                    if self.store.media_asset(media_id)["conversation_id"] != conversation_id:
                        raise ValueError("Attachment is outside this conversation")
                vision_route = request.get("vision_route") or self.store.settings().get(
                    "vision_route", "gemini"
                )
                if vision_route not in VISION_MODELS:
                    raise ValueError("Unknown vision route")
                with self.store.transaction():
                    self.store.set_setting("vision_route", vision_route)
                    self.store.set_setting("provider", provider_name)
                    if enabled_tools is not None:
                        self.store.set_setting("tools", json.dumps(enabled_tools))
                clear_capture = clear_capture_request(request.get("text", ""))
                capture_authorized = clear_capture and bool(
                    request.get("capture_authorized", clear_capture)
                )
                if action == "send":
                    self.active = asyncio.create_task(
                        self.run_turn(
                            conversation_id,
                            request["text"],
                            provider_name,
                            speech_engine,
                            self.epoch,
                            speech_voice=speech_voice,
                            tools=tuple(enabled_tools) if enabled_tools is not None else None,
                            media_ids=media_ids,
                            vision_route=vision_route,
                            capture_authorized=capture_authorized,
                            operation_id=operation_id or "",
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
                                speech_voice,
                            )
                        )
            else:
                raise ValueError("Unknown action")
        except Exception as exc:
            self.emit("error", id=request_id, action=action, error=safe_error(exc))


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
        speech_options=selectable_options(),
        settings=bridge.store.settings(),
    )
    bridge.index_task = asyncio.create_task(bridge.drain_index())
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                await bridge.handle(json.loads(line))
            except (ValueError, TypeError) as exc:
                bridge.emit("error", error=f"Invalid request: {exc}")
    finally:
        await bridge.stop()
        if bridge.index_task and not bridge.index_task.done():
            bridge.index_task.cancel()
            await asyncio.gather(bridge.index_task, return_exceptions=True)
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
