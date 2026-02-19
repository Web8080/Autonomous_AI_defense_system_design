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
      let res = await fetch(`${API_URL}/api/v1/simulation/replay?name=${encodeURIComponent(name)}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        res = await fetch(`/replay/${encodeURIComponent(name)}`);
      }
      if (!res.ok) throw new Error(await res.text().catch(() => res.statusText));
      const data: Replay = await res.json();
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
    const scaleX = w / (bX - bx || 1);
    const scaleY = h / (bY - by || 1);
    const scale = Math.min(scaleX, scaleY);
    const ox = (w - (bX - bx) * scale) / 2;
    const oy = (h - (bY - by) * scale) / 2;
    const toCanvas = (x: number, y: number) => ({ x: ox + (x - bx) * scale, y: oy + (y - by) * scale });

    ctx.fillStyle = "#0f1419";
    ctx.fillRect(0, 0, w, h);

    if (overlays.zones && replay.scenario) {
      ctx.strokeStyle = "rgba(80,80,120,0.6)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(ox, oy, (bX - bx) * scale, (bY - by) * scale);
      ctx.setLineDash([]);
    }

    const pathPoints = replay.path?.length ? replay.path : (frame.assets?.length ? [[0, 100], [200, 100], [400, 105], [600, 98], [800, 102], [1000, 100]] : []);
    if (pathPoints.length) {
      ctx.strokeStyle = "#30363d";
      ctx.lineWidth = 4;
      ctx.beginPath();
      pathPoints.forEach((p, i) => {
        const { x, y } = toCanvas(p[0], p[1]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    if (overlays.trails) {
      frame.assets?.forEach((a) => {
        const key = a.id;
        if (!trailsRef.current[key]) trailsRef.current[key] = [];
        trailsRef.current[key].push([...a.position]);
        if (trailsRef.current[key].length > 80) trailsRef.current[key].shift();
        const trail = trailsRef.current[key];
        ctx.strokeStyle = a.asset_type === "drone" ? "rgba(88,166,255,0.5)" : "rgba(126,231,135,0.5)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        trail.forEach((pos, i) => {
          const { x, y } = toCanvas(pos[0], pos[1]);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
    }

    frame.assets?.forEach((a) => {
      const { x, y } = toCanvas(a.position[0], a.position[1]);
      ctx.fillStyle = a.asset_type === "drone" ? "#58a6ff" : "#7ee787";
      ctx.beginPath();
      ctx.arc(x, y, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#e6edf3";
      ctx.lineWidth = 1;
      ctx.stroke();
    });

    frame.threats_active?.forEach((th) => {
      const { x, y } = toCanvas(th.position[0], th.position[1]);
      ctx.fillStyle = "rgba(248,81,73,0.8)";
      ctx.beginPath();
      ctx.arc(x, y, 10, 0, Math.PI * 2);
      ctx.fill();
    });
  }, [replay, currentFrameIndex, overlays]);

  const frame = replay?.frames[currentFrameIndex];
  const timeSec = frame?.t ?? 0;

  return (
    <main style={{ padding: "1.5rem" }}>
      <h1>Scenario simulation</h1>
      <p>
        Run a scenario (e.g. railway line), then load the replay to see how agents move and respond over time. Play/pause and toggle overlays.
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
              Player trails
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
          </p>
          <div style={{ marginBottom: "0.5rem" }}>
            <input
              type="range"
              min={0}
              max={replay.frames.length - 1}
              value={currentFrameIndex}
              onChange={(e) => setCurrentFrameIndex(Number(e.target.value))}
              style={{ width: "100%", maxWidth: 600 }}
            />
          </div>
          <canvas
            ref={canvasRef}
            width={900}
            height={400}
            style={{ border: "1px solid #30363d", borderRadius: 8, background: "#0f1419" }}
          />
          {frame?.assets?.length ? (
            <ul style={{ listStyle: "none", padding: 0, marginTop: "0.5rem" }}>
              {frame.assets.map((a) => (
                <li key={a.id} style={{ padding: "0.25rem 0" }}>
                  {a.name}: [{a.position[0].toFixed(0)}, {a.position[1].toFixed(0)}] {a.status} {a.task || ""}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      )}

      {!replay && !loading && (
        <section style={{ marginTop: "1rem", padding: "1rem", border: "1px solid #21262d", borderRadius: 8 }}>
          <h2>How to watch the simulation</h2>
          <p><strong>Option A – no backend:</strong> Copy the replay file into the dashboard so the viewer can load it directly.</p>
          <ol style={{ marginLeft: "1.25rem" }}>
            <li>Create <code>dashboard/public/replay/</code> and copy <code>simulation/replay/railway_line_replay.json</code> into it.</li>
            <li>From repo root: <code>cd dashboard &amp;&amp; npm run dev</code>. Open <code>http://localhost:3000</code>, go to Simulation, click Load replay (uses <code>railway_line_replay.json</code> from <code>/replay/</code>).</li>
          </ol>
          <p><strong>Option B – via API:</strong> Set gateway <code>REPLAY_DIR</code> to the full path of <code>simulation/replay</code>, restart the api-gateway, then load the replay above (requires login with dev-token if using API).</p>
        </section>
      )}
    </main>
  );
}
