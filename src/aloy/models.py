"""Pinned, selective speech-model downloads in one local cache."""

import argparse
import shutil
from pathlib import Path

from aloy.storage import data_root

MODELS = {
    "qwen-asr": (
        "mlx-community/Qwen3-ASR-0.6B-8bit",
        "89e96d92ba34aca20b3e29fb10cc284097d1219f",
        ["*.json", "*.safetensors", "*.model", "*.tiktoken", "*.txt"],
    ),
    "pocket-german": (
        "kyutai/pocket-tts-without-voice-cloning",
        "4e1e0a3e611c51c0b4ed8174fc10f32a54644303",
        [
            "languages/german/model.safetensors",
            "languages/german/tokenizer.json",
            "languages/german/tokenizer.model",
            "languages/german/embeddings/juergen.safetensors",
        ],
    ),
}


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
    selected = {"models--" + repo.replace("/", "--") for repo, _, _ in MODELS.values()}
    for directory in hub.glob("models--*"):
        if directory.name not in selected and directory.is_dir():
            shutil.rmtree(directory)
    locks = hub / ".locks"
    if locks.exists():
        for directory in locks.glob("models--*"):
            if directory.name not in selected and directory.is_dir():
                shutil.rmtree(directory)
    blobs = hub / "blobs"
    if not blobs.exists():
        return 0
    required = {
        file.resolve()
        for directory in hub.glob("models--*")
        for file in directory.rglob("*")
        if file.is_symlink() and file.resolve().is_relative_to(blobs)
    }
    reclaimed = 0
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
    for name in args.names or MODELS:
        print(f"{name}: {model_path(name, local_only=False)}", flush=True)


if __name__ == "__main__":
    main()
