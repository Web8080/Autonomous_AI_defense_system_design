-- Phase 0: product schema for infrastructure monitoring.
-- Adds multi-tenancy (org -> site), missions/flights, persisted detections,
-- model registry, and media. Requires PostGIS + TimescaleDB.
-- Run after 01_init.sql.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS orgs;
CREATE SCHEMA IF NOT EXISTS missions;
CREATE SCHEMA IF NOT EXISTS detections;
CREATE SCHEMA IF NOT EXISTS ml;
CREATE SCHEMA IF NOT EXISTS media;

-- ---------------------------------------------------------------------------
-- Tenancy: organization -> site -> asset
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS orgs.organizations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(255) NOT NULL,
    slug         VARCHAR(128) NOT NULL UNIQUE,
    plan         VARCHAR(64)  NOT NULL DEFAULT 'trial',
    -- Data retention, in days, for raw media. Compliance-driven; see DPIA.
    media_retention_days INT NOT NULL DEFAULT 30,
    disabled     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- A site is a physical location under monitoring: a substation, a rail section,
-- a solar farm, a port. Geofence is authoritative and enforced pre-arm.
CREATE TABLE IF NOT EXISTS orgs.sites (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES orgs.organizations(id) ON DELETE CASCADE,
    name          VARCHAR(255) NOT NULL,
    slug          VARCHAR(128) NOT NULL,
    -- Legacy bridge: 01_init used a free-text region_id on assets.
    legacy_region_id VARCHAR(128),
    timezone      VARCHAR(64) NOT NULL DEFAULT 'Europe/London',
    centroid      GEOGRAPHY(POINT, 4326),
    -- Operating envelope
    boundary      GEOGRAPHY(POLYGON, 4326),
    max_altitude_m NUMERIC(6,1) NOT NULL DEFAULT 120.0,  -- UK/EU open-category ceiling
    metadata      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_sites_org ON orgs.sites(org_id);
CREATE INDEX IF NOT EXISTS idx_sites_legacy_region ON orgs.sites(legacy_region_id);
CREATE INDEX IF NOT EXISTS idx_sites_boundary ON orgs.sites USING GIST(boundary);

-- ---------------------------------------------------------------------------
-- Auth hardening: real credentials + org membership
-- ---------------------------------------------------------------------------

ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES orgs.organizations(id) ON DELETE CASCADE;
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS failed_login_count INT NOT NULL DEFAULT 0;
ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_org ON auth.users(org_id);

-- Site-level scoping for operators. Absence of rows for a non-admin = no access.
CREATE TABLE IF NOT EXISTS auth.user_sites (
    user_id  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    site_id  UUID NOT NULL REFERENCES orgs.sites(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, site_id)
);

-- Refresh tokens, hashed at rest, revocable.
CREATE TABLE IF NOT EXISTS auth.refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token_hash  VARCHAR(255) NOT NULL UNIQUE,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    user_agent  VARCHAR(512),
    ip_addr     INET
);

CREATE INDEX IF NOT EXISTS idx_refresh_user ON auth.refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_expires ON auth.refresh_tokens(expires_at);

