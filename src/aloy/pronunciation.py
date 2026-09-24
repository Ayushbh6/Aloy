"""Experimental audio-grounded German pronunciation feedback."""

import argparse
import asyncio
import base64
import os
from pathlib import Path

from aloy.budget import estimated_cost
from aloy.contracts import Usage
from aloy.dispatch import DispatchBudget, gemini_client
from aloy.lifecycle import RunLifecycle
from aloy.live import _pcm_from_wav
from aloy.providers import load_local_env
from aloy.storage import ConversationStore


async def assess(audio: bytes, expected: str, store: ConversationStore) -> str:
    """Send the actual recording; a transcript alone cannot establish pronunciation."""
    if not audio or len(audio) > 2_000_000 or not expected.strip():
        raise ValueError("Provide a short recording and its expected German phrase")
    _, seconds = _pcm_from_wav(audio)
    if seconds > 30:
        raise ValueError("Pronunciation recordings must be at most 30 seconds")
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY is missing")
    client = gemini_client(key)
    dispatch = DispatchBudget(limit=1)
    prompt = (
        f"Listen to the actual German speech. Expected phrase: {expected!r}. "
        "Use at most three plain sentences, no Markdown. "
        "Give at most one specific audible pronunciation issue and one practical correction. "
        "Consider vowel length, umlauts and the ich sound where relevant. "
        "If no issue is clearly audible, say so. Do not infer pronunciation from a transcript. "
        "Do not assign a numerical score."
    )
    conversation_id = store.create_conversation(prompt, "Pronunciation experiment")
    try:
        with RunLifecycle(
            store, conversation_id, "gemini-audio", "gemini-3.8-flash", expected, 0.02
        ) as run:
            async with asyncio.timeout(45):
                store.save_audio(
                    conversation_id,
                    audio,
                    direction="input",
                    extension="wav",
                    run_id=run.run_id,
                    message_id=store.message_id(run.run_id, "user"),
                    duration_seconds=seconds,
                )
                run.dispatched()
                dispatch.consume()
                interaction = await client.aio.interactions.create(
                    model="gemini-3.8-flash",
                    input=[
                        {"type": "text", "text": prompt},
                        {
                            "type": "audio",
                            "data": base64.b64encode(audio).decode(),
                            "mime_type": "audio/wav",
                        },
                    ],
                    generation_config={"max_output_tokens": 1024, "thinking_level": "low"},
                )
                if not interaction.output_text:
                    raise RuntimeError("Pronunciation model returned no feedback")
                usage = interaction.usage
                input_tokens = usage.total_input_tokens if usage else None
                output_tokens = usage.total_output_tokens if usage else None
                estimate = estimated_cost("gemini-3.8-flash", Usage(input_tokens, output_tokens))
                answer = interaction.output_text.strip()
                run.complete(answer, Usage(input_tokens, output_tokens, estimate))
                return answer
    finally:
        await client.aio.aclose()
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("expected")
    args = parser.parse_args()
    load_local_env()
    if args.audio.suffix.lower() != ".wav":
        parser.error("Use a mono 16 kHz WAV file")
    store = ConversationStore()
    try:
        print(asyncio.run(assess(args.audio.read_bytes(), args.expected, store)))
    finally:
        store.close()


if __name__ == "__main__":
    main()
