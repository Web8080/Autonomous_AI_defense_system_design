# Bench harness (Phase 4)

Author: Victor.I

Local CPU/MPS/CUDA profiling for the inference serving layer. Same JSON shape
is intended for Jetson nightly jobs later.

```bash
python bench/profile_stages.py \
  --weights data/models/visdrone-yolov8n.pt \
  --images data/visdrone-yolo/val/images \
  --limit 40 \
  --device mps \
  --out bench/results/stages.json
```

Orin: set `--device orin-trt-fp16` after loading a `.engine` with
`INFERENCE_RUNTIME=tensorrt`.
