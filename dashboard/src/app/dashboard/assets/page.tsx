"use client";

import { useEffect, useState } from "react";
import { listAssets, type Asset } from "@/lib/api";

export default function AssetsPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    listAssets()
      .then((r) => setAssets(r.items))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ padding: "2rem" }}>Loading...</p>;
  if (error) return <p style={{ padding: "2rem", color: "#f85149" }}>{error}</p>;

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Assets</h1>
      <p>Drones, ground vehicles, and sensors. Region-filtered for local operators.</p>
      <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "1rem" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid #30363d" }}>
            <th style={{ textAlign: "left", padding: "0.5rem" }}>Name</th>
            <th style={{ textAlign: "left", padding: "0.5rem" }}>Type</th>
            <th style={{ textAlign: "left", padding: "0.5rem" }}>Region</th>
            <th style={{ textAlign: "left", padding: "0.5rem" }}>Status</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((a) => (
            <tr key={a.id} style={{ borderBottom: "1px solid #21262d" }}>
              <td style={{ padding: "0.5rem" }}>{a.name}</td>
              <td style={{ padding: "0.5rem" }}>{a.asset_type}</td>
              <td style={{ padding: "0.5rem" }}>{a.region_id}</td>
              <td style={{ padding: "0.5rem" }}>{a.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
