"""Codex subscription transport through the documented local app-server protocol."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from aloy.contracts import AgentConfig, Message, ProviderEvent
from aloy.storage import ConversationStore, data_root

DISABLED_FEATURES = (
    "shell_tool",
    "apps",
    "computer_use",
    "in_app_browser",
    "browser_use",
    "browser_use_external",
    "remote_plugin",
    "multi_agent",
    "multi_agent_v2",
    "hooks",
    "memories",
    "goals",
    "image_generation",
)


class CodexProvider:
    """One provider-managed turn; no claim about hidden underlying model calls."""

    def __init__(self, store: ConversationStore) -> None:
        self.store = store
        self.home = data_root() / "codex-home"
        self.workspace = data_root() / "codex-empty-workspace"
        self.home.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        self.workspace.chmod(0o700)
        self.cli = data_root() / "runtime/codex-cli/node_modules/.bin/codex"
        if not self.cli.exists():
            self.cli = (
                Path(__file__).resolve().parents[2] / ".local/codex-cli/node_modules/.bin/codex"
            )
        if not self.cli.exists():
            raise RuntimeError("Pinned Codex CLI is missing; install the local 0.156.1 CLI")

    def _command(self) -> list[str]:
        command = [str(self.cli)]
        for feature in DISABLED_FEATURES:
            command.extend(("--disable", feature))
        command.extend(("--config", 'web_search="disabled"'))
        return command

    async def _preflight(self, environment: dict[str, str]) -> None:
        login = await asyncio.create_subprocess_exec(
            str(self.cli),
            "login",
            "status",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await login.communicate()
        if login.returncode != 0:
            raise RuntimeError(
                "Aloy's isolated Codex home is not signed in. Run the documented local login setup."
            )
        features = await asyncio.create_subprocess_exec(
            *self._command(),
            "features",
            "list",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, _ = await features.communicate()
        if features.returncode != 0:
            raise RuntimeError("Could not verify Codex tool restrictions")
        states = {
            line.split()[0]: line.split()[-1]
            for line in output.decode().splitlines()
            if len(line.split()) >= 3
        }
        if any(states.get(feature) != "false" for feature in DISABLED_FEATURES):
            raise RuntimeError("Codex tool restrictions did not take effect")

    async def stream(
        self,
        *,
        conversation_id: str,
        system_prompt: str,
        messages: list[Message],
        config: AgentConfig,
    ) -> AsyncIterator[ProviderEvent]:
        environment = {**os.environ, "CODEX_HOME": str(self.home)}
        await self._preflight(environment)
        process = await asyncio.create_subprocess_exec(
            *self._command(),
            "app-server",
            "--stdio",
            cwd=self.workspace,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        pending: list[dict] = []
        sequence = 0

        async def send(method: str, params: dict | None = None) -> int:
            nonlocal sequence
            sequence += 1
            packet = {"method": method, "id": sequence}
            if params is not None:
                packet["params"] = params
            process.stdin.write((json.dumps(packet) + "\n").encode())
            await process.stdin.drain()
            return sequence

        async def response(request_id: int) -> dict:
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise RuntimeError("Codex app-server exited unexpectedly")
                packet = json.loads(line)
                if packet.get("id") == request_id:
                    if "error" in packet:
                        detail = packet["error"].get("message", "rejected a request")
                        raise RuntimeError(f"Codex app-server: {detail[:160]}")
                    return packet["result"]
                pending.append(packet)

        try:
            await response(
                await send(
                    "initialize",
                    {"clientInfo": {"name": "aloy", "title": "Aloy", "version": "0.0.1"}},
                )
            )
            process.stdin.write(b'{"method":"initialized"}\n')
            await process.stdin.drain()
            session = self.store.get_session(conversation_id, f"codex:{config.model}")
            if session and session["synced_sequence"] == len(messages) - 1:
                result = await response(
                    await send(
                        "thread/resume",
                        {
                            "threadId": session["session_id"],
                            "cwd": str(self.workspace),
                            "baseInstructions": system_prompt,
                        },
                    )
                )
                input_text = messages[-1].text
            else:
                result = await response(
                    await send(
                        "thread/start",
                        {
                            "model": config.model,
                            "cwd": str(self.workspace),
                            "approvalPolicy": "never",
                            "sandbox": "read-only",
                            "baseInstructions": system_prompt
                            + " Respond only with conversational text. Do not use tools.",
                            "serviceName": "aloy",
                        },
                    )
                )
                input_text = (
                    "Conversation so far:\n"
                    + "\n".join(f"{item.role}: {item.text}" for item in messages)
                    + "\nReply to the latest user message."
                )
            thread_id = result["thread"]["id"]
            await response(
                await send(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": input_text}],
                        "cwd": str(self.workspace),
                        "model": config.model,
                        "approvalPolicy": "never",
                        "sandboxPolicy": {"type": "readOnly"},
                    },
                )
            )
            emitted = False
            while True:
                if pending:
                    packet = pending.pop(0)
                else:
                    line = await process.stdout.readline()
                    if not line:
                        raise RuntimeError("Codex app-server ended before turn completion")
                    packet = json.loads(line)
                method = packet.get("method", "")
                if "id" in packet and method:
                    raise RuntimeError(
                        "Codex requested a tool or approval; text-only turn rejected"
                    )
                if method == "item/agentMessage/delta":
                    delta = packet.get("params", {}).get("delta", "")
                    if delta:
                        emitted = True
                        yield ProviderEvent("delta", delta)
                elif method == "item/started":
                    item_type = packet.get("params", {}).get("item", {}).get("type")
                    if item_type and item_type not in (
                        "userMessage",
                        "agentMessage",
                        "reasoning",
                        "plan",
                    ):
                        raise RuntimeError(f"Codex attempted a non-text item: {item_type}")
                elif method == "turn/completed":
                    status = packet.get("params", {}).get("turn", {}).get("status")
                    if status != "completed":
                        raise RuntimeError(f"Codex turn ended with status {status}")
                    if not emitted:
                        raise RuntimeError("Codex returned no final text")
                    yield ProviderEvent("session", thread_id)
                    return
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
