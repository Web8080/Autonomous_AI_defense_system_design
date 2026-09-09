"""Training and evaluation tooling for the VisDrone detection model.

Runs on a GPU box (never in CI, and never in the service container). The model
registry service is the *certificate* layer: it records provenance, runs the
promotion gate, and archives incumbents. This package produces the evidence the
registry certifies:

- `train.py`      reproducible training -> artifact + content hash + manifest
- `eval.py`       frozen benchmark + temporal corpus -> ml.model_evals-shaped JSON
- `config/dataset.yaml`  VisDrone classes + train/val roots (benchmark excluded)
"""