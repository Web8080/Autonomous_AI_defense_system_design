"use client";

import { useEffect, useState } from "react";
import { listAlerts, type Alert } from "@/lib/api";

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    listAlerts({ limit: 50 })
      .then((r) => setAlerts(r.items))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ padding: "2rem" }}>Loading...</p>;
  if (error) return <p style={{ padding: "2rem", color: "#f85149" }}>{error}</p>;

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Alerts</h1>
      <p>AI detections and system events. Acknowledge or escalate from Control or Admin.</p>
      <ul style={{ listStyle: "none", padding: 0, marginTop: "1rem" }}>
        {alerts.map((a) => (
          <li
            key={a.id}
            style={{
              padding: "0.75rem",
              borderBottom: "1px solid #21262d",
              borderLeft: `3px solid ${a.severity === "critical" ? "#f85149" : a.severity === "high" ? "#d29922" : "#3fb950"}`,
            }}
          >
            <strong>{a.title}</strong> [{a.severity}] {a.state}
            <br />
            <small>{a.created_at} | {a.asset_id || a.region_id || "—"}</small>
          </li>
        ))}
      </ul>
    </main>
  );
}
