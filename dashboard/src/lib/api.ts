/**
 * API client for defense backend. All requests use API_URL from env.
 * Auth: Bearer token from session/localStorage (set after login).
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("defense_token");
}

export async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...options.headers,
  };
  if (token) (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (!res.ok) throw new Error(await res.text().catch(() => res.statusText));
  return res.json() as Promise<T>;
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
