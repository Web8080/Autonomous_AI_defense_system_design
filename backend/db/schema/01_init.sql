-- Multi-tenant capable schema for defense system.
-- Run order: 01_init, then service-specific migrations.

CREATE SCHEMA IF NOT EXISTS assets;
CREATE SCHEMA IF NOT EXISTS telemetry;
CREATE SCHEMA IF NOT EXISTS alerts;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS auth;

-- Auth: users and roles (simplified; expand per your auth provider)
CREATE TABLE IF NOT EXISTS auth.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    role VARCHAR(64) NOT NULL,
    region_ids TEXT[],
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth.sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Assets: drones, vehicles, sensors
CREATE TABLE IF NOT EXISTS assets.assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    asset_type VARCHAR(64) NOT NULL,
    region_id VARCHAR(128) NOT NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'offline',
    metadata JSONB NOT NULL DEFAULT '{}',
    tags TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assets_region ON assets.assets(region_id);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets.assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets.assets(asset_type);

-- Telemetry: aggregated store (raw can go to Kafka/S3)
CREATE TABLE IF NOT EXISTS telemetry.aggregated (
    id BIGSERIAL PRIMARY KEY,
    asset_id UUID NOT NULL,
    bucket_ts TIMESTAMPTZ NOT NULL,
    source VARCHAR(64) NOT NULL,
    count_events INT NOT NULL DEFAULT 0,
    payload_sample JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telemetry_asset_ts ON telemetry.aggregated(asset_id, bucket_ts);
CREATE INDEX IF NOT EXISTS idx_telemetry_bucket_ts ON telemetry.aggregated(bucket_ts);

-- Alerts
CREATE TABLE IF NOT EXISTS alerts.alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(128) NOT NULL,
    severity VARCHAR(32) NOT NULL,
    title VARCHAR(512) NOT NULL,
    body TEXT,
    asset_id UUID,
    region_id VARCHAR(128),
    detection_id VARCHAR(255),
    state VARCHAR(32) NOT NULL DEFAULT 'new',
    metadata JSONB NOT NULL DEFAULT '{}',
    acknowledged_by UUID,
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_region ON alerts.alerts(region_id);
CREATE INDEX IF NOT EXISTS idx_alerts_state ON alerts.alerts(state);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts.alerts(created_at);

-- Audit: commands and overrides
CREATE TABLE IF NOT EXISTS audit.command_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID,
    intent VARCHAR(64) NOT NULL,
    issued_by VARCHAR(255) NOT NULL,
    is_override BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL DEFAULT '{}',
    result VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_asset ON audit.command_log(asset_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit.command_log(created_at);
