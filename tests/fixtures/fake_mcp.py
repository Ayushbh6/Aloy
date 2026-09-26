"""Synthetic newline-delimited MCP peer; no external network or host actions."""

import json
import sys
import time

for line in sys.stdin:
    packet = json.loads(line)
    method = packet.get("method")
    if "id" not in packet:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "synthetic", "version": "1"},
        }
    elif method == "tools/list":
        if packet.get("params", {}).get("cursor"):
            result = {
                "tools": [
                    {
                        "name": "second",
                        "description": "Second catalog page",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        else:
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo synthetic text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }
                ],
                "nextCursor": "page2",
            }
    elif method == "tools/call":
        if packet["params"]["arguments"].get("text") == "WAIT_FOREVER":
            time.sleep(60)
        result = {"content": [{"type": "text", "text": packet["params"]["arguments"]["text"]}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": packet["id"], "result": result}), flush=True)
