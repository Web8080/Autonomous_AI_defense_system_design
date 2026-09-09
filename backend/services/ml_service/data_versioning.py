"""
Dataset versioning and frozen-benchmark quarantine.

The AI-Act traceability promise ("every model that flies carries the dataset hash
that produced it") is only meaningful if the hash is content-derived, and the
promotion gate is only meaningful if the evaluation data was never seen during
training. This module provides:

- `hash_dataset`: a content hash over a cleaned dataset directory (paths + bytes).
- `freeze_benchmark`: builds a train/benchmark split, *refuses* to proceed if any
  benchmark item also appears in the training set (quarantine), and writes a
  signed manifest recording the hash plus counts.

A benchmark without a recorded hash is treated as 'not frozen' by the promotion
gate, which then refuses to certify anything evaluated against it.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class FrozenSplit:
    dataset_hash: str
    benchmark_hash: str
    train_count: int
    benchmark_count: int
    train_root: str
    benchmark_root: str
    manifest_path: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _walk_files(root: str) -> list[str]:
    """All files under root, deterministic relative paths."""
    if not os.path.isdir(root):
        raise NotADirectoryError(f"dataset root does not exist: {root}")
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            out.append(rel)
    return out


def hash_dataset(root: str, *, chunk_bytes: int = 1 << 20) -> str:
    """Content hash over a dataset directory: relative path + size + bytes.

    Deterministic because paths are sorted. Includes file bytes so renaming a
    file changes the hash: the hash identifies the data, not the folder shape.
    """
    h = hashlib.sha256()
    for rel in _walk_files(root):
        full = os.path.join(root, rel)
        size = os.path.getsize(full)
        h.update(f"{rel}\0{size}\0".encode("utf-8"))
        with open(full, "rb") as f:
            while True:
                chunk = f.read(chunk_bytes)
                if not chunk:
                    break
                h.update(chunk)
    return h.hexdigest()


def _key(root: str, prefix: str) -> str:
    """Relative-path key of `prefix` under `root` (deterministic identity)."""
    return os.path.relpath(prefix, root).replace(os.sep, "/")


def freeze_benchmark(
    train_root: str,
    benchmark_root: str,
    manifest_path: str,
    *,
    benchmark_name: str = "visdrone-detect-frozen-2026-09",
) -> FrozenSplit:
    """Freeze the quarantined benchmark split and write its manifest.

    Raises ValueError if any benchmark item exists under the training root:
    that is contamination and must fail loudly, not be logged away.
    """
    bm_files = _walk_files(benchmark_root)
    tr_files = set(_key(train_root, rel) for rel in _walk_files(train_root))

    overlap = sorted({rel for rel in bm_files if _key(benchmark_root, rel) in tr_files})
    # A plain "does this file exist under the train root" check is insufficient:
    # the same *image* could be renamed in the other split while its label was
    # echoed verbatim. Image/label pairs are identified by stem, which is the
    # name the yolo split refers to them by.
    tr_stems = {os.path.splitext(rel)[0] for rel in _walk_files(train_root)}
    bm_stems = {os.path.splitext(rel)[0] for rel in bm_files}
    stem_overlap = sorted(tr_stems & bm_stems)
    if overlap or stem_overlap:
        samples = (overlap or stem_overlap)[:5]
        raise ValueError(
            "benchmark contamination: training and benchmark splits overlap "
            f"(e.g. {samples}). The benchmark must be data the model never trained on."
        )

    dataset_hash = hash_dataset(train_root)
    benchmark_hash = hash_dataset(benchmark_root)

    manifest = {
        "benchmark_name": benchmark_name,
        "dataset_hash": dataset_hash,
        "benchmark_hash": benchmark_hash,
        "train_count": len(_walk_files(train_root)),
        "benchmark_count": len(bm_files),
        "train_root": train_root,
        "benchmark_root": benchmark_root,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "poison": "benchmark is quarantined from training data; do not train on it",
    }

    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    return FrozenSplit(
        dataset_hash=dataset_hash,
        benchmark_hash=benchmark_hash,
        train_count=manifest["train_count"],
        benchmark_count=manifest["benchmark_count"],
        train_root=train_root,
        benchmark_root=benchmark_root,
        manifest_path=manifest_path,
        created_at=manifest["created_at"],
    )


def load_manifest(manifest_path: str) -> dict:
    with open(manifest_path) as f:
        return json.load(f)