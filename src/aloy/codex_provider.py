"""Codex subscription transport through the documented local app-server protocol."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from aloy.contracts import (
    AgentConfig,
    ContextOverflowError,
    Message,
    ProviderEvent,
    ProviderStepResult,
    ProviderTurn,
    ToolCall,
    Usage,
)
from aloy.multimodal import data_url, wire_name
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
    "view_image",
)


class CodexProvider:
    native_media_types = ("image",)

    """One provider-managed turn; no claim about hidden underlying model calls."""

    uses_dynamic_tools = True

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
        version = await asyncio.create_subprocess_exec(
            str(self.cli),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, _ = await version.communicate()
        if version.returncode != 0 or output.decode().strip() != "codex-cli 0.156.1":
            raise RuntimeError("Aloy requires its pinned Codex CLI 0.156.1")
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

    async def step(self, turn: ProviderTurn, *, tool_callback=None) -> ProviderStepResult:
        self._agent_turn = turn
        self._tool_callback = tool_callback
        text = ""
        usage = Usage()
        session_id = None
        try:
            async for event in self.stream(
                conversation_id=turn.conversation_id,
                system_prompt=turn.system_prompt,
                messages=turn.messages,
                config=turn.config,
            ):
                if event.kind == "delta":
                    text += event.text
                    if turn.on_event:
                        turn.on_event(event)
                elif event.kind == "usage":
                    usage = event.usage
                elif event.kind == "session":
                    session_id = event.text
            return ProviderStepResult(text=text, usage=usage, session_id=session_id)
        finally:
            self._agent_turn = None
            self._tool_callback = None

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
        agent_turn = getattr(self, "_agent_turn", None)

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
                    {
                        "clientInfo": {"name": "aloy", "title": "Aloy", "version": "0.0.1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
            )
            process.stdin.write(b'{"method":"initialized"}\n')
            await process.stdin.drain()
            effective = await response(await send("config/read", {"includeLayers": False}))
            config_data = effective.get("config", {})
            if any(
                item.get("enabled", True) for item in config_data.get("mcp_servers", {}).values()
            ):
                raise RuntimeError("Aloy's isolated Codex config contains enabled MCP servers")
            skill_params = {"cwds": [str(self.workspace)], "forceReload": True}
            skills = await response(await send("skills/list", skill_params))
            for group in skills.get("data", []):
                if group.get("errors"):
                    raise RuntimeError("Could not verify Codex skills")
                for skill in group.get("skills", []):
                    if skill.get("enabled", True):
                        await response(
                            await send(
                                "skills/config/write", {"path": skill["path"], "enabled": False}
                            )
                        )
            skills = await response(await send("skills/list", skill_params))
            if any(
                skill.get("enabled", True)
                for group in skills.get("data", [])
                for skill in group.get("skills", [])
            ):
                raise RuntimeError("Codex skills were not disabled")
            session = self.store.get_session(conversation_id, f"codex:{config.model}")
            can_resume = bool(session and session["synced_sequence"] == len(messages) - 1)
            if agent_turn is not None:
                can_resume = bool(
                    session
                    and agent_turn.continuation_id
                    and session["session_id"] == agent_turn.continuation_id
                )
            if can_resume:
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
                            + (
                                " Use only the explicitly supplied Aloy tools."
                                if agent_turn and agent_turn.tools
                                else " Respond only with conversational text. Do not use tools."
                            ),
                            "serviceName": "aloy",
                            "dynamicTools": [
                                {
                                    "type": "function",
                                    "name": spec.name.replace(".", "_"),
                                    "description": spec.description,
                                    "inputSchema": spec.input_schema,
                                }
                                for spec in agent_turn.tools
                            ]
                            if agent_turn
                            else [],
                        },
                    )
                )
                input_text = (
                    "Conversation so far:\n"
                    + "\n".join(f"{item.role}: {item.text}" for item in messages)
                    + "\nReply to the latest user message."
                )
            thread_id = result["thread"]["id"]
            turn_input = [{"type": "text", "text": input_text}]
            if agent_turn:
                turn_input.extend(
                    {"type": "localImage", "path": asset["path"]}
                    for asset in agent_turn.media
                    if asset["kind"] == "image"
                )
            await response(
                await send(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": turn_input,
                        "cwd": str(self.workspace),
                        "model": config.model,
                        "approvalPolicy": "never",
                        "sandboxPolicy": {"type": "readOnly"},
                        "outputSchema": config.output_schema,
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
                    if (
                        method == "item/tool/call"
                        and agent_turn is not None
                        and self._tool_callback is not None
                    ):
                        params = packet.get("params", {})
                        tool_call = ToolCall(
                            params.get("callId", ""),
                            wire_name(params.get("tool", ""), agent_turn.tools),
                            params.get("arguments", {}),
                        )
                        result = await self._tool_callback(tool_call)
                        reply = {
                            "id": packet["id"],
                            "result": {
                                "success": result.success,
                                "contentItems": [
                                    *[
                                        {
                                            "type": "inputImage",
                                            "imageUrl": data_url(asset),
                                        }
                                        for asset in result.media
                                    ],
                                    {
                                        "type": "inputText",
                                        "text": json.dumps(result.value, ensure_ascii=False),
                                    },
                                ],
                            },
                        }
                        process.stdin.write((json.dumps(reply) + "\n").encode())
                        await process.stdin.drain()
                        continue
                    raise RuntimeError("Codex requested a disabled tool or approval")
                if method == "item/agentMessage/delta":
                    delta = packet.get("params", {}).get("delta", "")
                    if delta:
                        emitted = True
                        yield ProviderEvent("delta", delta)
                elif method == "thread/tokenUsage/updated":
                    usage = packet.get("params", {}).get("tokenUsage", {}).get("last", {})
                    yield ProviderEvent(
                        "usage", usage=Usage(usage.get("inputTokens"), usage.get("outputTokens"))
                    )
                elif method == "item/started":
                    item_type = packet.get("params", {}).get("item", {}).get("type")
                    if item_type == "contextCompaction":
                        raise ContextOverflowError("Codex attempted context compaction")
                    if item_type and item_type not in (
                        "userMessage",
                        "agentMessage",
                        "reasoning",
                        "plan",
                        "dynamicToolCall",
                    ):
                        raise RuntimeError(f"Codex attempted a non-text item: {item_type}")
                elif method == "turn/completed":
                    status = packet.get("params", {}).get("turn", {}).get("status")
                    if status != "completed":
                        if (
                            "context"
                            in str(
                                packet.get("params", {}).get("turn", {}).get("error", "")
                            ).lower()
                        ):
                            raise ContextOverflowError()
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
