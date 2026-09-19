# AI Act technical file outline

Author: Victor.I

Map product controls to an EU AI Act technical documentation structure. Classification
(limited / high-risk) is a legal call — do not self-certify in marketing.

1. General description of the AI system (patrol CV, human-in-the-loop).
2. Detailed design — microservices, Kafka topics, model registry, eval gate.
3. Data governance — frozen benchmark quarantine, provenance on detections.
4. Risk management — 14 failure modes (Phase 2 notes), fail-closed gate.
5. Human oversight — dispatch approval, e-stop, alert triage.
6. Accuracy & robustness — VisDrone metrics + site acceptance (`sim_regression`).
7. Cybersecurity — OWASP map, AI safeguardrails.
8. Post-market monitoring — drift cron, bench nightly (Orin when available).
