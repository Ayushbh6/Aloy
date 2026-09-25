"""Bounded private media preparation and evidence contracts."""

import asyncio
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from jsonschema import validate

MEDIA_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "observations": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "timestamp_seconds": {"type": "number", "minimum": 0},
                    "modality": {"type": "string", "enum": ["visual", "audio", "both"]},
                    "description": {"type": "string"},
                },
                "required": ["timestamp_seconds", "modality", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "observations"],
    "additionalProperties": False,
}


def parse_observations(text):
    result = json.loads(text)
    validate(result, MEDIA_SCHEMA)
    if not result["observations"]:
        raise ValueError("Vision returned no observations")
    if len(json.dumps(result)) > 14000:
        raise ValueError("Vision evidence exceeds output limit")
    return result


@asynccontextmanager
async def prepared_media(asset, root):
    path = Path(asset["path"])
    temporary = None
    mime = asset["mime_type"]
    try:
        if asset["kind"] in {"video", "audio"}:
            probe = await asyncio.create_subprocess_exec(
                shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                output, _ = await probe.communicate()
                if probe.returncode or float(output) > 60.5:
                    raise ValueError("Attach a clip of 60 seconds or less")
            finally:
                if probe.returncode is None:
                    probe.kill()
                    await probe.wait()
            folder = root / "tmp"
            folder.mkdir(parents=True, exist_ok=True)
            folder.chmod(0o700)
            temporary = folder / f"inspect-{uuid.uuid4().hex}.mp4"
            if asset["kind"] == "audio":
                temporary = temporary.with_suffix(".wav")
                options = ["-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le"]
                mime = "audio/wav"
            else:
                options = [
                    "-vf",
                    "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=10",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-b:v",
                    "1200k",
                    "-maxrate",
                    "1500k",
                    "-bufsize",
                    "3000k",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                ]
                mime = "video/mp4"
            process = await asyncio.create_subprocess_exec(
                shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(path),
                "-t",
                "60",
                *options,
                str(temporary),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, error = await process.communicate()
                if process.returncode:
                    raise RuntimeError("Media preparation failed: " + error.decode()[-200:])
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            temporary.chmod(0o600)
            path = temporary
        if path.stat().st_size > 20_000_000:
            raise ValueError("Prepared media exceeds the 20 MB request limit")
        yield path.read_bytes(), mime
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
