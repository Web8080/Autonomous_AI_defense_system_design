"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import {
  getSimulationLayers,
  listSimulationExercises,
  createSimulationExercise,
  getSimulationExercise,
  stopSimulationExercise,
  ingestSimulationFrame,
  type SimulationLayers,
  type SimulationStatus,
  type SimulationExerciseSummary,
} from "@/lib/api";
import BrowserWorld from "@/components/simulation/BrowserWorld";

type DemoEnvironment = {
  id: string;
  title: string;
  subtitle: string;
  video_id: string;
  public_path: string;
  blurb: string;
  available: boolean;
};

const FALLBACK_ENVIRONMENTS: DemoEnvironment[] = [
  {
    id: "railway-corridor",
    title: "Railway corridor",
    subtitle: "Overhead patrol along track and yard",
    video_id: "site-railway-0000001",
    public_path: "/demo-videos/railway-corridor.mp4",
    blurb: "Long-form aerial of a rail corridor — vehicles, pedestrians, and infrastructure in one pass.",
    available: true,
  },
  {
    id: "urban-sprawl",
    title: "Urban sprawl",
    subtitle: "Mixed traffic over city blocks",
    video_id: "site-aerial-0000001",
    public_path: "/demo-videos/urban-sprawl.mp4",
    blurb: "Dense urban FOV — cars, vans, and people under the drone path.",
    available: true,
  },
  {
    id: "site-perimeter",
    title: "Site perimeter",
    subtitle: "Facility edge and approach roads",
    video_id: "site-aerial-0000069",
    public_path: "/demo-videos/site-perimeter.mp4",
    blurb: "Shorter perimeter sweep for a tight live-detection demo.",
    available: true,
  },
];

function BoxOverlay({
  result,
}: {
  result: SimulationStatus["recent_results"][number] | null;
}) {
  if (!result) return null;
  const gts = result.gts ?? [];
  const dets = result.detections ?? [];
  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {gts.map((g, i) => {
        const [x0, y0, x1, y1] = g.bbox;
        return (
          <div
            key={`gt-${i}`}
            title={`GT ${g.class_name}`}
            style={{
              position: "absolute",
              left: `${x0 * 100}%`,
              top: `${y0 * 100}%`,
              width: `${(x1 - x0) * 100}%`,
              height: `${(y1 - y0) * 100}%`,
              border: "1.5px dashed #58a6ff",
              background: "rgba(88,166,255,0.08)",
              boxSizing: "border-box",
            }}
          />
        );
      })}
      {dets.map((d, i) => {
        const [x0, y0, x1, y1] = d.bbox;
        const col = d.matched ? "#3fb950" : "#f85149";
        const bg = d.matched ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.14)";
        return (
          <div
            key={`det-${i}`}
            style={{
              position: "absolute",
              left: `${x0 * 100}%`,
              top: `${y0 * 100}%`,
              width: `${(x1 - x0) * 100}%`,
              height: `${(y1 - y0) * 100}%`,
              border: `1.8px solid ${col}`,
              background: bg,
              boxSizing: "border-box",
            }}
          >
            <span
              style={{
                position: "absolute",
                top: -16,
                left: 0,
                fontSize: 10,
                fontWeight: 600,
                color: "#e6edf3",
                background: col,
                padding: "1px 4px",
                borderRadius: 3,
                whiteSpace: "nowrap",
                lineHeight: "12px",
              }}
            >
              {d.class_name} {d.confidence.toFixed(2)}
              {d.matched ? " ✓" : ""}
            </span>
          </div>
        );
      })}
      <div
        style={{
          position: "absolute",
          bottom: 10,
          left: 10,
          background: "rgba(0,0,0,0.75)",
          color: "#e6edf3",
          fontSize: 12,
          padding: "4px 8px",
          borderRadius: 4,
          lineHeight: 1.3,
        }}
      >
        TP {result.tp} · FP {result.fp} · FN {result.fn} · GT {result.gt}
        {result.latency_ms != null ? ` · ${result.latency_ms}ms` : ""}
      </div>
    </div>
  );
}

/** Investor stage: real MP4 playback with live YOLO boxes overlaid. */
function VideoStage({
  src,
  result,
  title,
  videoRef,
}: {
  src: string;
  result: SimulationStatus["recent_results"][number] | null;
  title: string;
  videoRef: RefObject<HTMLVideoElement | null>;
}) {
  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        border: "1px solid #30363d",
        borderRadius: 10,
        overflow: "hidden",
        background: "#010409",
        boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
      }}
    >
      <video
        key={src}
        ref={videoRef}
        src={src}
        controls
        playsInline
        preload="metadata"
        style={{ display: "block", width: "100%", maxHeight: "70vh", background: "#000" }}
      />
      <BoxOverlay result={result} />
      <div
        style={{
          position: "absolute",
          top: 10,
          left: 10,
          background: "rgba(0,0,0,0.7)",
          color: "#e6edf3",
          fontSize: 12,
          fontWeight: 600,
          padding: "4px 10px",
          borderRadius: 4,
          letterSpacing: 0.2,
        }}
      >
        {title} · live aerial
      </div>
    </div>
  );
}

