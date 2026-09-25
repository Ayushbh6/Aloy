"""Cancellable local model process; contains no conversation orchestration."""

import asyncio
import base64
import contextlib
import json
import os
import sys
from pathlib import Path

from aloy.models import cache_dir


class SpeechWorker:
    def __init__(self, engine: str):
        self.engine = engine
        self.process = None
        self.lock = asyncio.Lock()

    async def close(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()

    async def request(self, value: str | dict[str, str]):
        async with self.lock:
            try:
                async with asyncio.timeout(90):
                    if self.process is None or self.process.returncode is not None:
                        self.process = await asyncio.create_subprocess_exec(
                            sys.executable,
                            "-m",
                            "aloy.speech_worker",
                            self.engine,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL,
                            limit=30_000_000,
                            env={
                                **os.environ,
                                "HF_HUB_CACHE": str(cache_dir()),
                                "HF_HUB_OFFLINE": "1",
                                "TRANSFORMERS_OFFLINE": "1",
                            },
                        )
                    self.process.stdin.write((json.dumps(value) + "\n").encode())
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                    if not line:
                        raise RuntimeError("Local speech engine exited")
                    response = json.loads(line)
                    if "error" in response:
                        raise RuntimeError(response["error"])
                    return response["result"]
            except BaseException:
                await self.close()
                raise


def main():
    from aloy.speech import ChatterboxSynthesizer, MLXTranscriber

    engine = sys.argv[1]
    if engine.startswith("asr:"):
        model = MLXTranscriber(engine.split(":", 1)[1])
    elif engine == "chatterbox-tts":
        model = ChatterboxSynthesizer()
    else:
        raise ValueError("Unknown speech worker")
    for line in sys.stdin:
        try:
            value = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if value == {"command": "prewarm"}:
                    if engine.startswith("asr:"):
                        model._prewarm()
                    else:
                        model._load()
                    result = ""
                else:
                    result = (
                        model._transcribe(Path(value))
                        if engine.startswith("asr:")
                        else base64.b64encode(model._synthesize(value)).decode()
                    )
            packet = {"result": result}
        except Exception as exc:
            packet = {"error": f"Local {engine} failed: {type(exc).__name__}"}
        print(json.dumps(packet), flush=True)


if __name__ == "__main__":
    main()
