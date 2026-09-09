"""Tests for dataset versioning + frozen benchmark quarantine."""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "services" / "ml_service"))

from data_versioning import (  # noqa: E402
    freeze_benchmark,
    hash_dataset,
    load_manifest,
)


def _make_tree(root: Path, files: dict[str, bytes]):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def test_hash_is_deterministic_and_content_sensitive(tmp_path):
    a = _make_tree(tmp_path / "a", {"images/x.jpg": b"abc", "labels/x.txt": b"1 0.5 0.5 0.1 0.1"})
    h1 = hash_dataset(str(a))
    assert len(h1) == 64
    assert hash_dataset(str(a)) == h1  # deterministic

    b = _make_tree(tmp_path / "b", {"images/x.jpg": b"abc", "labels/x.txt": b"1 0.5 0.5 0.1 0.2"})
    assert hash_dataset(str(b)) != h1  # content change detected

    c = _make_tree(tmp_path / "c", {"images/x.jpg": b"abc", "images/y.jpg": b"abc"})
    assert hash_dataset(str(c)) != h1  # file set change detected


def test_freeze_writes_manifest(tmp_path):
    train = _make_tree(
        tmp_path / "train",
        {"images/t1.jpg": b"t1", "labels/t1.txt": b"1 0.1 0.1 0.1 0.1",
         "images/t2.jpg": b"t2", "labels/t2.txt": b"1 0.2 0.2 0.1 0.1"},
    )
    bm = _make_tree(
        tmp_path / "bm",
        {"images/b1.jpg": b"b1", "labels/b1.txt": b"1 0.3 0.3 0.1 0.1"},
    )
    split = freeze_benchmark(str(train), str(bm), str(tmp_path / "manifest.json"))
    assert split.train_count == 4
    assert split.benchmark_count == 2
    assert split.dataset_hash and split.benchmark_hash

    manifest = load_manifest(str(tmp_path / "manifest.json"))
    assert manifest["benchmark_name"] == "visdrone-detect-frozen-2026-09"
    assert manifest["benchmark_hash"] == split.benchmark_hash
    assert json.dumps(manifest["poison"])


def test_freeze_rejects_identical_file_in_both_splits(tmp_path):
    train = _make_tree(tmp_path / "train", {"images/x.jpg": b"leak", "labels/x.txt": b"1 0.1 0.1 0.1 0.1"})
    bm = _make_tree(tmp_path / "bm", {"images/x.jpg": b"leak", "labels/x.txt": b"1 0.1 0.1 0.1 0.1"})
    with pytest.raises(ValueError, match="contamination"):
        freeze_benchmark(str(train), str(bm), str(tmp_path / "m.json"))


def test_freeze_rejects_renamed_contamination(tmp_path):
    # Same image renamed between splits must be caught via stem identity.
    train = _make_tree(tmp_path / "train", {"images/img_0001.jpg": b"same", "labels/img_0001.txt": b"1 0.1 0.1 0.1 0.1"})
    bm = _make_tree(tmp_path / "bm", {"images/img_0001.jpg": b"same", "labels/img_0001.txt": b"1 0.1 0.1 0.1 0.1"})
    with pytest.raises(ValueError):
        freeze_benchmark(str(train), str(bm), str(tmp_path / "m.json"))