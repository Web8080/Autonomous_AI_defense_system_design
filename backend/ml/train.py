"""Training entrypoint for the VisDrone detection model.

Runs on a GPU box (never in CI). Purpose: make every training run reproducible
and every artifact proveable:

- fixed seed, pinned ultralytics/pytorch (see backend/requirements.txt),
- the dataset hash is computed from content via ml_service.data_versioning,
  so the hash recorded against the model means something,
- the trained artifact is copied out and content-hashed; the hash is printed
  for registration in the ML registry.

Usage:
    python -m ml.train \
        --dataset-root data/visdrone-yolo/train \
        --artifact-out artifacts/visdrone-yolov8s.pt \
        --git-sha $(git rev-parse HEAD) \
        --epochs 100 --batch 16 --imgsz 640 --seed 42

The evaluation split is intentionally NOT accepted here. Training on the
benchmark split is a structural offence handled by data_versioning.freeze_benchmark;
this script refuses a data root that contains the frozen benchmark stems only if
you also pass --benchmark-root to check against.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "services"))
sys.path.insert(0, str(BACKEND / "services" / "ml_service"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def outer_sample_size(data_yaml: Path) -> int:
    import yaml
    data = yaml.safe_load(data_yaml.read_text())
    root = Path(data["train_root"])
    return sum(1 for _ in root.glob("images/*")) if root.exists() else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--benchmark-root")
    parser.add_argument("--data-yaml", default=str(Path(__file__).parent / "config" / "dataset.yaml"))
    parser.add_argument("--artifact-out", required=True)
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--weights", default="",
                        help="start from an existing trained weights file (stage 2 warm start)")
    parser.add_argument("--freeze", type=int, default=0,
                        help="freeze this many backbone layers (stage 1: ~10)")
    parser.add_argument("--lr0", type=float, default=0.02)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--project", default="runs")
    parser.add_argument("--run-name", default="train")
    parser.add_argument("--git-sha", default="")
    args = parser.parse_args()

    random.seed(args.seed)

    # Optional quarantine self-check: if a benchmark root is supplied, verify
    # the training root does not contain the same images (by stem, so renames
    # cannot hide contamination). This duplicates the freeze-time check as a
    # second layer at the moment someone actually starts training.
    if args.benchmark_root:
        from ml_service.data_versioning import _walk_files
        tr_stems = {Path(p).stem for p in Path(args.dataset_root).rglob("*.jpg")}
        bm_stems = {Path(p).stem for p in Path(args.benchmark_root).rglob("*.jpg")}
        overlap = sorted(tr_stems & bm_stems)
        if overlap:
            sys.exit(f"REFUSED: training root overlaps frozen benchmark (e.g. {overlap[:3]}).")

    from ml_service.data_versioning import hash_dataset
    dataset_hash = hash_dataset(args.dataset_root)
    print(f"dataset_hash={dataset_hash}")

    from ultralytics import YOLO
    model = YOLO(args.weights or args.model)
    results = model.train(
        data=args.data_yaml,
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        seed=args.seed,
        project=args.project,
        name=args.run_name,
        # deterministic=True is not honoured well on the MPS backend; the seed
        # still pins weight init / data ordering.
        deterministic=(args.device != "mps"),
        device=args.device,
        freeze=args.freeze,
        lr0=args.lr0,
        amp=False,
    )

    # `results.save_dir` is authoritative: ultralytics may nest the named run
    # under a framework project dir (e.g. runs/detect/<name>) depending on
    # version, so never re-derive the path from args.
    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.exists():
        sys.exit(f"training finished but best.pt not found at {best}")
    out = Path(args.artifact_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(best, out)
    artifact_sha = sha256_file(out)

    manifest = {
        "artifact": str(out),
        "artifact_sha256": artifact_sha,
        "dataset_hash": dataset_hash,
        "git_sha": args.git_sha,
        "seed": args.seed,
        "epochs": args.epochs,
        "batch": args.batch,
        "imgsz": args.imgsz,
        "model": args.model,
    }
    manifest_path = out.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"artifact={out.absolute()} artifact_sha256={artifact_sha}")
    print(f"manifest={manifest_path.absolute()}")

    # Next step, printed because it is easy to forget and cheap to check:
    print("register in the registry: POST /models with git_sha, dataset_hash, artifact_uri")


if __name__ == "__main__":
    main()