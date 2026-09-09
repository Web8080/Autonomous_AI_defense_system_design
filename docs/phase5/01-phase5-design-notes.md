# Phase 5 Design Notes — Multi-Tenant SaaS, Billing & Onboarding (Plan)

**Status:** Planned — sizing and isolation follow the Phase 4 harness

Phase 5 turns the single-site deployment into a true SaaS: many sites, many tenants, one control plane, billed.

---

## 1. Intended scope

| Capability | Notes |
|------------|-------|
| Tenancy | `org_id` / `site_id` already in the schema (`auth.users`, `assets.assets`, `telemetry.vehicle_state` geofence). Phase 5 hardens row-level isolation (RLS or app-layer), plus site-scoped RBAC and audit. |
| Onboarding | Self-serve site creation, API keys for edge gateways, Jetson provisioning (artifact pull from `GET /ml/models/production` per site), fleet health dashboard |
| Billing | Metering: flight hours, detections, alerts, storage. Stripe (or equivalent) — webhook → `billing` service, grace/overage handling |
| Ops | Per-tenant Helm values, per-site Kafka partitions, per-site model pinning (can stay on older `production` while another site promotes), SLO per site |

---

## 2. Dependencies on earlier phases

* **Phase 2 registry** already carries `org_id` in the detection path (`telemetry.vehicle_state` upsert, `detections.detections` provenance). Tenancy is a scoping filter, not a new table.
* **Phase 4 simulation harness** (L1/L2/L3) becomes the **per-tenant acceptance suite** — a site's digital twin (Isaac Sim USD) reproduces its yard and proves the new model before promotion.
* **Serving SLOs** (20 fps FP16 / 30 fps INT8 @1280, p95 <150 ms) are the capacity plan: one Orin per 1–2 streams, `detections-consumer` scales by partition.

---

## 3. Non-goals for now

Billing UI beyond Stripe customer portal, complex seat-based RBAC (current `super_admin`/`local_operator`/`system_ai` suffices), cross-region active-active (add explicit consistency patterns if needed — see `docs/phase0/03-architectural-exploration.md` migration triggers).

---

## 4. When to reconsider

If a tenant needs strong global consistency (e.g., single-flight cross-site transaction), the current eventual-consistency event bus needs a saga/outbox per domain. If team size cannot support tenancy, stay on Phase 4 and offer managed single-tenant instead (the Hybrid fallback).

*Placeholder — to be expanded when Phase 4 edge + simulation nightly green is stable.*
