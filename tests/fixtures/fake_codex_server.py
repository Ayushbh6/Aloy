"""A tiny JSONL app-server stand-in for Codex dynamic-tool adapter tests."""

import json
import sys


def send(packet):
    print(json.dumps(packet), flush=True)


for line in sys.stdin:
    packet = json.loads(line)
    method = packet.get("method")
    if method == "initialized":
        continue
    if "id" not in packet:
        continue
    identifier = packet["id"]
    if method == "initialize":
        assert packet["params"]["capabilities"]["experimentalApi"]
        send({"id": identifier, "result": {}})
    elif method == "config/read":
        send({"id": identifier, "result": {"config": {"mcp_servers": {}}}})
    elif method == "skills/list":
        send({"id": identifier, "result": {"data": []}})
    elif method == "thread/start":
        assert packet["params"]["dynamicTools"][0]["name"] == "memory_search"
        send({"id": identifier, "result": {"thread": {"id": "fake-thread"}}})
    elif method == "turn/start":
        send({"id": identifier, "result": {"turn": {"id": "fake-turn"}}})
        send(
            {
                "id": 900,
                "method": "item/tool/call",
                "params": {
                    "tool": "memory_search",
                    "callId": "fake-call",
                    "arguments": {"query": "German"},
                    "threadId": "fake-thread",
                    "turnId": "fake-turn",
                },
            }
        )
    elif identifier == 900:
        assert packet["result"]["success"]
        send({"method": "item/agentMessage/delta", "params": {"delta": "A fact."}})
        send({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
