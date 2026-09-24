"""Cancellable local model process; contains no conversation orchestration."""

import asyncio
import base64
import contextlib
import json
import sys
from pathlib import Path


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

    async def request(self, value: str):
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
    from aloy.speech import MLXTranscriber, PocketSynthesizer

    engine = sys.argv[1]
    model = MLXTranscriber() if engine == "asr" else PocketSynthesizer()
    for line in sys.stdin:
        try:
            value = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                result = (
                    model._transcribe(Path(value))
                    if engine == "asr"
                    else base64.b64encode(model._synthesize(value)).decode()
                )
            packet = {"result": result}
        except Exception as exc:
            packet = {"error": f"Local {engine} failed: {type(exc).__name__}"}
        print(json.dumps(packet), flush=True)


if __name__ == "__main__":
    main()
