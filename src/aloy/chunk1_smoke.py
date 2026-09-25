"""Explicit bounded Chunk 1 checks; all payloads are synthetic and isolated."""

import argparse
import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from aloy.agent import AgentRunner, FakeProvider
from aloy.contracts import AgentConfig, AgentInput, ProviderStepResult, ToolCall
from aloy.dispatch import DispatchBudget
from aloy.maintenance import MaintenanceService
from aloy.providers import MODEL_PRESETS, load_local_env, make_provider
from aloy.storage import ConversationStore

SCENARIOS = (
    "tools-gemini",
    "tools-openrouter",
    "tools-codex",
    "maintenance",
    "web",
    "image-gemini",
    "video-gemini",
    "image-openrouter",
    "video-openrouter",
    "image-codex",
)


def fixture(folder: Path, video: bool) -> tuple[bytes, str, str]:
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    target = folder / ("synthetic.mp4" if video else "synthetic.png")
    command = [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=96x64:r=10:d=1",
    ]
    if video:
        command += [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=96x64:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-t",
            "2",
        ]
    else:
        command += ["-frames:v", "1", "-threads", "1"]
    subprocess.run([*command, str(target)], check=True, capture_output=True, timeout=15)
    return target.read_bytes(), "video" if video else "image", "video/mp4" if video else "image/png"


async def check(scenario: str, live: bool = False) -> dict:
    start = time.monotonic()
    load_local_env()
    charged_provider = (
        "openrouter" if scenario == "maintenance" or scenario.endswith("openrouter") else "gemini"
    )
    if scenario.endswith("codex"):
        charged_provider = "codex"
    ledger, reservation, provider = None, None, None
    if live:
        ledger = ConversationStore()
        reservation = ledger.reserve(
            0 if charged_provider == "codex" else 0.5, provider=charged_provider
        )
        ledger.mark_dispatched(reservation)
    with tempfile.TemporaryDirectory(prefix="aloy-chunk1-") as directory:
        store = ConversationStore(Path(directory) / "data")
        guard = DispatchBudget(limit=2)
        try:
            async with asyncio.timeout(150):
                route = scenario.rsplit("-", 1)[-1]
                name = route if live and scenario.startswith("tools-") else "fake"
                if scenario == "image-codex" and live:
                    name = "codex"
                config = AgentConfig(
                    provider=name,
                    model=MODEL_PRESETS[name],
                    max_steps=2,
                    max_output_tokens=1024,
                    timeout_seconds=140,
                    tools=("memory.search",),
                )
                cid = store.create_conversation(config.system_prompt)
                prompt = (
                    "Call memory.search exactly once for 'synthetic apricot', "
                    "then report the result in one sentence."
                )
                store.remember("The synthetic apricot code is 427.", "fact")
                provider = (
                    make_provider(name, store, dispatch=guard)
                    if name != "fake"
                    else FakeProvider(
                        script=[
                            ProviderStepResult(
                                calls=(
                                    ToolCall(
                                        "test", "memory.search", {"query": "synthetic apricot"}
                                    ),
                                )
                            ),
                            ProviderStepResult(text="The synthetic code is 427."),
                        ]
                    )
                )
                media_ids = ()
                if scenario == "maintenance":
                    if not live:
                        from aloy.maintenance import FakeMaintenance

                        maintenance = FakeMaintenance(store)
                    else:
                        maintenance = MaintenanceService(store)
                    run = store.begin_run(
                        cid, "fake", "fake-v1", "My favourite test fruit is apricot."
                    )
                    memories = await maintenance.extract(
                        run,
                        cid,
                        "My favourite test fruit is apricot.",
                        store.message_id(run, "user"),
                    )
                    if live and not memories:
                        raise AssertionError("Maintenance extracted no stable synthetic fact")
                    store.finish_run(run, "completed", "Understood.")
                    summary = await maintenance.compact(run, cid, store.completed_messages(cid))
                    assert summary["text"]
                    events = []
                else:
                    if scenario == "web":
                        if not live:
                            raise ValueError("Use the tools scenario for the offline harness")
                        config = AgentConfig(
                            provider="fake",
                            model="fake-v1",
                            max_steps=2,
                            timeout_seconds=120,
                            tools=("web.search",),
                        )
                        provider = FakeProvider(
                            script=[
                                ProviderStepResult(
                                    calls=(
                                        ToolCall(
                                            "search",
                                            "web.search",
                                            {"query": "Python official documentation pathlib"},
                                        ),
                                    )
                                ),
                                ProviderStepResult(text="Search complete."),
                            ]
                        )
                        prompt = "Search Python's official pathlib documentation."
                    elif scenario.startswith(("image-", "video-")):
                        if not live:
                            raise ValueError("Media live checks must be explicitly selected")
                        data, kind, mime = fixture(Path(directory), scenario.startswith("video-"))
                        asset = store.save_media(
                            cid,
                            data,
                            kind=kind,
                            mime_type=mime,
                            duration_seconds=2 if kind == "video" else None,
                        )
                        media_ids = (asset["id"],)
                        config = AgentConfig(
                            provider=name,
                            model=MODEL_PRESETS[name],
                            max_steps=2,
                            timeout_seconds=120,
                            tools=("media.inspect",),
                            max_output_tokens=1024,
                        )
                        if name == "fake":
                            provider = FakeProvider("Inspection complete.")
                        prompt = (
                            "Describe the colors, their changes, and audible sounds, "
                            "with timestamps."
                        )
                    runner = AgentRunner(
                        config,
                        store,
                        provider,
                        vision_route=route if route in {"gemini", "openrouter"} else "gemini",
                    )
                    events = [
                        event async for event in runner.stream(AgentInput(cid, prompt, media_ids))
                    ]
                    if not any(event.kind == "completed" for event in events):
                        raise AssertionError(
                            next((e.error for e in events if e.kind == "failed"), "No completion")
                        )
                    results = [event.data for event in events if event.kind == "tool_result"]
                    if scenario != "image-codex":
                        assert results and all(result["success"] for result in results), results
                    if scenario == "web":
                        assert results[0]["result"]["sources"]
                    if scenario.startswith(("image-", "video-")) and scenario != "image-codex":
                        assert results[0]["result"]["observations"]
                calls = [
                    dict(row)
                    for row in store.db.execute(
                        "SELECT purpose,provider,model,status,input_tokens,output_tokens,"
                        "estimated_usd FROM provider_calls"
                    )
                ]
                return {
                    "scenario": scenario,
                    "mode": "live" if live else "fake",
                    "result": "passed",
                    "seconds": round(time.monotonic() - start, 2),
                    "calls": calls,
                    "dispatches": (
                        sum(call["provider"] != "fake" for call in calls)
                        if name != "codex"
                        else "provider-managed turn"
                    ),
                    "tool_results": sum(e.kind == "tool_result" for e in events),
                    "estimated_usd": None if charged_provider == "codex" else store.monthly_spend(),
                }
        finally:
            if provider and hasattr(provider, "close"):
                await provider.close()
            if ledger:
                ledger.settle(reservation, store.monthly_spend())
                ledger.close()
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--scenario", choices=SCENARIOS, default="tools-gemini")
    args = parser.parse_args()
    try:
        result = asyncio.run(check(args.scenario, args.mode == "live"))
    except Exception as exc:
        from aloy.errors import safe_error

        print(json.dumps({"scenario": args.scenario, "result": "failed", "error": safe_error(exc)}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
