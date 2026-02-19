"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Asset = { id: string; name: string; asset_type: string; position: number[]; status: string; task?: string };
type Threat = { position: number[]; type: string };
type Frame = { t: number; assets: Asset[]; threats_active: Threat[]; decisions?: unknown[] };
type Replay = { scenario: string; bounds?: number[]; path?: number[][]; duration_sec: number; dt: number; frames: Frame[] };

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const REPLAYS: { label: string; file: string }[] = [
  { label: "Railway line", file: "railway_line_replay.json" },
];

type ViewMode = "map" | "drone";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("defense_token");
}

export default function SimulationViewerPage() {
  const [replay, setReplay] = useState<Replay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [currentFrameIndex, setCurrentFrameIndex] = useState(0);
  const [overlays, setOverlays] = useState({ trails: true, zones: true });
  const [viewMode, setViewMode] = useState<ViewMode>("map");
  const [selectedReplay, setSelectedReplay] = useState(REPLAYS[0]?.file ?? "railway_line_replay.json");
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trailsRef = useRef<Record<string, number[][]>>({});
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    loadReplay("railway_line_replay.json");
  }, []);

  const loadReplay = useCallback(async (name: string) => {
    setLoading(true);
    setError("");
    try {
      const token = getToken();
      const isLocal = typeof window !== "undefined" && (API_URL.includes("localhost") || API_URL.includes("127.0.0.1"));
      let res: Response;
      if (isLocal) {
        res = await fetch(`/replay/${encodeURIComponent(name)}`);
        if (!res.ok) {
          res = await fetch(`${API_URL}/api/v1/simulation/replay?name=${encodeURIComponent(name)}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
        }
      } else {
        res = await fetch(`${API_URL}/api/v1/simulation/replay?name=${encodeURIComponent(name)}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) res = await fetch(`/replay/${encodeURIComponent(name)}`);
      }
      if (!res.ok) throw new Error(await res.text().catch(() => res.statusText));
      const data: Replay = await res.json();
      if (!data.frames || !Array.isArray(data.frames)) throw new Error("Invalid replay: missing frames");
      setReplay(data);
      setCurrentFrameIndex(0);
      trailsRef.current = {};
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load replay");
      setReplay(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!replay || !playing) return;
    const id = setInterval(() => {
      setCurrentFrameIndex((i) => (i + 1 >= replay.frames.length ? 0 : i + 1));
    }, 200);
    return () => clearInterval(id);
  }, [replay, playing]);

  useEffect(() => {
    if (!replay || !canvasRef.current) return;
    const frame = replay.frames[currentFrameIndex];
    if (!frame) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const w = canvas.width;
    const h = canvas.height;
    const bounds = replay.bounds ?? [0, 0, 1000, 200];
    const [bx, by, bX, bY] = bounds;
    const pathPoints = replay.path?.length ? replay.path : [[0, 100], [200, 100], [400, 105], [600, 98], [800, 102], [1000, 100]];

    const leadAsset = frame.assets?.[0];
    const leadPos = leadAsset?.position;
    const camX = viewMode === "drone" && leadPos != null ? (leadPos[0] ?? (bx + bX) / 2) : (bx + bX) / 2;
    const camY = viewMode === "drone" && leadPos != null ? (leadPos[1] ?? (by + bY) / 2) : (by + bY) / 2;
    const viewRadius = Math.max(viewMode === "drone" ? 220 : Math.max((bX - bx), (bY - by)) / 2, 1);
    const scale = (Math.min(w, h) * 0.85) / (2 * viewRadius);
    const ox = w / 2 - (camX - bx) * scale;
    const oy = h / 2 - (camY - by) * scale;
    const toCanvas = (x: number, y: number) => ({ x: ox + (x - bx) * scale, y: oy + (y - by) * scale });

    ctx.fillStyle = viewMode === "map" ? "#0d1117" : "#0a0e14";
    ctx.fillRect(0, 0, w, h);

    if (viewMode === "map") {
      const gridStep = 100;
      ctx.strokeStyle = "rgba(48,54,61,0.4)";
      ctx.lineWidth = 1;
      for (let gx = Math.floor((bx - camX + viewRadius) / gridStep) * gridStep + camX - viewRadius; gx <= camX + viewRadius + gridStep; gx += gridStep) {
        const start = toCanvas(gx, by);
        const end = toCanvas(gx, bY);
        ctx.beginPath();
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
      }
      for (let gy = Math.floor((by - camY + viewRadius) / gridStep) * gridStep + camY - viewRadius; gy <= camY + viewRadius + gridStep; gy += gridStep) {
        const start = toCanvas(bx, gy);
        const end = toCanvas(bX, gy);
        ctx.beginPath();
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
      }
    }

    if (overlays.zones && replay.scenario && viewMode === "map") {
      ctx.strokeStyle = "rgba(80,80,120,0.5)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(toCanvas(bx, by).x, toCanvas(bx, by).y, (bX - bx) * scale, (bY - by) * scale);
      ctx.setLineDash([]);
    }

    const trackOffset = 5;
    if (pathPoints.length >= 2) {
      for (const sign of [-1, 1]) {
        ctx.strokeStyle = "#3d3d3d";
        ctx.lineWidth = viewMode === "map" ? 3 : 4;
        ctx.beginPath();
        for (let i = 0; i < pathPoints.length; i++) {
          const p = pathPoints[i];
          const dx = i < pathPoints.length - 1 ? pathPoints[i + 1][0] - p[0] : (p[0] - pathPoints[i - 1][0]);
          const dy = i < pathPoints.length - 1 ? pathPoints[i + 1][1] - p[1] : (p[1] - pathPoints[i - 1][1]);
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          const nx = (-dy / len) * trackOffset * sign;
          const ny = (dx / len) * trackOffset * sign;
          const q = toCanvas(p[0] + nx, p[1] + ny);
          if (i === 0) ctx.moveTo(q.x, q.y);
          else ctx.lineTo(q.x, q.y);
        }
        ctx.stroke();
      }
      ctx.strokeStyle = "#6e7681";
      ctx.lineWidth = viewMode === "map" ? 2 : 3;
      ctx.beginPath();
      pathPoints.forEach((p, i) => {
        const { x, y } = toCanvas(p[0], p[1]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      if (viewMode === "map") {
        const mid = pathPoints[Math.floor(pathPoints.length / 2)];
        const { x, y } = toCanvas(mid[0], mid[1]);
        ctx.fillStyle = "rgba(110,118,129,0.8)";
        ctx.font = "12px system-ui, sans-serif";
        ctx.fillText("Railway", x - 24, y - 8);
      }
    }

    if (overlays.trails) {
      frame.assets?.forEach((a) => {
        const pos = a.position;
        if (!pos || pos.length < 2) return;
        const key = a.id;
        if (!trailsRef.current[key]) trailsRef.current[key] = [];
        trailsRef.current[key].push([Number(pos[0]), Number(pos[1])]);
        if (trailsRef.current[key].length > 80) trailsRef.current[key].shift();
        const trail = trailsRef.current[key];
        ctx.strokeStyle = a.asset_type === "drone" ? "rgba(88,166,255,0.5)" : "rgba(126,231,135,0.5)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        trail.forEach((p, i) => {
          const { x, y } = toCanvas(p[0], p[1]);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
    }

    frame.assets?.forEach((a) => {
      const pos = a.position;
      if (!pos || pos.length < 2) return;
      const { x, y } = toCanvas(Number(pos[0]), Number(pos[1]));
      ctx.fillStyle = a.asset_type === "drone" ? "#58a6ff" : "#7ee787";
      ctx.beginPath();
      ctx.arc(x, y, viewMode === "drone" ? 10 : 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#e6edf3";
      ctx.lineWidth = 1;
      ctx.stroke();
      if (viewMode === "map" || (viewMode === "drone" && a === leadAsset)) {
        ctx.fillStyle = "#e6edf3";
        ctx.font = "11px system-ui, sans-serif";
        ctx.fillText(a.name, x + 12, y + 4);
      }
    });

    frame.threats_active?.forEach((th) => {
      const p = th?.position;
      if (!p || p.length < 2) return;
      const { x, y } = toCanvas(Number(p[0]), Number(p[1]));
      ctx.fillStyle = "rgba(248,81,73,0.9)";
      ctx.beginPath();
      ctx.arc(x, y, 10, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#f85149";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#fff";
      ctx.font = "10px system-ui, sans-serif";
      ctx.fillText(th.type, x - 12, y - 12);
    });

    if (viewMode === "map") {
      const corner = toCanvas(bX - 20, by + 15);
      ctx.strokeStyle = "rgba(110,118,129,0.8)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(corner.x, corner.y);
      ctx.lineTo(corner.x - 12, corner.y);
      ctx.lineTo(corner.x - 12, corner.y + 12);
      ctx.stroke();
      ctx.fillStyle = "rgba(110,118,129,0.8)";
      ctx.font = "10px system-ui, sans-serif";
      ctx.fillText("N", corner.x - 18, corner.y + 6);
      ctx.fillStyle = "rgba(110,118,129,0.7)";
      ctx.fillText("0    200m", corner.x - 80, corner.y + 28);
      ctx.beginPath();
      ctx.moveTo(corner.x - 90, corner.y + 22);
      ctx.lineTo(corner.x - 10, corner.y + 22);
      ctx.stroke();
    }

    if (viewMode === "drone" && leadAsset) {
      ctx.fillStyle = "rgba(0,0,0,0.6)";
      ctx.fillRect(8, h - 32, 200, 26);
      ctx.fillStyle = "#58a6ff";
      ctx.font = "bold 12px system-ui, sans-serif";
      ctx.fillText("Follow: " + leadAsset.name, 14, h - 14);
      ctx.fillStyle = "#8b949e";
      ctx.font = "11px system-ui, sans-serif";
      ctx.fillText("Track ahead", w / 2 - 28, 24);
    }
  }, [replay, currentFrameIndex, overlays, viewMode]);

  const frame = replay?.frames[currentFrameIndex];
  const timeSec = frame?.t ?? 0;

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Scenario simulation</h1>
      <p>
        Map view: top-down with grid and railway. Drone view: camera follows the lead asset so you see the track as it flies along the railway.
      </p>

      <section style={{ marginBottom: "1rem", display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
        <label>
          Replay:{" "}
          <select
            value={selectedReplay}
            onChange={(e) => {
              const file = e.target.value;
              setSelectedReplay(file);
              loadReplay(file);
            }}
            style={{ minWidth: 180 }}
          >
            {REPLAYS.map((r) => (
              <option key={r.file} value={r.file}>{r.label}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => loadReplay(selectedReplay)}>
          Reload
        </button>
        <label>
          View:{" "}
          <select value={viewMode} onChange={(e) => setViewMode(e.target.value as ViewMode)} style={{ minWidth: 140 }}>
            <option value="map">Map</option>
            <option value="drone">Drone fly-through</option>
          </select>
        </label>
        {replay && (
          <>
            <button type="button" onClick={() => setPlaying((p) => !p)}>
              {playing ? "Pause" : "Play"}
            </button>
            <label>
              <input
                type="checkbox"
                checked={overlays.trails}
                onChange={(e) => setOverlays((o) => ({ ...o, trails: e.target.checked }))}
              />
              Trails
            </label>
            <label>
              <input
                type="checkbox"
                checked={overlays.zones}
                onChange={(e) => setOverlays((o) => ({ ...o, zones: e.target.checked }))}
              />
              Zones
            </label>
          </>
        )}
      </section>

      {loading && <p>Loading replay...</p>}
      {error && (
        <p style={{ color: "#f85149" }}>
          {error}. If using dashboard only (no API), ensure <code>dashboard/public/replay/railway_line_replay.json</code> exists.
        </p>
      )}
      {replay && (
        <section>
          <p style={{ marginBottom: "0.5rem" }}>
            {replay.scenario} | Time: {timeSec.toFixed(1)}s / {replay.duration_sec}s | Frame {currentFrameIndex + 1} / {replay.frames.length}
            {viewMode === "drone" && frame?.assets?.[0] && (
              <> | Following: {frame.assets[0].name}</>
            )}
          </p>
          <div style={{ marginBottom: "0.5rem" }}>
            <input
              type="range"
              min={0}
              max={Math.max(0, replay.frames.length - 1)}
              value={Math.min(currentFrameIndex, Math.max(0, replay.frames.length - 1))}
              onChange={(e) => setCurrentFrameIndex(Number(e.target.value))}
              style={{ width: "100%", maxWidth: 600 }}
            />
          </div>
          <canvas
            ref={canvasRef}
            width={900}
            height={400}
            style={{ border: "1px solid #30363d", borderRadius: 8, background: "#0f1419", display: "block" }}
          />
          {frame?.assets?.length ? (
            <ul style={{ listStyle: "none", padding: 0, marginTop: "0.5rem" }}>
              {frame.assets.map((a) => (
                <li key={a.id} style={{ padding: "0.25rem 0" }}>
                  {a.name}: [{a.position?.[0] ?? 0}, {a.position?.[1] ?? 0}] {a.status} {a.task || ""}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      )}

      {!replay && !loading && (
        <section style={{ marginTop: "1rem", padding: "1rem", border: "1px solid #21262d", borderRadius: 8 }}>
          <h2>How to watch</h2>
          <p>Run <code>./scripts/launch_dashboard.sh</code> from repo root, then open Simulation. Use <strong>Map</strong> for top-down or <strong>Drone fly-through</strong> to follow the lead asset along the railway.</p>
        </section>
      )}
    </main>
  );
}
