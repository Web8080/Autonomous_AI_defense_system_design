# Model card template

Author: Victor.I

Fill one card per promoted artifact. Do not invent metrics — copy from eval records.

## Identity

- Model name / version:
- Artifact SHA256:
- Runtime / precision: (pytorch | onnx | tensorrt) / (fp32 | fp16 | int8)
- Training git SHA:
- Dataset hash:
- Benchmark identity + hash:

## Intended use

- Critical infrastructure aerial patrol (observe & alert).
- Out of scope: identification of individuals, kinetic action, biometric matching.

## Metrics (from eval gate record)

- mAP@50:
- false_alarms_per_hour:
- recall_at_target_far:
- latency_p95_ms / device:
- Per-class recall table:

## Training data

- Sources (VisDrone / site / synthetic):
- Known gaps (night, weather, rare classes):

## Ethical / safety

- Human approval gate before dispatch: yes
- Blur policy: faces / plates
- Fail-closed promotion: yes

## Contact

- Owner:
- Date:
