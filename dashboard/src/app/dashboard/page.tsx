"use client";

import { useEffect, useState } from "react";
import { listAssets, listAlerts, type Asset, type Alert } from "@/lib/api";

export default function DashboardMapPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([listAssets(), listAlerts({ limit: 20 })])
      .then(([a, b]) => {
        setAssets(a.items);
        setAlerts(b.items);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ padding: "2rem" }}>Loading...</p>;
  if (error) return <p style={{ padding: "2rem", color: "#f85149" }}>{error}</p>;

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Operations map</h1>
      <p>
        Placeholder for real-time telemetry map: asset positions, threat overlays, path planning.
        Connect to WebSocket or polling endpoint for live updates.
      </p>
      <section style={{ marginTop: "1rem", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
        <div>
          <h2>Assets ({assets.length})</h2>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {assets.slice(0, 10).map((a) => (
              <li key={a.id} style={{ padding: "0.25rem 0", borderBottom: "1px solid #21262d" }}>
                {a.name} | {a.asset_type} | {a.region_id} | {a.status}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2>Recent alerts ({alerts.length})</h2>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {alerts.slice(0, 10).map((a) => (
              <li key={a.id} style={{ padding: "0.25rem 0", borderBottom: "1px solid #21262d" }}>
                [{a.severity}] {a.title} | {a.state}
              </li>
            ))}
          </ul>
        </div>
      </section>
    </main>
  );
}
