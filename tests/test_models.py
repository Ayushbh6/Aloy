from aloy import models


def test_prune_only_unselected_aloy_cache(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    blobs = hub / "blobs"
    blobs.mkdir(parents=True)
    keep = blobs / "keep"
    unused = blobs / "unused"
    keep.write_bytes(b"retained")
    unused.write_bytes(b"unselected")
    selected = hub / "models--mlx-community--Qwen3-ASR-0.6B-8bit" / "snapshots" / "pin"
    selected.mkdir(parents=True)
    (selected / "model.safetensors").symlink_to(keep)
    discarded = hub / "models--mlx-community--whisper-large-v3-turbo" / "snapshots" / "pin"
    discarded.mkdir(parents=True)
    (discarded / "weights.safetensors").symlink_to(unused)
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    monkeypatch.setattr(models, "cache_dir", lambda: hub)

    assert models.prune_unselected() == len(b"unselected")
    assert keep.exists()
    assert not unused.exists()
    assert not discarded.exists()
    assert outside.read_bytes() == b"untouched"
