# Phase 5 — Tenancy, billing & onboarding (implementation scaffold)

Author: Victor.I

**Status:** Software scaffolding — not live Stripe / multi-tenant production.

Roboflow and cloud billing keys stay optional and out of scope until pre-deploy.

## What exists today

- Schema already carries `org_id` / `site_id` on auth, assets, telemetry, detections.
- API gateway enforces JWT roles and site scoping on many routes.
- Model registry can pin artifacts; per-site pin is a Phase 5 promotion policy.

## Scaffold added in this phase slice

| Piece | Path | Purpose |
|-------|------|---------|
| Tenancy helpers | `backend/shared/defense_shared/tenancy.py` | `require_site_scope`, org/site claim checks |
| Billing meter stub | `backend/services/billing_service/` | In-memory meters for flight_hours / detections / alerts — no Stripe webhook yet |
| Onboarding checklist | this doc §Onboarding | Operator steps before a second tenant |

## Onboarding (manual until self-serve UI)

1. Create org + site rows (SQL or admin API).
2. Seed operator user with `site_ids` claim.
3. Issue edge gateway API key (future) — today: JWT from auth-service.
4. Point Jetson `GET /ml/models/production` (or lab `MODEL_PATH`) at certified artifact.
5. Run `scripts/sim_regression.py` as site acceptance suite (L1 MP4 of that site when available).

## Billing (deferred)

- Metering events: flight hours, detection rows, alert rows, storage GB.
- Stripe Customer Portal + webhook → `billing_service` (not implemented live).
- Grace/overage: fail open to read-only dashboard, never disable e-stop.

## Non-goals until Phase 4 Orin harness is green

- Self-serve signup UI
- Per-tenant Helm charts in prod
- Cross-region active-active

## Gate to expand this doc

Phase 4 `bench/` on Orin green + INT8 re-cert profile `orin-trt-int8` passing.
