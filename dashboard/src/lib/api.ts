/**
 * API client. Attaches the access token, and on a 401 attempts one silent
 * refresh before surfacing the error, so a 15-minute access TTL is invisible
 * to the operator mid-shift.
 */

import { getAccessToken, refreshSession, clearSession } from "./auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit, retry: boolean): Promise<T> {
  const token = getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (res.status === 401 && retry) {
    if (await refreshSession()) return request<T>(path, options, false);
    clearSession();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError("Session expired", 401);
  }
  if (!res.ok) {
    throw new ApiError(await res.text().catch(() => res.statusText), res.status);
  }
  return res.json() as Promise<T>;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  return request<T>(path, options, true);
}

export async function listAssets(params?: { region_id?: string; status?: string }) {
  const q = new URLSearchParams(params as Record<string, string>).toString();
  return api<{ items: Asset[]; total: number }>(`/api/v1/assets${q ? `?${q}` : ""}`);
}

export async function getTelemetry(params: {
  asset_id?: string;
  region_id?: string;
  from_ts?: string;
  to_ts?: string;
  limit?: number;
}) {
  const q = new URLSearchParams(
    Object.fromEntries(
      Object.entries(params).filter(([, v]) => v != null) as [string, string][]
    )
  ).toString();
  return api<{ items: TelemetryPoint[]; total: number }>(
    `/api/v1/telemetry/aggregated${q ? `?${q}` : ""}`
  );
}

export async function listAlerts(params?: { region_id?: string; state?: string; limit?: number }) {
  const q = new URLSearchParams(params as Record<string, string>).toString();
  return api<{ items: Alert[]; total: number }>(`/api/v1/alerts${q ? `?${q}` : ""}`);
}

export async function emergencyStop(body: { asset_id?: string }) {
  return api<{ ok: boolean; scope: string; result: string }>(
    "/api/v1/control/emergency-stop",
    { method: "POST", body: JSON.stringify(body) }
  );
}

export async function sendCommand(body: {
  asset_id: string;
  intent: string;
  payload?: Record<string, unknown>;
  is_override?: boolean;
}) {
  return api<{ ok: boolean; asset_id: string; intent: string; result: string }>(
    "/api/v1/control/command",
    { method: "POST", body: JSON.stringify(body) }
  );
}

export type Asset = {
  id: string;
  name: string;
  asset_type: string;
  region_id: string;
  status: string;
  metadata: Record<string, unknown>;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type TelemetryPoint = {
  id: number;
  asset_id: string;
  bucket_ts: string;
  source: string;
  count_events: number;
  payload_sample: Record<string, unknown>;
  created_at: string;
};

export type Alert = {
  id: string;
  source: string;
  severity: string;
  title: string;
  body: string;
  asset_id: string | null;
  region_id: string | null;
  detection_id: string | null;
  state: string;
  metadata: Record<string, unknown>;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  created_at: string;
  updated_at: string;
};
