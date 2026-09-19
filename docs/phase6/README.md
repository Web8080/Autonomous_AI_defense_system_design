# Phase 6 — Compliance evidence pack (templates)

Author: Victor.I

**Status:** Templates + evidence map. Not a completed DPIA / pen-test / AI Act filing.

The registry, audit trail, eval gate, and frozen benchmark are the technical
evidence base. This folder holds the human-facing files that wrap them.

## Deliverables checklist

| Artifact | Template | Evidence in repo |
|----------|----------|------------------|
| Model card | `docs/phase6/MODEL_CARD_TEMPLATE.md` | `manifests/`, `ml.models`, eval records |
| DPIA draft | `docs/phase6/DPIA_OUTLINE.md` | auth/RBAC, blur policy, human approval gate |
| AI Act technical file outline | `docs/phase6/AI_ACT_TECHNICAL_FILE.md` | safeguardrails, fail-closed gate, audit |
| Pen-test scope | `docs/phase6/PENTEST_SCOPE.md` | OWASP map in `docs/security/` |

## Hard rules already in product

- Observe and alert only — no kinetic autonomy.
- Human approval before arm/dispatch (`approved_by` / `approved_at`).
- Faces / plates blurred at edge by product policy (see AI safeguardrails).
- Stub / unregistered detections never become training labels.

## Before claiming compliance

Legal review + signed DPIA + independent pen-test. Agents must not invent
certifications or CE marks.
