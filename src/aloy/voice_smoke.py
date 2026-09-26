"""Bounded synthetic speech checks: two dispatches per explicitly selected live scenario."""

import argparse
import asyncio
import base64
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from aloy.agent import FakeProvider
from aloy.bridge import PROMPT, Bridge
from aloy.contracts import AgentConfig, ProviderStepResult
from aloy.dispatch import DispatchBudget
from aloy.maintenance import FakeMaintenance
from aloy.providers import load_local_env, make_provider
from aloy.speech import SpeechAudio, make_synthesizer
from aloy.storage import ConversationStore
from aloy.voice_session import pcm_wav

TEXT = (
    "Guten Tag. Heute üben wir einen kurzen deutschen Satz. "
    "Ich gehe am Samstag mit einem Freund in den Park. "
    "Danach trinken wir einen Kaffee und sprechen über unsere Pläne für die nächste Woche."
)


class SyntheticSpeech:
    def __init__(self):
        self.dispatch = DispatchBudget(1)

    async def preflight(self):
        pass

    async def synthesize(self, text):
        self.dispatch.consume()
        await asyncio.sleep(0.02)
        return SpeechAudio(pcm_wav(b"\0\0" * 7200, 24000), "wav", 0.3)

    async def stream(self, text):
        self.dispatch.consume()
        for _ in range(3):
            await asyncio.sleep(0.005)
            yield b"\0\0" * 2400

    async def close(self):
        pass


class WholeFile:
    """Exercise the existing unary adapter through the same speech pipeline."""

    def __init__(self, speech):
        self.speech = speech

    async def preflight(self):
        await self.speech.preflight()

    async def synthesize(self, text):
        return await self.speech.synthesize(text)


async def run(mode="fake", scenario="compare", output: Path | None = None):
    load_local_env()
    ledger = ConversationStore() if mode == "live" else None
    reservation = ledger.reserve(0.25, provider="gemini") if ledger else None
    results, total_dispatches = [], 0
    with tempfile.TemporaryDirectory(prefix="aloy-voice-synthetic-") as folder:
        with patch.dict(os.environ, {"ALOY_DATA_DIR": folder}):
            bridge = Bridge()
        bridge.index = None  # Synthetic store only; never retrieve personal history.
        cid = bridge.store.create_conversation(PROMPT, "Synthetic voice check")
        if output:
            output.mkdir(parents=True, exist_ok=True)
        try:
            if ledger:
                ledger.mark_dispatched(reservation)
            for route in ["whole_file", "stream"] if scenario == "compare" else ["pipeline"]:
                speech = (
                    make_synthesizer("gemini-lite", "Achird")
                    if mode == "live"
                    else SyntheticSpeech()
                )
                speech.dispatch = DispatchBudget(1)
                bridge.synthesizers[("gemini-lite", "Achird")] = (
                    WholeFile(speech) if route == "whole_file" else speech
                )
                events = []
                stream_pcm = bytearray()
                began = time.monotonic()

                def emit(kind, _events=events, _began=began, _pcm=stream_pcm, **fields):
                    _events.append(
                        {
                            "event": kind,
                            "seconds": time.monotonic() - _began,
                            **({"error": fields["error"]} if "error" in fields else {}),
                        }
                    )
                    if kind == "audio_chunk":
                        _pcm.extend(base64.b64decode(fields["pcm"]))
                        _events[-1]["bytes"] = len(base64.b64decode(fields["pcm"]))

                bridge.emit = emit
                provider = None
                try:
                    async with asyncio.timeout(65):
                        if route == "pipeline":
                            provider = (
                                make_provider("gemini-quality", bridge.store)
                                if mode == "live"
                                else FakeProvider(script=[ProviderStepResult(text=TEXT)])
                            )
                            provider.dispatch = DispatchBudget(1)

                            def config(**kwargs):
                                return AgentConfig(
                                    **{
                                        **kwargs,
                                        "tools": (),
                                        "max_steps": 1,
                                        "max_output_tokens": 1024,
                                    }
                                )

                            with (
                                patch("aloy.bridge.make_provider", return_value=provider),
                                patch("aloy.bridge.MaintenanceService", FakeMaintenance),
                                patch("aloy.bridge.AgentConfig", side_effect=config),
                            ):
                                await bridge.run_turn(
                                    cid,
                                    "Say one short German sentence about visiting a park.",
                                    "gemini-quality" if mode == "live" else "fake",
                                    "gemini-lite",
                                    bridge.epoch,
                                    speech_voice="Achird",
                                )
                        else:
                            queue = asyncio.Queue()
                            queue.put_nowait(TEXT)
                            queue.put_nowait(None)
                            await bridge.speak(
                                queue, cid, "gemini-lite", "Achird", bridge.epoch, {"id": None}
                            )
                    if provider:
                        total_dispatches += provider.dispatch.count
                    total_dispatches += speech.dispatch.count
                    failure = next(
                        (e.get("error") for e in events if e["event"] in {"error", "speech_error"}),
                        None,
                    )
                    if failure or not any(e["event"] == "audio" for e in events):
                        raise RuntimeError(failure or "No saved audio")
                    first = next(
                        e["seconds"] for e in events if e["event"] in {"audio_chunk", "audio"}
                    )
                    saved = next(e["seconds"] for e in events if e["event"] == "audio")
                    if route != "whole_file" and first >= saved:
                        raise AssertionError("Streaming failed to deliver before completion")
                    result = {
                        "route": route,
                        "first_audio_s": first,
                        "saved_audio_s": saved,
                        "events": events,
                    }
                    results.append(result)
                    if output:
                        (output / f"{scenario}-{route}.pcm").write_bytes(stream_pcm)
                        assets = bridge.store.audio_assets(cid)
                        (output / f"{scenario}-{route}.wav").write_bytes(
                            Path(assets[-1]["path"]).read_bytes()
                        )
                        (output / f"{scenario}-{route}.json").write_text(
                            json.dumps(result, indent=2)
                        )
                finally:
                    await speech.close()
            if mode == "live" and total_dispatches != 2:
                raise AssertionError("Live scenario must have exactly two generation dispatches")
            return {
                "mode": mode,
                "scenario": scenario,
                "result": "passed",
                "dispatches": total_dispatches,
                "estimated_usd": bridge.store.monthly_spend(),
                "measurements": results,
            }
        finally:
            await bridge.stop(False)
            if bridge.index_task:
                await bridge.index_task
            if ledger:
                ledger.settle(reservation, bridge.store.monthly_spend())
                ledger.close()
            bridge.store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--scenario", choices=("compare", "pipeline"), default="compare")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.mode, args.scenario, args.output)), indent=2))


if __name__ == "__main__":
    main()
