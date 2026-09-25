"""One bounded speech request through Aloy's normal sentence/playback pipeline."""

import argparse
import asyncio
import io
import json
import os
import tempfile
import time
import wave

from aloy.bridge import PROMPT, Bridge
from aloy.dispatch import DispatchBudget
from aloy.providers import load_local_env
from aloy.speech import SpeechAudio, make_synthesizer
from aloy.speech_catalog import BY_ID, resolve_voice


class FakeSpeech:
    def __init__(self) -> None:
        self.dispatch = DispatchBudget(limit=1)

    async def preflight(self) -> None:
        pass

    async def synthesize(self, text: str) -> SpeechAudio:
        self.dispatch.consume()
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\0\0" * 1600)
        return SpeechAudio(output.getvalue(), "wav", 0.1)

    async def close(self) -> None:
        pass


async def run(option_id: str, mode: str, voice: str | None = None) -> dict:
    load_local_env()
    option = BY_ID[option_id]
    voice = resolve_voice(option_id, voice)
    temporary = tempfile.TemporaryDirectory(prefix="aloy-speech-fake-") if mode == "fake" else None
    previous_root = os.environ.get("ALOY_DATA_DIR")
    if temporary:
        os.environ["ALOY_DATA_DIR"] = temporary.name
    bridge = None
    conversation_id = None
    started = time.monotonic()
    events = []
    synthesizer = None
    try:
        bridge = Bridge()
        bridge.emit = lambda kind, **fields: events.append((kind, fields.get("error")))
        conversation_id = bridge.store.create_conversation(PROMPT, "Synthetic speech check")
        synthesizer = FakeSpeech() if mode == "fake" else make_synthesizer(option_id, voice)
        synthesizer.dispatch = DispatchBudget(limit=1)
        bridge.synthesizers[(option_id, voice)] = synthesizer
        await bridge.run_turn(
            conversation_id,
            "Synthetic greeting",
            "fake",
            option_id,
            bridge.epoch,
            speech_voice=voice,
        )
        assets = bridge.store.audio_assets(conversation_id)
        kinds = [kind for kind, _ in events]
        passed = len(assets) == 1 and "speech_error" not in kinds and "audio" in kinds
        return {
            "mode": mode,
            "option": option_id,
            "voice": voice,
            "model": option.model,
            "result": "passed" if passed else "failed",
            "dispatches": synthesizer.dispatch.count,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "estimated_usd": assets[0]["estimated_usd"] if assets else None,
            "audio_format": assets[0]["format"] if assets else None,
            "error": next(
                (detail for kind, detail in events if kind in {"speech_error", "error"}), None
            ),
        }
    except Exception as exc:
        return {
            "mode": mode,
            "option": option_id,
            "voice": voice,
            "model": option.model,
            "result": "failed",
            "dispatches": synthesizer.dispatch.count if synthesizer else 0,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "error_type": type(exc).__name__,
        }
    finally:
        if bridge:
            if synthesizer:
                await synthesizer.close()
            if conversation_id:
                bridge.store.delete_conversation(conversation_id)
            bridge.store.close()
        if temporary:
            temporary.cleanup()
        if previous_root is None:
            os.environ.pop("ALOY_DATA_DIR", None)
        else:
            os.environ["ALOY_DATA_DIR"] = previous_root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--option", choices=tuple(BY_ID))
    parser.add_argument("--voice", help="Voice ID offered by this speech option")
    args = parser.parse_args()
    if args.option is None:
        args.option = "gemini-lite"
    if args.mode == "live" and BY_ID[args.option].provider == "local":
        parser.error("Live mode requires a paid speech option")
    evidence = asyncio.run(run(args.option, args.mode, args.voice))
    print(json.dumps(evidence, sort_keys=True))
    if evidence["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
