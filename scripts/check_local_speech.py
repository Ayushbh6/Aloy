"""Explicit offline model comparison through production speech adapters.

Manifest: [{"path": "/absolute/audio.wav", "expected": "verified transcript"}].
Use expected="" for silence controls. Results and audition files remain local.
"""

import argparse
import asyncio
import json
import re
import resource
import time
from pathlib import Path

from aloy.speech import ChatterboxSynthesizer, MLXTranscriber


def word_errors(reference, hypothesis):
    def words(text):
        return re.findall(r"\w+", text.casefold())

    expected, actual = words(reference), words(hypothesis)
    row = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        previous, row = row, [i]
        for j, right in enumerate(actual, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
    return {"edits": row[-1], "reference_words": len(expected)}


async def run(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"asr": [], "tts": [], "paid_calls": 0}
    if args.manifest:
        transcriber = MLXTranscriber()
        try:
            for item in json.loads(args.manifest.read_text()):
                started = time.monotonic()
                text = await transcriber.transcribe(Path(item["path"]))
                evidence["asr"].append(
                    {
                        "text": text,
                        "seconds": time.monotonic() - started,
                        **word_errors(item["expected"], text),
                    }
                )
        finally:
            await transcriber.close()
    if args.tts:
        synth = ChatterboxSynthesizer()
        try:
            for language, text in [
                ("en", "Hey Ayush. Good to hear from you. How was your day?"),
                ("de", "Hallo Ayush. Schön, dass du da bist. Wie war dein Tag?"),
            ]:
                started = time.monotonic()
                audio = await synth.synthesize(text)
                path = output / f"chatterbox-{language}.wav"
                path.write_bytes(audio)
                evidence["tts"].append({"file": path.name, "seconds": time.monotonic() - started})
        finally:
            await synth.close()
    evidence["child_peak_rss_platform_units"] = resource.getrusage(
        resource.RUSAGE_CHILDREN
    ).ru_maxrss
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--tts", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(run(parser.parse_args()))
