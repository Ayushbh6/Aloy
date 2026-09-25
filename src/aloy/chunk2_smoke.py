"""Bounded synthetic canvas generation through the production agent; offline by default."""

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path

from aloy.agent import AgentRunner, FakeProvider
from aloy.contracts import AgentConfig, AgentInput, ProviderStepResult, ToolCall
from aloy.dispatch import DispatchBudget
from aloy.errors import safe_error
from aloy.providers import MODEL_PRESETS, load_local_env, make_provider
from aloy.storage import ConversationStore

EXAMPLE = {
    "title": "German word order",
    "blocks": [
        {
            "id": "sentence-1",
            "kind": "sentence",
            "source": "Heute lerne ich Deutsch.",
            "target": "Ich lerne heute Deutsch.",
            "explanation": "The verb stays in second position.",
        },
        {
            "id": "choice-1",
            "kind": "choice",
            "prompt": "Which word is the verb?",
            "options": [{"id": "lerne", "label": "lerne"}, {"id": "heute", "label": "Heute"}],
        },
    ],
}


async def check(*, live: bool = False, route: str = "gemini") -> dict:
    if route not in {"gemini", "openrouter"}:
        raise ValueError("This bounded check supports direct Gemini or OpenRouter only")
    started = time.monotonic()
    ledger = None
    reservation = None
    provider = None
    guard = DispatchBudget(limit=2)
    with tempfile.TemporaryDirectory(prefix="aloy-chunk2-") as folder:
        store = ConversationStore(Path(folder) / "synthetic")
        try:
            name = route if live else "fake"
            config = AgentConfig(
                provider=name,
                model=MODEL_PRESETS[name],
                max_steps=2,
                max_output_tokens=2048,
                timeout_seconds=60,
                tools=("canvas.present",),
            )
            cid = store.create_conversation(config.system_prompt)
            if live:
                load_local_env()
                provider = make_provider(name, store, dispatch=guard)
                ledger = ConversationStore()
                reservation = ledger.reserve(0.10, provider=route)
                ledger.mark_dispatched(reservation)
            else:
                provider = FakeProvider(
                    script=[
                        ProviderStepResult(
                            calls=(ToolCall("canvas-test", "canvas.present", EXAMPLE),)
                        ),
                        ProviderStepResult(text="The verb stays in second position."),
                    ]
                )
            prompt = (
                "Make a synthetic desktop teaching canvas. Call canvas.present exactly once "
                "with the following arguments, then give one short sentence. Do not call another "
                "tool or produce another artifact. Arguments: " + json.dumps(EXAMPLE)
            )
            async with asyncio.timeout(65):
                events = [
                    e
                    async for e in AgentRunner(config, store, provider).stream(
                        AgentInput(cid, prompt)
                    )
                ]
            failure = next((e.error for e in events if e.kind == "failed"), None)
            if failure or not any(e.kind == "completed" for e in events):
                # Synthetic-only diagnostics: never export prompts or private app state.
                diagnostics = [
                    {
                        "name": e.data["name"],
                        "success": e.data["success"],
                        "error": e.data["result"].get("error"),
                        "message": e.data["result"].get("message"),
                    }
                    for e in events
                    if e.kind == "tool_result"
                ]
                raise AssertionError(
                    (failure or "No completed run") + " " + json.dumps(diagnostics)
                )
            results = [e for e in events if e.kind == "tool_result"]
            assert len(results) == 1 and results[0].data["success"], (
                "Canvas tool did not succeed once"
            )
            artifacts = [e for e in events if e.kind == "artifact"]
            assert len(artifacts) == 1, "Missing canonical artifact event"
            assert store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
            calls = [
                dict(row)
                for row in store.db.execute(
                    "SELECT purpose,provider,model,status,input_tokens,output_tokens,estimated_usd "
                    "FROM provider_calls ORDER BY rowid"
                )
            ]
            assert len(calls) == 2 and all(c["status"] == "completed" for c in calls)
            if live:
                assert guard.count == 2, "Expected one tool call turn and one continuation"
            return {
                "mode": "live" if live else "fake",
                "provider": name,
                "result": "passed",
                "seconds": round(time.monotonic() - started, 2),
                "dispatches": guard.count,
                "artifacts": len(artifacts),
                "calls": calls,
                "estimated_usd": store.monthly_spend(),
            }
        finally:
            try:
                if provider and hasattr(provider, "close"):
                    await provider.close()
            finally:
                if ledger:
                    if reservation:
                        # Unknown dispatched calls remain conservatively charged by the runner.
                        ledger.settle(reservation, store.monthly_spend())
                    ledger.close()
                store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--provider", choices=("gemini", "openrouter"), default="gemini")
    args = parser.parse_args()
    try:
        result = asyncio.run(check(live=args.mode == "live", route=args.provider))
    except Exception as exc:
        print(json.dumps({"result": "failed", "error": safe_error(exc)}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
