"""Pinned, selective speech-model downloads in one local cache."""

import argparse
import shutil
from pathlib import Path

from aloy.storage import data_root

MODELS = {
    "chatterbox": (
        "mlx-community/chatterbox-multilingual-v3",
        "03565773edd72e949572557597af8063bb49a18a",
        ["config.json", "tokenizer.json", "Cangjie5_TC.json", "model.safetensors"],
    ),
    "chatterbox-tokenizer": (
        "mlx-community/S3TokenizerV2",
        "e0c9886f0e1c35ae85b1f27277416fb19fc72bec",
        ["config.json", "model.safetensors"],
    ),
    "vad": (
        "mlx-community/Silero-VAD",
        "7bc17f22d3c0451bd3a6cd71e759b009271ff49a",
        ["config.json", "model.safetensors"],
    ),
    "qwen-asr": (
        "mlx-community/Qwen3-ASR-1.7B-8bit",
        "a8379a2e2f9e313c9292cdf1af4055ab56d50d55",
        ["*.json", "*.safetensors", "*.model", "*.tiktoken", "*.txt"],
    ),
}


DEFAULT_MODELS = ("vad", "qwen-asr", "chatterbox", "chatterbox-tokenizer")


def cache_dir() -> Path:
    return data_root() / "models" / "hub"


def model_path(name: str, *, local_only: bool = True) -> Path:
    from huggingface_hub import snapshot_download

    repo, revision, patterns = MODELS[name]
    return Path(
        snapshot_download(
            repo_id=repo,
            revision=revision,
            allow_patterns=patterns,
            cache_dir=cache_dir(),
            local_files_only=local_only,
        )
    )


def prune_unselected() -> int:
    """Remove only unused files from Aloy's dedicated model cache."""
    hub = cache_dir().resolve()
    if not hub.exists():
        return 0
    reclaimed = 0
    selected = {"models--" + MODELS[name][0].replace("/", "--") for name in DEFAULT_MODELS}
    for directory in hub.glob("models--*"):
        if directory.name not in selected and directory.is_dir():
            reclaimed += sum(
                file.stat().st_size
                for file in directory.rglob("*")
                if file.is_file() and not file.is_symlink()
            )
            shutil.rmtree(directory)
    locks = hub / ".locks"
    if locks.exists():
        for directory in locks.glob("models--*"):
            if directory.name not in selected and directory.is_dir():
                shutil.rmtree(directory)
    blobs = hub / "blobs"
    if not blobs.exists():
        return reclaimed
    required = {
        file.resolve()
        for directory in hub.glob("models--*")
        for file in directory.rglob("*")
        if file.is_symlink() and file.resolve().is_relative_to(blobs)
    }
    for file in blobs.rglob("*"):
        if file.is_file() and file.name != ".huggingface-shared-blobs" and file not in required:
            reclaimed += file.stat().st_size
            file.unlink()
    for directory in sorted((p for p in blobs.rglob("*") if p.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    return reclaimed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*", choices=list(MODELS))
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args()
    if args.prune:
        print(f"Reclaimed {prune_unselected() / 1024**3:.2f} GiB from unused Aloy models")
        return
    for name in args.names or DEFAULT_MODELS:
        print(f"{name}: {model_path(name, local_only=False)}", flush=True)


if __name__ == "__main__":
    main()
