"use client";

import { useEffect, useState } from "react";
import { emergencyStop, listAssets, type Asset } from "@/lib/api";

export default function ControlPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");

  const loadAssets = () => {
    listAssets()
      .then((r) => setAssets(r.items))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadAssets();
  }, []);

  const handleEmergencyStop = (assetId?: string) => {
    setError("");
    setResult("");
    emergencyStop(assetId ? { asset_id: assetId } : {})
      .then((r) => setResult(`Emergency stop sent: ${r.scope} -> ${r.result}`))
      .catch((e) => setError(e.message));
  };

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Control and override</h1>
      <p>Manual override and emergency stop. All actions are logged.</p>
      <section style={{ marginTop: "1rem" }}>
        <h2>Emergency stop</h2>
        <button
          type="button"
          className="danger"
          onClick={() => handleEmergencyStop()}
          style={{ marginRight: "0.5rem" }}
        >
          Emergency stop (all assets)
        </button>
        {!loading &&
          assets.slice(0, 5).map((a) => (
            <button
              key={a.id}
              type="button"
              className="danger"
              onClick={() => handleEmergencyStop(a.id)}
              style={{ marginRight: "0.5rem", marginTop: "0.5rem" }}
            >
              Stop {a.name}
            </button>
          ))}
      </section>
      {result && <p style={{ color: "#3fb950", marginTop: "1rem" }}>{result}</p>}
      {error && <p style={{ color: "#f85149", marginTop: "1rem" }}>{error}</p>}
    </main>
  );
}