-- ---------------------------------------------------------------------------
-- Assets: bring under tenancy
-- ---------------------------------------------------------------------------

ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS org_id  UUID REFERENCES orgs.organizations(id) ON DELETE CASCADE;
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES orgs.sites(id) ON DELETE SET NULL;
-- Airframe/vehicle identity, needed for MAVLink routing and Remote ID compliance.
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS serial_number   VARCHAR(128);
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS mavlink_sys_id  INT;
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS remote_id       VARCHAR(128);
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS firmware_version VARCHAR(64);
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS flight_hours    NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS battery_cycles  INT NOT NULL DEFAULT 0;
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS maintenance_due_at TIMESTAMPTZ;
ALTER TABLE assets.assets ADD COLUMN IF NOT EXISTS last_seen_at    TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_assets_org  ON assets.assets(org_id);
CREATE INDEX IF NOT EXISTS idx_assets_site ON assets.assets(site_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_serial ON assets.assets(serial_number) WHERE serial_number IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Geofences and no-fly zones
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS missions.geofences (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES orgs.organizations(id) ON DELETE CASCADE,
    site_id    UUID NOT NULL REFERENCES orgs.sites(id) ON DELETE CASCADE,
    name       VARCHAR(255) NOT NULL,
    -- 'inclusion': aircraft must remain inside. 'exclusion': must never enter.
    kind       VARCHAR(32) NOT NULL CHECK (kind IN ('inclusion', 'exclusion')),
    geom       GEOGRAPHY(POLYGON, 4326) NOT NULL,
    min_altitude_m NUMERIC(6,1) NOT NULL DEFAULT 0,
    max_altitude_m NUMERIC(6,1) NOT NULL DEFAULT 120.0,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geofence_site ON missions.geofences(site_id);
CREATE INDEX IF NOT EXISTS idx_geofence_geom ON missions.geofences USING GIST(geom);

-- Monitored zones drive alerting: an intrusion is a detection inside one of these.
-- Deliberately geometric, not learned, so the decision is explainable.
CREATE TABLE IF NOT EXISTS missions.monitored_zones (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES orgs.organizations(id) ON DELETE CASCADE,
    site_id    UUID NOT NULL REFERENCES orgs.sites(id) ON DELETE CASCADE,
    name       VARCHAR(255) NOT NULL,
    geom       GEOGRAPHY(POLYGON, 4326) NOT NULL,
    -- Which detected classes are considered violations in this zone.
    alert_classes TEXT[] NOT NULL DEFAULT '{person,vehicle}',
    severity   VARCHAR(32) NOT NULL DEFAULT 'high',
    -- Restrict alerting to a time window, e.g. out-of-hours only.
    active_from TIME,
    active_to   TIME,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_zone_site ON missions.monitored_zones(site_id);
CREATE INDEX IF NOT EXISTS idx_zone_geom ON missions.monitored_zones USING GIST(geom);

-- ---------------------------------------------------------------------------
-- Missions, waypoints, flights
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS missions.missions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES orgs.organizations(id) ON DELETE CASCADE,
    site_id     UUID NOT NULL REFERENCES orgs.sites(id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    mission_type VARCHAR(64) NOT NULL DEFAULT 'patrol'
        CHECK (mission_type IN ('patrol', 'survey', 'inspection', 'investigate', 'manual')),
    -- NULL = ad hoc. Otherwise a cron expression for recurring patrols.
    schedule_cron VARCHAR(128),
    default_altitude_m NUMERIC(6,1) NOT NULL DEFAULT 60.0,
    default_speed_ms   NUMERIC(5,2) NOT NULL DEFAULT 5.0,
    -- Every autonomous dispatch requires a human to confirm before arm.
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  UUID REFERENCES auth.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mission_site ON missions.missions(site_id);

CREATE TABLE IF NOT EXISTS missions.waypoints (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id  UUID NOT NULL REFERENCES missions.missions(id) ON DELETE CASCADE,
    seq         INT NOT NULL,
    position    GEOGRAPHY(POINT, 4326) NOT NULL,
    altitude_m  NUMERIC(6,1) NOT NULL,
    speed_ms    NUMERIC(5,2),
    -- MAVLink-ish action at this waypoint.
    action      VARCHAR(64) NOT NULL DEFAULT 'waypoint'
        CHECK (action IN ('waypoint', 'loiter', 'capture', 'scan', 'rtl', 'land')),
    loiter_seconds INT NOT NULL DEFAULT 0,
    heading_deg NUMERIC(5,1),
    gimbal_pitch_deg NUMERIC(5,1),
    UNIQUE (mission_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_waypoint_mission ON missions.waypoints(mission_id, seq);

-- A flight is one execution of a mission by one asset.
CREATE TABLE IF NOT EXISTS missions.flights (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES orgs.organizations(id) ON DELETE CASCADE,
    site_id       UUID NOT NULL REFERENCES orgs.sites(id) ON DELETE CASCADE,
    mission_id    UUID REFERENCES missions.missions(id) ON DELETE SET NULL,
    asset_id      UUID NOT NULL REFERENCES assets.assets(id) ON DELETE CASCADE,
    status        VARCHAR(32) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'awaiting_approval', 'approved', 'armed',
                          'in_flight', 'returning', 'completed', 'aborted', 'failed')),
    -- Human oversight record. Populated before arm when requires_approval.
    approved_by   UUID REFERENCES auth.users(id),
    approved_at   TIMESTAMPTZ,
    -- What triggered this flight: schedule, operator, or a detection.
    trigger_kind  VARCHAR(32) NOT NULL DEFAULT 'manual'
        CHECK (trigger_kind IN ('manual', 'scheduled', 'detection')),
    trigger_detection_id UUID,
    started_at    TIMESTAMPTZ,
    ended_at      TIMESTAMPTZ,
    duration_s    INT,
    distance_m    NUMERIC(10,2),
    battery_start_pct NUMERIC(5,2),
    battery_end_pct   NUMERIC(5,2),
    abort_reason  VARCHAR(255),
    -- Denormalised flight path for fast replay without scanning telemetry.
    track         GEOGRAPHY(LINESTRING, 4326),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flight_asset   ON missions.flights(asset_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_flight_site    ON missions.flights(site_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_flight_status  ON missions.flights(status);
CREATE INDEX IF NOT EXISTS idx_flight_mission ON missions.flights(mission_id);

-- ---------------------------------------------------------------------------
-- Detections: previously only Kafka messages, never persisted.
-- Needed for audit, analytics, and as the training-data capture path.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS detections.detections (
    id            UUID NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL,
    site_id       UUID,
    asset_id      UUID,
    flight_id     UUID,
    detected_at   TIMESTAMPTZ NOT NULL,
    frame_id      VARCHAR(128),
    class_name    VARCHAR(128) NOT NULL,
    confidence    NUMERIC(5,4) NOT NULL,
    -- Normalised xyxy in [0,1] so it is resolution independent.
    bbox          NUMERIC(6,5)[] NOT NULL,
    -- Geo-referenced footprint of the detection, when pose is known.
    position      GEOGRAPHY(POINT, 4326),
    altitude_m    NUMERIC(6,1),
    -- Provenance: which model produced this. Required for AI Act traceability.
    model_id      UUID,
    model_version VARCHAR(64),
    -- Deterministic zone evaluation result, not learned.
    zone_id       UUID,
    in_restricted_zone BOOLEAN NOT NULL DEFAULT FALSE,
    -- Operator feedback: this is the active-learning label.
    review_state  VARCHAR(32) NOT NULL DEFAULT 'unreviewed'
        CHECK (review_state IN ('unreviewed', 'confirmed', 'dismissed', 'ambiguous')),
    reviewed_by   UUID,
    reviewed_at   TIMESTAMPTZ,
    -- Set when the operator corrects the class; feeds relabelling.
    corrected_class VARCHAR(128),
    media_id      UUID,
    metadata      JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, detected_at)
);

SELECT create_hypertable('detections.detections', 'detected_at',
                         chunk_time_interval => INTERVAL '7 days',
                         if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_det_org_time    ON detections.detections(org_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_det_site_time   ON detections.detections(site_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_det_flight      ON detections.detections(flight_id);
CREATE INDEX IF NOT EXISTS idx_det_review      ON detections.detections(review_state, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_det_class       ON detections.detections(class_name, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_det_position    ON detections.detections USING GIST(position);

-- ---------------------------------------------------------------------------
-- Telemetry: convert to a hypertable and add real flight fields
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry.vehicle_state (
    asset_id      UUID NOT NULL,
    org_id        UUID NOT NULL,
    flight_id     UUID,
    ts            TIMESTAMPTZ NOT NULL,
    position      GEOGRAPHY(POINT, 4326),
    altitude_rel_m NUMERIC(7,2),
    altitude_amsl_m NUMERIC(7,2),
    heading_deg   NUMERIC(5,1),
    groundspeed_ms NUMERIC(6,2),
    vertical_speed_ms NUMERIC(6,2),
    roll_deg      NUMERIC(6,2),
    pitch_deg     NUMERIC(6,2),
    yaw_deg       NUMERIC(6,2),
    battery_pct   NUMERIC(5,2),
    battery_voltage NUMERIC(6,2),
    gps_fix_type  SMALLINT,
    gps_satellites SMALLINT,
    -- MAVLink flight mode string, e.g. OFFBOARD, AUTO.MISSION, AUTO.RTL
    flight_mode   VARCHAR(64),
    armed         BOOLEAN,
    -- Link health drives the loss-of-link failsafe.
    rssi_dbm      SMALLINT,
    link_latency_ms INT,
    PRIMARY KEY (asset_id, ts)
);

SELECT create_hypertable('telemetry.vehicle_state', 'ts',
                         chunk_time_interval => INTERVAL '1 day',
                         if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_vstate_flight ON telemetry.vehicle_state(flight_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_vstate_org    ON telemetry.vehicle_state(org_id, ts DESC);

-- Raw telemetry is high volume and low long-term value; compress aggressively.
ALTER TABLE telemetry.vehicle_state SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'asset_id',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy('telemetry.vehicle_state', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('telemetry.vehicle_state', INTERVAL '400 days', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Model registry: no model reaches an aircraft without a recorded eval.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ml.models (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(255) NOT NULL,
    version      VARCHAR(64)  NOT NULL,
    task         VARCHAR(64)  NOT NULL
        CHECK (task IN ('detection', 'segmentation', 'classification', 'thermal', 'tracking')),
    architecture VARCHAR(128),
    -- Full provenance chain, required for AI Act technical documentation.
    git_sha      VARCHAR(64),
    dataset_hash VARCHAR(128),
    mlflow_run_id VARCHAR(128),
    artifact_uri  TEXT,
    -- Deployment target and format.
    runtime      VARCHAR(32) NOT NULL DEFAULT 'pytorch'
        CHECK (runtime IN ('pytorch', 'onnx', 'tensorrt', 'openvino')),
    precision    VARCHAR(16) NOT NULL DEFAULT 'fp32'
        CHECK (precision IN ('fp32', 'fp16', 'int8')),
    class_names  TEXT[] NOT NULL DEFAULT '{}',
    stage        VARCHAR(32) NOT NULL DEFAULT 'development'
        CHECK (stage IN ('development', 'staging', 'production', 'archived')),
    promoted_by  UUID REFERENCES auth.users(id),
    promoted_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name, version)
);

CREATE INDEX IF NOT EXISTS idx_models_stage ON ml.models(stage);

CREATE TABLE IF NOT EXISTS ml.model_evals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id     UUID NOT NULL REFERENCES ml.models(id) ON DELETE CASCADE,
    benchmark    VARCHAR(128) NOT NULL,
    benchmark_hash VARCHAR(128),
    -- Aggregate metrics
    map_50       NUMERIC(6,4),
    map_50_95    NUMERIC(6,4),
    precision_at_conf NUMERIC(6,4),
    recall_at_conf    NUMERIC(6,4),
    -- The metric that actually matters operationally.
    false_alarms_per_hour NUMERIC(8,3),
    recall_at_target_far  NUMERIC(6,4),
    -- Latency on the target device.
    device       VARCHAR(64),
    latency_p50_ms NUMERIC(8,2),
    latency_p95_ms NUMERIC(8,2),
    -- Per-class and per-slice breakdown: altitude band, time of day, weather.
    per_class    JSONB NOT NULL DEFAULT '{}',
    per_slice    JSONB NOT NULL DEFAULT '{}',
    passed_gate  BOOLEAN NOT NULL DEFAULT FALSE,
    gate_notes   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evals_model ON ml.model_evals(model_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Media: video and stills. Blobs live in S3; this is the index.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS media.media (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES orgs.organizations(id) ON DELETE CASCADE,
    site_id      UUID REFERENCES orgs.sites(id) ON DELETE SET NULL,
    asset_id     UUID,
    flight_id    UUID,
    kind         VARCHAR(32) NOT NULL CHECK (kind IN ('frame', 'thumbnail', 'video', 'thermal')),
    storage_uri  TEXT NOT NULL,
    content_type VARCHAR(128),
    bytes        BIGINT,
    width        INT,
    height       INT,
    duration_s   NUMERIC(10,2),
    captured_at  TIMESTAMPTZ NOT NULL,
    -- Privacy: faces/plates blurred at the edge by default. Recorded for DPIA evidence.
    redacted     BOOLEAN NOT NULL DEFAULT FALSE,
    redaction_method VARCHAR(64),
    -- Retention enforcement, derived from org media_retention_days.
    delete_after TIMESTAMPTZ,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_media_flight  ON media.media(flight_id);
CREATE INDEX IF NOT EXISTS idx_media_org     ON media.media(org_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_expiry  ON media.media(delete_after) WHERE delete_after IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Alerts and audit: bring under tenancy
-- ---------------------------------------------------------------------------

ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS org_id  UUID REFERENCES orgs.organizations(id) ON DELETE CASCADE;
ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES orgs.sites(id) ON DELETE SET NULL;
ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS flight_id UUID;
ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS zone_id UUID;
ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS media_id UUID;
ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS model_version VARCHAR(64);
ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES auth.users(id);
ALTER TABLE alerts.alerts ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_alerts_org  ON alerts.alerts(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_site ON alerts.alerts(site_id, created_at DESC);

ALTER TABLE audit.command_log ADD COLUMN IF NOT EXISTS org_id  UUID;
ALTER TABLE audit.command_log ADD COLUMN IF NOT EXISTS site_id UUID;
ALTER TABLE audit.command_log ADD COLUMN IF NOT EXISTS flight_id UUID;
-- The full decision chain for a dispatch, for AI Act logging duties:
-- detection -> model -> confidence -> rule -> operator decision -> command -> outcome.
ALTER TABLE audit.command_log ADD COLUMN IF NOT EXISTS decision_chain JSONB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_audit_org ON audit.command_log(org_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Backfill: give existing rows a home so nothing orphans.
-- ---------------------------------------------------------------------------

INSERT INTO orgs.organizations (name, slug, plan)
VALUES ('Default Organization', 'default', 'trial')
ON CONFLICT (slug) DO NOTHING;

-- Promote each distinct legacy region_id to a site under the default org.
INSERT INTO orgs.sites (org_id, name, slug, legacy_region_id)
SELECT o.id, a.region_id, a.region_id, a.region_id
FROM (SELECT DISTINCT region_id FROM assets.assets WHERE region_id IS NOT NULL) a
CROSS JOIN (SELECT id FROM orgs.organizations WHERE slug = 'default') o
ON CONFLICT (org_id, slug) DO NOTHING;

UPDATE assets.assets a
SET org_id  = s.org_id,
    site_id = s.id
FROM orgs.sites s
WHERE s.legacy_region_id = a.region_id
  AND a.org_id IS NULL;