function FrameOverlay({ frame, result }: { frame: { image_b64: string; width: number; height: number } | null; result: SimulationStatus["recent_results"][number] | null }) {
  if (!frame) return <div style={{ width: "100%", maxWidth: 640, height: 360, background: "#0d1117", border: "1px solid #30363d", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "#8b949e", fontSize: 13 }}>No frame yet — start an exercise or wait for Kafka.</div>;
  return (
    <div style={{ position: "relative", width: "100%", maxWidth: frame.width, border: "1px solid #30363d", borderRadius: 8, overflow: "hidden", background: "#0d1117" }}>
      <img src={`data:image/jpeg;base64,${frame.image_b64}`} alt="frame" style={{ display: "block", width: "100%", height: "auto" }} draggable={false} />
      <BoxOverlay result={result} />
    </div>
  );
}

type Asset = { id: string; name: string; asset_type: string; position: number[]; status: string; task?: string };
type Threat = { position: number[]; type: string };
type Frame = { t: number; assets: Asset[]; threats_active: Threat[]; decisions?: unknown[] };
type Replay = { scenario: string; bounds?: number[]; path?: number[][]; duration_sec: number; dt: number; frames: Frame[] };
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const REPLAYS: { label: string; file: string }[] = [{ label: "Railway line", file: "railway_line_replay.json" }];
type ViewMode = "map" | "drone";
function getTokenLegacy(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("dis_access_token") || localStorage.getItem("defense_token");
}

