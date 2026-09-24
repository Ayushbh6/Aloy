"""Explicit, single-turn integration checks through the production agent."""

import argparse
import asyncio
import json
import time

from aloy import AgentConfig, ChatAgent, ConversationStore
from aloy.dispatch import DispatchBudget
from aloy.providers import MODEL_PRESETS, load_local_env, make_provider


async def _run(provider_name: str) -> dict:
    load_local_env()
    if provider_name not in MODEL_PRESETS:
        raise ValueError(f"Unknown provider: {provider_name}")
    store = ConversationStore()
    config = AgentConfig(
        provider=provider_name,
        model=MODEL_PRESETS[provider_name],
        system_prompt="Reply with one short sentence. No tools.",
        max_output_tokens=256,
        timeout_seconds=45,
    )
    conversation_id = store.create_conversation(config.system_prompt, "Synthetic smoke check")
    start = time.monotonic()
    dispatch = DispatchBudget(limit=1)
    provider = None
    try:
        provider = make_provider(provider_name, store, dispatch=dispatch)
        reply = await ChatAgent(config, store, provider).reply(
            conversation_id, "Say hello in German in one short sentence."
        )
        evidence = {
            "provider": provider_name,
            "model": config.model,
            "result": "passed" if reply.text.strip() else "failed",
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "dispatches": dispatch.count if provider_name != "codex" else "provider-managed turn",
            "input_tokens": reply.usage.input_tokens,
            "output_tokens": reply.usage.output_tokens,
            "estimated_usd": reply.usage.estimated_usd,
        }
    except Exception as exc:
        evidence = {
            "provider": provider_name,
            "model": config.model,
            "result": "failed",
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "error_type": type(exc).__name__,
            "dispatches": dispatch.count,
        }
    finally:
        if provider and hasattr(provider, "close"):
            await provider.close()
        store.delete_conversation(conversation_id)
        store.close()
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fake", "live"], default="fake")
    parser.add_argument("--provider", choices=list(MODEL_PRESETS), default="fake")
    args = parser.parse_args()
    if args.mode == "fake" and args.provider != "fake":
        parser.error("Fake mode only accepts the fake provider")
    if args.mode == "live" and args.provider == "fake":
        parser.error("Live mode requires a real provider")
    evidence = asyncio.run(_run(args.provider))
    print(json.dumps(evidence, sort_keys=True))
    if evidence["result"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
