"""Bounded synthetic harness checks. Live mode is explicit; private history is never loaded."""

import argparse
import asyncio
import json
import shlex
import shutil
import tempfile
from pathlib import Path

from aloy.agent import AgentRunner, FakeProvider
from aloy.contracts import AgentConfig, AgentInput, ProviderStepResult, ToolCall
from aloy.dispatch import DispatchBudget
from aloy.maintenance import MaintenanceService
from aloy.providers import MODEL_PRESETS, load_local_env, make_provider
from aloy.storage import ConversationStore


def make_pdf(path):
    """Tiny deterministic PDF; visual answers are shapes, not printed colour labels."""
    pages = [
        b"BT /F1 24 Tf 50 740 Td (German practice - page one) Tj ET\n"
        b"1 0 0 rg 80 420 180 180 re f\n",
        b"BT /F1 24 Tf 50 740 Td (German practice - page two) Tj ET\n"
        b"0 0 1 rg 80 420 m 260 420 l 170 610 l h f\n",
    ]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
    ]
    for i, stream in enumerate(pages):
        objects += [
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 7 0 R >> >> /Contents {4 + 2 * i} 0 R >>"
            ).encode(),
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
        ]
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(data)


async def run(mode="fake", scenario="pdf", provider_name="gemini", output=None):
    load_local_env()
    ledger = ConversationStore() if mode == "live" else None
    reservation = (
        ledger.reserve(0.25, provider="openrouter" if scenario == "checkpoint" else provider_name)
        if ledger
        else None
    )
    report = {
        "mode": mode,
        "scenario": scenario,
        "provider": "openrouter" if scenario == "checkpoint" else provider_name,
    }
    with tempfile.TemporaryDirectory(prefix="aloy-harness-synthetic-") as temporary:
        root = Path(temporary)
        store = ConversationStore(root / "state")
        provider = None
        registry = None
        try:
            if ledger:
                ledger.mark_dispatched(reservation)
            if scenario == "checkpoint":
                from aloy.harness_state import compact
                from aloy.maintenance import FakeMaintenance

                cid = store.create_conversation("Synthetic compaction")
                rid = store.begin_run(
                    cid,
                    "fake",
                    "fake-v1",
                    "Ask one question at a time. Explain accusative articles next.",
                )
                store.finish_run(rid, "completed", "We will practise that next.")
                maintenance = (
                    MaintenanceService(store) if mode == "live" else FakeMaintenance(store)
                )
                # _generate performs one SDK dispatch without retries; no fallback provider.
                await compact(store, maintenance, rid, cid, store.completed_messages(cid))
                checkpoint = dict(store.db.execute("SELECT * FROM harness_checkpoints").fetchone())
                if checkpoint["status"] != "complete":
                    raise AssertionError(
                        "Live checkpoint generation failed; fallback is not provider verification"
                    )
                report.update(
                    status="passed",
                    checkpoint=json.loads(checkpoint["content"]),
                    dispatches=1 if mode == "live" else 0,
                )
            elif scenario == "video":
                from aloy.chunk1_smoke import fixture

                fixture(root, video=True)
                clip = root / "synthetic.mp4"
                config = AgentConfig(
                    provider=provider_name if mode == "live" else "fake",
                    model=MODEL_PRESETS[provider_name] if mode == "live" else "fake-v1",
                    tools=("read",),
                    max_steps=3,
                    max_output_tokens=1024,
                    timeout_seconds=90,
                    max_cost_usd=0.25,
                )
                provider = (
                    make_provider(provider_name, store)
                    if mode == "live"
                    else FakeProvider(
                        script=[
                            ProviderStepResult(
                                calls=(ToolCall("video", "read", {"path": str(clip)}),)
                            ),
                            ProviderStepResult(text="Red changes to blue."),
                        ]
                    )
                )
                if mode == "live":
                    provider.dispatch = DispatchBudget(3)
                agent = AgentRunner(config, store, provider)
                registry = agent.registry
                cid = store.create_conversation(config.system_prompt)
                events = [
                    e
                    async for e in agent.stream(
                        AgentInput(
                            cid,
                            f"Use read on {clip} and inspect its actual video. What colour appears "
                            "first and what colour does it change to? Report briefly. Use this "
                            "synthetic file; do not inspect other files or environment.",
                        )
                    )
                ]
                failed = [e.error for e in events if e.kind == "failed"]
                if failed:
                    raise RuntimeError(failed[-1])
                completed = next(e for e in events if e.kind == "completed")
                answer = completed.text.casefold()
                if (
                    "red" not in answer
                    or "blue" not in answer
                    or answer.index("red") > answer.index("blue")
                ):
                    raise AssertionError("Video temporal ground truth was not identified")
                read_steps = [
                    s
                    for s in store.steps(completed.run_id)
                    if s["name"] == "read" and s["kind"] == "tool"
                ]
                if not read_steps or not any(s["status"] == "completed" for s in read_steps):
                    raise AssertionError("Native video must be read successfully")
                report.update(
                    status="passed",
                    answer=completed.text,
                    dispatches=provider.dispatch.count if mode == "live" else 0,
                    reads=len(read_steps),
                )
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(clip, Path(output) / clip.name)
            else:
                if not shutil.which("pdftotext") or not shutil.which("pdftoppm"):
                    raise RuntimeError("Install Poppler before the PDF check")
                pdf = root / "synthetic lesson.pdf"
                make_pdf(pdf)
                text = root / "extracted.txt"
                prefix = root / "page"
                command = (
                    f"pdftotext {shlex.quote(str(pdf))} {shlex.quote(str(text))} && "
                    f"pdftoppm -r 72 -png {shlex.quote(str(pdf))} {shlex.quote(str(prefix))}"
                )
                config = AgentConfig(
                    provider=provider_name if mode == "live" else "fake",
                    model=MODEL_PRESETS[provider_name] if mode == "live" else "fake-v1",
                    tools=("terminal", "terminal_control", "read"),
                    max_steps=12 if provider_name == "codex" else 8,
                    max_output_tokens=2048,
                    timeout_seconds=150,
                    max_cost_usd=0.25,
                )
                cid = store.create_conversation(config.system_prompt)
                provider = (
                    make_provider(provider_name, store)
                    if mode == "live"
                    else FakeProvider(
                        script=[
                            ProviderStepResult(
                                calls=(
                                    ToolCall(
                                        "extract",
                                        "terminal",
                                        {"command": command, "yield_ms": 1000},
                                    ),
                                )
                            ),
                            ProviderStepResult(
                                calls=(
                                    ToolCall("text", "read", {"path": str(text)}),
                                    ToolCall("page1", "read", {"path": str(root / "page-1.png")}),
                                    ToolCall("page2", "read", {"path": str(root / "page-2.png")}),
                                )
                            ),
                            ProviderStepResult(
                                text=(
                                    "Page 1: red square. Page 2: blue triangle. "
                                    "Both page headings were extracted."
                                )
                            ),
                        ]
                    )
                )
                if mode == "live":
                    provider.dispatch = DispatchBudget(8)
                agent = AgentRunner(config, store, provider)
                registry = agent.registry
                prompt = (
                    f"Inspect this synthetic two-page PDF end-to-end: {pdf}. "
                    "Use terminal to extract its text and render BOTH pages to PNG, "
                    "then use read on the text and EACH page image. Report each page heading "
                    "and the large shape and colour you actually see. Do not infer visuals "
                    "from text. Stay within these synthetic files; do not inspect credentials, "
                    f"environment or other files. Suggested command: {command}. "
                    f"Images will be {root}/page-1.png and {root}/page-2.png. "
                    "Read each file once. Keep the final answer brief; this is a bounded "
                    "inspection test and needs no task bookkeeping."
                )
                events = [e async for e in agent.stream(AgentInput(cid, prompt))]
                failed = [e.error for e in events if e.kind == "failed"]
                if failed:
                    raise RuntimeError(failed[-1])
                completed = next(e for e in events if e.kind == "completed")
                steps = store.steps(completed.run_id)
                reads = [
                    json.loads(s["arguments_json"])["arguments"]["path"]
                    for s in steps
                    if s["kind"] == "tool" and s["name"] == "read" and s["status"] == "completed"
                ]
                if set(reads) != {str(text), str(root / "page-1.png"), str(root / "page-2.png")}:
                    raise AssertionError("Every rendered page and extracted text must be read")
                answer = completed.text.casefold()
                if not all(word in answer for word in ["red", "square", "blue", "triangle"]):
                    raise AssertionError("Visual ground truth was not identified")
                report.update(
                    status="passed",
                    answer=completed.text,
                    dispatches=(
                        None
                        if provider_name == "codex" and mode == "live"
                        else provider.dispatch.count
                        if mode == "live"
                        else 0
                    ),
                    managed_turns=1 if provider_name == "codex" and mode == "live" else 0,
                    pages_inspected=2,
                    tool_steps=[
                        {"name": s["name"], "status": s["status"]}
                        for s in steps
                        if s["kind"] == "tool"
                    ],
                )
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    for file in [pdf, text, root / "page-1.png", root / "page-2.png"]:
                        shutil.copyfile(file, Path(output) / file.name)
            report["estimated_usd"] = store.monthly_spend()
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                (Path(output) / f"{scenario}-{provider_name}-{mode}.json").write_text(
                    json.dumps(report, indent=2)
                )
            return report
        finally:
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                audit = [
                    dict(row)
                    for row in store.db.execute(
                        "SELECT kind,name,status,arguments_json,result_json,error FROM run_steps "
                        "ORDER BY sequence"
                    )
                ]
                (Path(output) / "audit.json").write_text(json.dumps(audit, indent=2))
            if registry:
                await registry.harness.close()
            if provider and hasattr(provider, "close"):
                await provider.close()
            if ledger:
                ledger.settle(reservation, store.monthly_spend())
                ledger.close()
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fake", "live"], default="fake")
    parser.add_argument("--scenario", choices=["pdf", "checkpoint", "video"], default="pdf")
    parser.add_argument(
        "--provider", choices=["gemini", "gemini-quality", "openrouter", "codex"], default="gemini"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(asyncio.run(run(args.mode, args.scenario, args.provider, args.output)), indent=2)
    )


if __name__ == "__main__":
    main()