function LegacyReplayViewer() {
  const [replay, setReplay] = useState<Replay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [currentFrameIndex, setCurrentFrameIndex] = useState(0);
  const [overlays, setOverlays] = useState({ trails: true, zones: true });
  const [viewMode, setViewMode] = useState<ViewMode>("map");
  const [selectedReplay, setSelectedReplay] = useState(REPLAYS[0]?.file ?? "railway_line_replay.json");
  const [cvEnabled, setCvEnabled] = useState(true);
  const [cvStatus, setCvStatus] = useState<SimulationStatus | null>(null);
  const [cvId, setCvId] = useState<string | null>(null);
  const [cvError, setCvError] = useState("");
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trailsRef = useRef<Record<string, number[][]>>({});
  const cvIdRef = useRef<string | null>(null);

  useEffect(() => { loadReplay("railway_line_replay.json"); }, []);
  const loadReplay = useCallback(async (name: string) => {
    setLoading(true); setError("");
    try {
      const token = getTokenLegacy();
      const isLocal = typeof window !== "undefined" && (API_URL.includes("localhost") || API_URL.includes("127.0.0.1"));
      let res: Response;
      if (isLocal) {
        res = await fetch(`/replay/${encodeURIComponent(name)}`);
        if (!res.ok) res = await fetch(`${API_URL}/api/v1/simulation/replay?name=${encodeURIComponent(name)}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      } else {
        res = await fetch(`${API_URL}/api/v1/simulation/replay?name=${encodeURIComponent(name)}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
        if (!res.ok) res = await fetch(`/replay/${encodeURIComponent(name)}`);
      }
      if (!res.ok) throw new Error(await res.text().catch(() => res.statusText));
      const data: Replay = await res.json();
      if (!data.frames || !Array.isArray(data.frames)) throw new Error("Invalid replay: missing frames");
      setReplay(data); setCurrentFrameIndex(0); trailsRef.current = {};
    } catch (e) { setError(e instanceof Error ? e.message : "Failed to load replay"); setReplay(null); } finally { setLoading(false); }
  }, []);

  const stopCv = useCallback(async () => {
    const id = cvIdRef.current;
    if (!id) return;
    try { await stopSimulationExercise(id); } catch { /* ignore */ }
    cvIdRef.current = null;
    setCvId(null);
  }, []);

  const startCv = useCallback(async () => {
    if (!cvEnabled) return;
    setCvError("");
    try {
      await stopCv();
      const s = await createSimulationExercise({
        layer: 1,
        fps: 5,
        frames: 120,
        agent_replay: selectedReplay,
      });
      cvIdRef.current = s.id;
      setCvId(s.id);
      setCvStatus(s);
    } catch (e) {
      setCvError(e instanceof Error ? e.message : String(e));
    }
  }, [cvEnabled, selectedReplay, stopCv]);

  useEffect(() => {
    if (!replay || !playing) return;
    const id = setInterval(() => setCurrentFrameIndex((i) => (i + 1 >= replay.frames.length ? 0 : i + 1)), 200);
    return () => clearInterval(id);
  }, [replay, playing]);

  useEffect(() => {
    if (playing && cvEnabled) void startCv();
    else if (!playing) void stopCv();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, cvEnabled]);

  useEffect(() => {
    if (!cvId) return;
    const id = setInterval(async () => {
      try {
        const s = await getSimulationExercise(cvId);
        setCvStatus(s);
      } catch { /* ignore */ }
    }, 900);
    return () => clearInterval(id);
  }, [cvId]);

  useEffect(() => {
    if (!replay || !canvasRef.current) return;
    const frame = replay.frames[currentFrameIndex]; if (!frame) return;
    const canvas = canvasRef.current; const ctx = canvas.getContext("2d"); if (!ctx) return;
    const w = canvas.width, h = canvas.height;
    const bounds = replay.bounds ?? [0, 0, 1000, 200];
    const [bx, by, bX, bY] = bounds;
    const pathPoints = replay.path?.length ? replay.path : [[0, 100], [200, 100], [400, 105], [600, 98], [800, 102], [1000, 100]];
    const leadAsset = frame.assets?.[0];
    const leadPos = leadAsset?.position;
    const camX = viewMode === "drone" && leadPos != null ? (leadPos[0] ?? (bx + bX) / 2) : (bx + bX) / 2;
    const camY = viewMode === "drone" && leadPos != null ? (leadPos[1] ?? (by + bY) / 2) : (by + bY) / 2;
    const viewRadius = Math.max(viewMode === "drone" ? 220 : Math.max((bX - bx), (bY - by)) / 2, 1);
    const scale = (Math.min(w, h) * 0.85) / (2 * viewRadius);
    const ox = w / 2 - (camX - bx) * scale, oy = h / 2 - (camY - by) * scale;
    const toCanvas = (x: number, y: number) => ({ x: ox + (x - bx) * scale, y: oy + (y - by) * scale });
    ctx.fillStyle = viewMode === "map" ? "#0d1117" : "#0a0e14"; ctx.fillRect(0, 0, w, h);
    if (viewMode === "map") {
      const gridStep = 100; ctx.strokeStyle = "rgba(48,54,61,0.4)"; ctx.lineWidth = 1;
      for (let gx = Math.floor((bx - camX + viewRadius) / gridStep) * gridStep + camX - viewRadius; gx <= camX + viewRadius + gridStep; gx += gridStep) { const s = toCanvas(gx, by), e = toCanvas(gx, bY); ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(e.x, e.y); ctx.stroke(); }
      for (let gy = Math.floor((by - camY + viewRadius) / gridStep) * gridStep + camY - viewRadius; gy <= camY + viewRadius + gridStep; gy += gridStep) { const s = toCanvas(bx, gy), e = toCanvas(bX, gy); ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(e.x, e.y); ctx.stroke(); }
    }
    if (overlays.zones && replay.scenario && viewMode === "map") { ctx.strokeStyle = "rgba(80,80,120,0.5)"; ctx.lineWidth = 1; ctx.setLineDash([4, 4]); ctx.strokeRect(toCanvas(bx, by).x, toCanvas(bx, by).y, (bX - bx) * scale, (bY - by) * scale); ctx.setLineDash([]); }
    const trackOffset = 5;
    if (pathPoints.length >= 2) {
      for (const sign of [-1, 1]) {
        ctx.strokeStyle = "#3d3d3d"; ctx.lineWidth = viewMode === "map" ? 3 : 4; ctx.beginPath();
        for (let i = 0; i < pathPoints.length; i++) {
          const p = pathPoints[i]; const dx = i < pathPoints.length - 1 ? pathPoints[i + 1][0] - p[0] : (p[0] - pathPoints[i - 1][0]);
          const dy = i < pathPoints.length - 1 ? pathPoints[i + 1][1] - p[1] : (p[1] - pathPoints[i - 1][1]);
          const len = Math.sqrt(dx * dx + dy * dy) || 1; const nx = (-dy / len) * trackOffset * sign; const ny = (dx / len) * trackOffset * sign;
          const q = toCanvas(p[0] + nx, p[1] + ny); if (i === 0) ctx.moveTo(q.x, q.y); else ctx.lineTo(q.x, q.y);
        } ctx.stroke();
      }
      ctx.strokeStyle = "#6e7681"; ctx.lineWidth = viewMode === "map" ? 2 : 3; ctx.beginPath();
      pathPoints.forEach((p, i) => { const { x, y } = toCanvas(p[0], p[1]); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }); ctx.stroke();
      if (viewMode === "map") { const mid = pathPoints[Math.floor(pathPoints.length / 2)]; const { x, y } = toCanvas(mid[0], mid[1]); ctx.fillStyle = "rgba(110,118,129,0.8)"; ctx.font = "12px system-ui, sans-serif"; ctx.fillText("Railway", x - 24, y - 8); }
    }
    if (overlays.trails) {
      frame.assets?.forEach((a) => {
        const pos = a.position; if (!pos || pos.length < 2) return; const key = a.id;
        if (!trailsRef.current[key]) trailsRef.current[key] = []; trailsRef.current[key].push([Number(pos[0]), Number(pos[1])]);
        if (trailsRef.current[key].length > 80) trailsRef.current[key].shift();
        const trail = trailsRef.current[key]; ctx.strokeStyle = a.asset_type === "drone" ? "rgba(88,166,255,0.5)" : "rgba(126,231,135,0.5)"; ctx.lineWidth = 2; ctx.beginPath();
        trail.forEach((p, i) => { const { x, y } = toCanvas(p[0], p[1]); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }); ctx.stroke();
      });
    }
    frame.assets?.forEach((a) => {
      const pos = a.position; if (!pos || pos.length < 2) return; const { x, y } = toCanvas(Number(pos[0]), Number(pos[1]));
      ctx.fillStyle = a.asset_type === "drone" ? "#58a6ff" : "#7ee787"; ctx.beginPath(); ctx.arc(x, y, viewMode === "drone" ? 10 : 8, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#e6edf3"; ctx.lineWidth = 1; ctx.stroke();
      if (viewMode === "map" || (viewMode === "drone" && a === leadAsset)) { ctx.fillStyle = "#e6edf3"; ctx.font = "11px system-ui, sans-serif"; ctx.fillText(a.name, x + 12, y + 4); }
    });
    frame.threats_active?.forEach((th) => {
      const p = th?.position; if (!p || p.length < 2) return; const { x, y } = toCanvas(Number(p[0]), Number(p[1]));
      ctx.fillStyle = "rgba(248,81,73,0.9)"; ctx.beginPath(); ctx.arc(x, y, 10, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#f85149"; ctx.lineWidth = 1; ctx.stroke(); ctx.fillStyle = "#fff"; ctx.font = "10px system-ui, sans-serif"; ctx.fillText(th.type, x - 12, y - 12);
    });
    if (viewMode === "map") {
      const corner = toCanvas(bX - 20, by + 15); ctx.strokeStyle = "rgba(110,118,129,0.8)"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(corner.x, corner.y); ctx.lineTo(corner.x - 12, corner.y); ctx.lineTo(corner.x - 12, corner.y + 12); ctx.stroke(); ctx.fillStyle = "rgba(110,118,129,0.8)"; ctx.font = "10px system-ui, sans-serif"; ctx.fillText("N", corner.x - 18, corner.y + 6); ctx.fillStyle = "rgba(110,118,129,0.7)"; ctx.fillText("0    200m", corner.x - 80, corner.y + 28); ctx.beginPath(); ctx.moveTo(corner.x - 90, corner.y + 22); ctx.lineTo(corner.x - 10, corner.y + 22); ctx.stroke();
    }
    if (viewMode === "drone" && leadAsset) {
      ctx.fillStyle = "rgba(0,0,0,0.6)"; ctx.fillRect(8, h - 32, 200, 26); ctx.fillStyle = "#58a6ff"; ctx.font = "bold 12px system-ui, sans-serif"; ctx.fillText("Follow: " + leadAsset.name, 14, h - 14); ctx.fillStyle = "#8b949e"; ctx.font = "11px system-ui, sans-serif"; ctx.fillText("Track ahead", w / 2 - 28, 24);
    }
  }, [replay, currentFrameIndex, overlays, viewMode]);

  const frame = replay?.frames[currentFrameIndex]; const timeSec = frame?.t ?? 0;
  const cvFrame = cvStatus?.recent_frames?.[0] ?? null;
  const cvResult = cvFrame ? (cvStatus?.recent_results?.find((r) => r.frame_id === cvFrame.frame_id) ?? cvStatus?.recent_results?.[0] ?? null) : null;

  return (
    <div>
      <div style={{ marginBottom: "0.75rem", display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        <label>Replay: <select value={selectedReplay} onChange={(e) => { const f = e.target.value; setSelectedReplay(f); loadReplay(f); }} style={{ minWidth: 180, padding: "0.35rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}><option value="railway_line_replay.json">Railway line</option></select></label>
        <button type="button" onClick={() => loadReplay(selectedReplay)} style={{ padding: "0.4rem 0.8rem" }}>Reload</button>
        <label>View: <select value={viewMode} onChange={(e) => setViewMode(e.target.value as ViewMode)} style={{ minWidth: 120, padding: "0.35rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}><option value="map">Map</option><option value="drone">Drone fly-through</option></select></label>
        {replay && <><button type="button" onClick={() => setPlaying((p) => !p)}>{playing ? "Pause" : "Play"}</button><label><input type="checkbox" checked={overlays.trails} onChange={(e) => setOverlays((o) => ({ ...o, trails: e.target.checked }))} /> Trails</label><label><input type="checkbox" checked={overlays.zones} onChange={(e) => setOverlays((o) => ({ ...o, zones: e.target.checked }))} /> Zones</label><label><input type="checkbox" checked={cvEnabled} onChange={(e) => setCvEnabled(e.target.checked)} /> Live CV (YOLO)</label></>}
      </div>
      {loading && <p>Loading replay…</p>}
      {error && <p style={{ color: "#f85149" }}>{error}</p>}
      {cvError && <p style={{ color: "#f85149", fontSize: 13 }}>CV feed: {cvError}. Build MP4s with <code>python scripts/build_visdrone_mp4s.py</code> and ensure inference has MODEL_PATH set.</p>}
      {replay && (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: "1rem", alignItems: "start" }}>
          <div>
            <p style={{ marginBottom: "0.5rem", fontSize: 13, color: "#8b949e" }}>{replay.scenario} | Time {timeSec.toFixed(1)}s / {replay.duration_sec}s | Frame {currentFrameIndex + 1} / {replay.frames.length}</p>
            <input type="range" min={0} max={Math.max(0, replay.frames.length - 1)} value={Math.min(currentFrameIndex, Math.max(0, replay.frames.length - 1))} onChange={(e) => setCurrentFrameIndex(Number(e.target.value))} style={{ width: "100%", maxWidth: 600, marginBottom: "0.5rem" }} />
            <canvas ref={canvasRef} width={900} height={400} style={{ border: "1px solid #30363d", borderRadius: 8, background: "#0f1419", display: "block", maxWidth: "100%" }} />
          </div>
          <div>
            <p style={{ marginBottom: "0.5rem", fontSize: 13, color: "#8b949e" }}>
              Camera CV · {cvStatus ? `${cvStatus.scenario} · ${cvStatus.state}` : "idle"} · Kafka → YOLO
            </p>
            {cvEnabled ? (
              <FrameOverlay frame={cvFrame} result={cvResult} />
            ) : (
              <div style={{ padding: "2rem", border: "1px dashed #30363d", borderRadius: 8, color: "#8b949e", fontSize: 13 }}>Enable Live CV to run site MP4 through the trained model.</div>
            )}
            {cvStatus && (
              <p style={{ marginTop: "0.5rem", fontSize: 12, color: "#8b949e" }}>
                P {cvStatus.scoreboard.overall.precision.toFixed(2)} · R {cvStatus.scoreboard.overall.recall.toFixed(2)} · alerts {cvStatus.alerts.candidates}
              </p>
            )}
          </div>
        </div>
      )}
      {!replay && !loading && <p style={{ fontSize: 13, color: "#8b949e", marginTop: "0.5rem" }}>Copy <code>simulation/replay/railway_line_replay.json</code> to <code>dashboard/public/replay/</code> or set <code>REPLAY_DIR</code> on the gateway.</p>}
    </div>
  );
}




export default function SimulationViewerPage() {
  const [tab, setTab] = useState<"cv" | "legacy">("cv");
  // CV Lab state
  const [layers, setLayers] = useState<SimulationLayers | null>(null);
  const [layersError, setLayersError] = useState("");
  const [layer, setLayer] = useState<1 | 2 | 3>(1);
  const [scenarioL2, setScenarioL2] = useState("railway-yard");
  const [sequenceL1, setSequenceL1] = useState("");
  const [videoL1, setVideoL1] = useState("");
  const [envId, setEnvId] = useState(FALLBACK_ENVIRONMENTS[0].id);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [fps, setFps] = useState(5);
  const [frames, setFrames] = useState<number>(180);
  const [starting, setStarting] = useState(false);
  const [exercises, setExercises] = useState<SimulationExerciseSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [cvError, setCvError] = useState("");
  const lastIngestedRef = useRef<string | null>(null);
  const [lastIngestedGt, setLastIngestedGt] = useState<{ class_name: string; bbox: number[] }[]>([]);
  const l3GtRef = useRef<{ class_name: string; bbox: number[] }[]>([]);
  const demoVideoRef = useRef<HTMLVideoElement | null>(null);

  const environments: DemoEnvironment[] =
    layers?.["1"]?.environments?.length ? layers["1"].environments : FALLBACK_ENVIRONMENTS;
  const selectedEnv = environments.find((e) => e.id === envId) ?? environments[0] ?? FALLBACK_ENVIRONMENTS[0];

  const loadLayers = useCallback(async () => {
    try { const l = await getSimulationLayers(); setLayers(l); setLayersError("");
      if (l["2"]?.scenarios?.length && !l["2"].scenarios.includes(scenarioL2)) setScenarioL2(l["2"].scenarios[0]);
      const envs = l["1"]?.environments;
      if (envs?.length && !envs.some((e) => e.id === envId)) setEnvId(envs[0].id);
    } catch (e) { setLayersError(e instanceof Error ? e.message : String(e)); }
  }, [scenarioL2, envId]);
  const loadExercises = useCallback(async () => {
    try { const list = await listSimulationExercises(); setExercises(list); } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadLayers(); loadExercises(); const id = setInterval(loadExercises, 3000); return () => clearInterval(id); }, [loadLayers, loadExercises]);

  useEffect(() => {
    if (!activeId) { setStatus(null); return; }
    let cancelled = false;
    const tick = async () => {
      try { const s = await getSimulationExercise(activeId); if (!cancelled) setStatus(s); } catch (e) { if (!cancelled) setCvError(e instanceof Error ? e.message : String(e)); }
    };
    tick();
    const id = setInterval(tick, 900);
    return () => { cancelled = true; clearInterval(id); };
  }, [activeId]);

  useEffect(() => {
    if (layer === 1) setFps(5);
    else if (layer === 2) setFps(8);
    else setFps(5);
  }, [layer]);

  // Keep dropdown video in sync with selected demo environment
  useEffect(() => {
    if (layer === 1 && selectedEnv?.video_id) {
      setVideoL1(selectedEnv.video_id);
      setSequenceL1("");
    }
  }, [layer, selectedEnv?.video_id]);

  const startExercise = useCallback(async (opts?: { video?: string; frames?: number }) => {
    setStarting(true); setCvError("");
    try {
      const body: Record<string, unknown> = { layer, fps };
      if (layer === 1) {
        const vid = opts?.video ?? videoL1;
        if (vid) body["video"] = vid;
        else if (sequenceL1) body["sequence"] = sequenceL1;
        body["frames"] = opts?.frames ?? frames;
      } else if (layer === 2) {
        body["scenario"] = scenarioL2;
        body["frames"] = frames;
      }
      const s = await createSimulationExercise(body as never);
      setActiveId(s.id);
      setStatus(s);
      await loadExercises();
    } catch (e) { setCvError(e instanceof Error ? e.message : String(e)); } finally { setStarting(false); }
  }, [layer, fps, sequenceL1, videoL1, frames, scenarioL2, loadExercises]);

  const playDemo = useCallback(async () => {
    if (!selectedEnv?.available && selectedEnv?.available !== undefined && layers?.["1"]?.environments) {
      setCvError(`Video for ${selectedEnv.title} is not on this host.`);
      return;
    }
    setLayer(1);
    setVideoL1(selectedEnv.video_id);
    setSequenceL1("");
    // Start pipeline scoring in parallel with HTML5 playback
    await startExercise({ video: selectedEnv.video_id, frames });
    const el = demoVideoRef.current;
    if (el) {
      try {
        el.currentTime = 0;
        await el.play();
      } catch {
        /* autoplay may require a prior user gesture — controls remain */
      }
    }
  }, [selectedEnv, startExercise, frames, layers]);

  const stopActive = useCallback(async () => {
    if (!activeId) return;
    try {
      await stopSimulationExercise(activeId);
      const s = await getSimulationExercise(activeId);
      setStatus(s);
      await loadExercises();
    } catch (e) { setCvError(e instanceof Error ? e.message : String(e)); }
    const el = demoVideoRef.current;
    if (el) el.pause();
  }, [activeId, loadExercises]);

  const displayFrame = status?.recent_frames?.[0] ?? null;
  const displayResult = status?.recent_results?.[0] ?? null;

  const handleL3Frame = useCallback(async (b64: string, gt: { class_name: string; bbox: number[] }[], w: number, h: number) => {
    if (!activeId || !status || status.layer !== 3 || status.state !== "running") return;
    l3GtRef.current = gt;
    setLastIngestedGt(gt);
    try {
      const res = await ingestSimulationFrame(activeId, { image_b64: b64, gt, width: w, height: h });
      lastIngestedRef.current = res.frame_id;
    } catch { /* ignore */ }
  }, [activeId, status]);

  const l3Result = activeId && status?.layer === 3
    ? (status.recent_results.find((r) => r.frame_id === lastIngestedRef.current) ?? status.recent_results[0] ?? null)
    : null;

  const demoMode = layer === 1 && !sequenceL1;

  return (
    <main style={{ padding: "1.5rem", maxWidth: 1280, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Simulation</h1>
      <p style={{ color: "#8b949e", fontSize: 13, marginBottom: "1rem", lineHeight: 1.5 }}>
        Play <b>real aerial video</b> from a site environment while the trained model scores the same clip through the live pipeline.
      </p>

      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", borderBottom: "1px solid #21262d", paddingBottom: "0.5rem" }}>
        <button type="button" onClick={() => setTab("cv")} style={{ padding: "0.45rem 0.9rem", borderRadius: 6, border: "1px solid #30363d", background: tab === "cv" ? "#1f6feb" : "#21262d", color: "#e6edf3", fontWeight: tab === "cv" ? 600 : 400 }}>Site video + live CV</button>
        <button type="button" onClick={() => setTab("legacy")} style={{ padding: "0.45rem 0.9rem", borderRadius: 6, border: "1px solid #30363d", background: tab === "legacy" ? "#1f6feb" : "#21262d", color: "#e6edf3", fontWeight: tab === "legacy" ? 600 : 400 }}>Agent Replay (legacy)</button>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#8b949e", alignSelf: "center" }}>Investor demo · real footage · product path</span>
      </div>

      {tab === "legacy" ? (
        <LegacyReplayViewer />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {layersError && (
            <div style={{ padding: "0.6rem", background: "rgba(248,81,73,0.12)", border: "1px solid rgba(248,81,73,0.4)", borderRadius: 6, fontSize: 13, color: "#f85149" }}>
              Simulation service unavailable: {layersError}. Start it on <code>:8014</code> with inference + Kafka.
            </div>
          )}

          <section>
            <div style={{ fontSize: 12, fontWeight: 600, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: "0.55rem" }}>Environment</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: "0.75rem" }}>
              {environments.map((env) => {
                const active = env.id === selectedEnv.id;
                return (
                  <button
                    key={env.id}
                    type="button"
                    disabled={env.available === false}
                    onClick={() => { setEnvId(env.id); setLayer(1); setVideoL1(env.video_id); setSequenceL1(""); }}
                    style={{
                      textAlign: "left",
                      padding: "0.85rem 0.95rem",
                      borderRadius: 10,
                      border: `1.5px solid ${active ? "#1f6feb" : "#30363d"}`,
                      background: active ? "rgba(31,111,235,0.16)" : "#0d1117",
                      color: "#e6edf3",
                      opacity: env.available === false ? 0.45 : 1,
                      cursor: env.available === false ? "not-allowed" : "pointer",
                      minHeight: 110,
                    }}
                  >
                    <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>{env.title}</div>
                    <div style={{ fontSize: 12, color: "#8b949e", marginBottom: 8 }}>{env.subtitle}</div>
                    <div style={{ fontSize: 12, color: "#c9d1d9", lineHeight: 1.4 }}>{env.blurb}</div>
                  </button>
                );
              })}
            </div>
          </section>

          <section style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 300px", gap: "1rem", alignItems: "start" }}>
            <div>
              {demoMode ? (
                <>
                  <VideoStage
                    src={selectedEnv.public_path}
                    result={status?.layer === 1 ? displayResult : null}
                    title={selectedEnv.title}
                    videoRef={demoVideoRef}
                  />
                  <p style={{ fontSize: 11, color: "#6e7681", marginTop: "0.45rem" }}>
                    Real site MP4. Boxes are live detections from the trained model on the same footage.
                  </p>
                </>
              ) : status?.layer === 3 && status ? (
                <div style={{ position: "relative" }}>
                  <BrowserWorld active={status.state === "running"} onFrame={handleL3Frame} />
                </div>
              ) : (
                <>
                  <FrameOverlay frame={displayFrame} result={displayResult} />
                  <p style={{ fontSize: 11, color: "#6e7681", marginTop: "0.4rem" }}>Stills / procedural — pick an environment above for real video.</p>
                </>
              )}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              <div style={{ border: "1px solid #30363d", borderRadius: 10, background: "#0d1117", padding: "0.9rem" }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: "0.35rem" }}>{selectedEnv.title}</div>
                <div style={{ fontSize: 12, color: "#8b949e", lineHeight: 1.45, marginBottom: "0.85rem" }}>{selectedEnv.blurb}</div>
                <button
                  type="button"
                  onClick={playDemo}
                  disabled={starting || selectedEnv.available === false}
                  style={{
                    width: "100%",
                    padding: "0.7rem 1rem",
                    background: "#1f6feb",
                    color: "#fff",
                    border: "1px solid #1f6feb",
                    borderRadius: 8,
                    fontWeight: 700,
                    fontSize: 14,
                    opacity: starting ? 0.65 : 1,
                  }}
                >
                  {starting ? "Starting…" : "Play video + live detection"}
                </button>
                {activeId && (
                  <button type="button" onClick={stopActive} style={{ width: "100%", marginTop: 8, padding: "0.55rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 8 }}>
                    Stop
                  </button>
                )}
                {activeId && status && (
                  <div style={{ marginTop: 10, fontSize: 12, color: status.state === "running" ? "#3fb950" : "#8b949e", fontWeight: 600 }}>
                    {status.state === "running" ? "● detecting" : status.state} · {status.frame_index}{status.frames_total ? ` / ${status.frames_total}` : ""}
                  </div>
                )}
                {cvError && <div style={{ marginTop: 8, fontSize: 12, color: "#f85149", whiteSpace: "pre-wrap" }}>{cvError}</div>}
              </div>

              {status && status.layer === 1 && (
                <div style={{ border: "1px solid #30363d", borderRadius: 10, background: "#0d1117", padding: "0.75rem" }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: "0.5rem" }}>Live scoreboard</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.45rem", fontSize: 13 }}>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>Precision</div><div style={{ fontWeight: 700 }}>{status.scoreboard.overall.precision.toFixed(3)}</div></div>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>Recall</div><div style={{ fontWeight: 700 }}>{status.scoreboard.overall.recall.toFixed(3)}</div></div>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>Alerts</div><div style={{ fontWeight: 600 }}>{status.alerts.candidates}</div></div>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>Latency p50</div><div style={{ fontWeight: 600 }}>{status.latency_ms.p50 ?? "—"}ms</div></div>
                  </div>
                </div>
              )}
            </div>
          </section>

          <section style={{ border: "1px solid #21262d", borderRadius: 8, padding: "0.75rem", background: "#0d1117" }}>
            <button type="button" onClick={() => setShowAdvanced((v) => !v)} style={{ background: "transparent", border: "none", color: "#8b949e", fontSize: 12, fontWeight: 600, cursor: "pointer", padding: 0 }}>
              {showAdvanced ? "Hide" : "Show"} advanced (stills · procedural · 3D)
            </button>
            {showAdvanced && (
              <div style={{ marginTop: "0.75rem", display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
                  {[1, 2, 3].map((n) => {
                    const isActive = layer === n;
                    const label = n === 1 ? "① Real footage" : n === 2 ? "② Procedural" : "③ 3D world";
                    return (
                      <button key={n} type="button" onClick={() => setLayer(n as 1 | 2 | 3)} style={{ padding: "0.45rem 0.7rem", borderRadius: 20, border: `1.5px solid ${isActive ? "#1f6feb" : "#30363d"}`, background: isActive ? "rgba(31,111,235,0.18)" : "#21262d", color: isActive ? "#58a6ff" : "#e6edf3", fontSize: 13 }}>{label}</button>
                    );
                  })}
                </div>
                <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "end" }}>
                  {layer === 1 && layers && (
                    <>
                      <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>MP4<select value={videoL1} onChange={(e) => { setVideoL1(e.target.value); if (e.target.value) setSequenceL1(""); }} style={{ minWidth: 200, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}><option value="">— stills —</option>{(layers["1"].videos ?? []).map((v) => <option key={v.id} value={v.id}>{v.file}</option>)}</select></label>
                      <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>Sequence<select value={sequenceL1} onChange={(e) => { setSequenceL1(e.target.value); if (e.target.value) setVideoL1(""); }} style={{ minWidth: 180, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}><option value="">All</option>{(layers["1"].sequences ?? []).map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
                    </>
                  )}
                  {layer === 2 && layers && (
                    <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>Scenario<select value={scenarioL2} onChange={(e) => setScenarioL2(e.target.value)} style={{ minWidth: 180, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}>{(layers["2"]?.scenarios ?? []).map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
                  )}
                  <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>FPS<input type="number" min={1} max={15} value={fps} onChange={(e) => setFps(Math.max(1, Math.min(15, Number(e.target.value) || 5)))} style={{ width: 70, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }} /></label>
                  {layer !== 3 && <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>Frames<input type="number" min={10} max={2000} value={frames} onChange={(e) => setFrames(Math.max(10, Math.min(2000, Number(e.target.value) || 60)))} style={{ width: 80, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }} /></label>}
                  <button type="button" onClick={() => startExercise()} disabled={starting} style={{ padding: "0.5rem 0.9rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}>{starting ? "Starting…" : `Start L${layer}`}</button>
                </div>
              </div>
            )}
          </section>

          <section style={{ border: "1px solid #21262d", borderRadius: 8, padding: "0.75rem", background: "#0d1117" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
              <h3 style={{ fontSize: 13, fontWeight: 600, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.4, margin: 0 }}>Recent exercises</h3>
              <button type="button" onClick={loadExercises} style={{ marginLeft: "auto", fontSize: 12, padding: "0.3rem 0.6rem" }}>Refresh</button>
            </div>
            {exercises.length === 0 ? (
              <p style={{ fontSize: 13, color: "#6e7681" }}>No exercises yet — pick an environment and play.</p>
            ) : (
              <div style={{ display: "flex", gap: "0.5rem", overflowX: "auto", paddingBottom: "0.25rem" }}>
                {exercises.slice(0, 12).map((ex) => (
                  <button key={ex.id} type="button" onClick={() => setActiveId(ex.id)} style={{ flex: "0 0 auto", textAlign: "left", padding: "0.55rem 0.7rem", minWidth: 170, borderRadius: 6, border: `1.5px solid ${activeId === ex.id ? "#1f6feb" : "#30363d"}`, background: activeId === ex.id ? "rgba(31,111,235,0.12)" : "#161b22", color: "#e6edf3" }}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>L{ex.layer} · {ex.scenario} <span style={{ fontWeight: 400, color: ex.state === "running" ? "#3fb950" : "#8b949e" }}>· {ex.state}</span></div>
                    <div style={{ fontSize: 11, color: "#8b949e", marginTop: 2 }}>{ex.frame_index}{ex.frames_total ? `/${ex.frames_total}` : ""} · P {ex.precision.toFixed(2)}</div>
                  </button>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
