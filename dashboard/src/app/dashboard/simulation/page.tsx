"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

// ---------- legacy agent replay viewer (preserved) ----------
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
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trailsRef = useRef<Record<string, number[][]>>({});
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
  useEffect(() => {
    if (!replay || !playing) return;
    const id = setInterval(() => setCurrentFrameIndex((i) => (i + 1 >= replay.frames.length ? 0 : i + 1)), 200);
    return () => clearInterval(id);
  }, [replay, playing]);
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
  return (
    <div>
      <div style={{ marginBottom: "0.75rem", display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        <label>Replay: <select value={selectedReplay} onChange={(e) => { const f = e.target.value; setSelectedReplay(f); loadReplay(f); }} style={{ minWidth: 180, padding: "0.35rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}><option value="railway_line_replay.json">Railway line</option></select></label>
        <button type="button" onClick={() => loadReplay(selectedReplay)} style={{ padding: "0.4rem 0.8rem" }}>Reload</button>
        <label>View: <select value={viewMode} onChange={(e) => setViewMode(e.target.value as ViewMode)} style={{ minWidth: 120, padding: "0.35rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}><option value="map">Map</option><option value="drone">Drone fly-through</option></select></label>
        {replay && <><button type="button" onClick={() => setPlaying((p) => !p)}>{playing ? "Pause" : "Play"}</button><label><input type="checkbox" checked={overlays.trails} onChange={(e) => setOverlays((o) => ({ ...o, trails: e.target.checked }))} /> Trails</label><label><input type="checkbox" checked={overlays.zones} onChange={(e) => setOverlays((o) => ({ ...o, zones: e.target.checked }))} /> Zones</label></>}
      </div>
      {loading && <p>Loading replay…</p>}
      {error && <p style={{ color: "#f85149" }}>{error}</p>}
      {replay && (
        <div>
          <p style={{ marginBottom: "0.5rem", fontSize: 13, color: "#8b949e" }}>{replay.scenario} | Time {timeSec.toFixed(1)}s / {replay.duration_sec}s | Frame {currentFrameIndex + 1} / {replay.frames.length}</p>
          <input type="range" min={0} max={Math.max(0, replay.frames.length - 1)} value={Math.min(currentFrameIndex, Math.max(0, replay.frames.length - 1))} onChange={(e) => setCurrentFrameIndex(Number(e.target.value))} style={{ width: "100%", maxWidth: 600, marginBottom: "0.5rem" }} />
          <canvas ref={canvasRef} width={900} height={400} style={{ border: "1px solid #30363d", borderRadius: 8, background: "#0f1419", display: "block", maxWidth: "100%" }} />
        </div>
      )}
      {!replay && !loading && <p style={{ fontSize: 13, color: "#8b949e", marginTop: "0.5rem" }}>Copy <code>simulation/replay/railway_line_replay.json</code> to <code>dashboard/public/replay/</code> or set <code>REPLAY_DIR</code> on the gateway.</p>}
    </div>
  );
}

// ---------- CV Lab ----------
function FrameOverlay({ frame, result }: { frame: { image_b64: string; width: number; height: number } | null; result: SimulationStatus["recent_results"][number] | null }) {
  if (!frame) return <div style={{ width: "100%", maxWidth: 640, height: 360, background: "#0d1117", border: "1px solid #30363d", borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "#8b949e", fontSize: 13 }}>No frame yet — start an exercise or wait for Kafka.</div>;
  const gts = result?.gts ?? [];
  const dets = result?.detections ?? [];
  return (
    <div style={{ position: "relative", width: "100%", maxWidth: frame.width, border: "1px solid #30363d", borderRadius: 8, overflow: "hidden", background: "#0d1117" }}>
      <img src={`data:image/jpeg;base64,${frame.image_b64}`} alt="frame" style={{ display: "block", width: "100%", height: "auto" }} draggable={false} />
      <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
        {gts.map((g, i) => {
          const [x0, y0, x1, y1] = g.bbox;
          return <div key={`gt-${i}`} title={`GT ${g.class_name}`} style={{ position: "absolute", left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, border: "1.5px dashed #58a6ff", background: "rgba(88,166,255,0.08)", boxSizing: "border-box" }} />;
        })}
        {dets.map((d, i) => {
          const [x0, y0, x1, y1] = d.bbox;
          const col = d.matched ? "#3fb950" : "#f85149";
          const bg = d.matched ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.14)";
          return (
            <div key={`det-${i}`} style={{ position: "absolute", left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, border: `1.8px solid ${col}`, background: bg, boxSizing: "border-box" }}>
              <span style={{ position: "absolute", top: -16, left: 0, fontSize: 10, fontWeight: 600, color: "#e6edf3", background: col, padding: "1px 4px", borderRadius: 3, whiteSpace: "nowrap", lineHeight: "12px" }}>{d.class_name} {d.confidence.toFixed(2)}{d.matched ? " ✓" : ""}</span>
            </div>
          );
        })}
      </div>
      {result && <div style={{ position: "absolute", bottom: 6, left: 6, background: "rgba(0,0,0,0.72)", color: "#e6edf3", fontSize: 11, padding: "3px 6px", borderRadius: 4, lineHeight: 1 }}>TP {result.tp} · FP {result.fp} · FN {result.fn} · GT {result.gt}{result.latency_ms != null ? ` · ${result.latency_ms}ms` : ""}</div>}
    </div>
  );
}

function L3Overlay({ gt, detections, width, height }: { gt: { class_name: string; bbox: number[] }[]; detections: SimulationStatus["recent_results"][number]["detections"]; width: number; height: number }) {
  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {gt.map((g, i) => { const [x0, y0, x1, y1] = g.bbox; return <div key={`gt-${i}`} style={{ position: "absolute", left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, border: "1.5px dashed #58a6ff", background: "rgba(88,166,255,0.08)", boxSizing: "border-box" }} />; })}
      {detections.map((d, i) => { const [x0, y0, x1, y1] = d.bbox; const col = d.matched ? "#3fb950" : "#f85149"; const bg = d.matched ? "rgba(63,185,80,0.14)" : "rgba(248,81,73,0.16)"; return <div key={`d-${i}`} style={{ position: "absolute", left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, border: `1.8px solid ${col}`, background: bg, boxSizing: "border-box" }}><span style={{ position: "absolute", top: -16, left: 0, fontSize: 10, fontWeight: 600, color: "#e6edf3", background: col, padding: "1px 4px", borderRadius: 3, whiteSpace: "nowrap" }}>{d.class_name} {d.confidence.toFixed(2)}{d.matched ? " ✓" : ""}</span></div>; })}
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
  const [fps, setFps] = useState(5);
  const [frames, setFrames] = useState<number>(60);
  const [starting, setStarting] = useState(false);
  const [exercises, setExercises] = useState<SimulationExerciseSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [cvError, setCvError] = useState("");
  const lastIngestedRef = useRef<string | null>(null);
  const [lastIngestedGt, setLastIngestedGt] = useState<{ class_name: string; bbox: number[] }[]>([]);
  // keep track of latest l3 gt for overlay when status hasn't caught up yet
  const l3GtRef = useRef<{ class_name: string; bbox: number[] }[]>([]);

  const loadLayers = useCallback(async () => {
    try { const l = await getSimulationLayers(); setLayers(l); setLayersError("");
      if (l["2"]?.scenarios?.length && !l["2"].scenarios.includes(scenarioL2)) setScenarioL2(l["2"].scenarios[0]);
    } catch (e) { setLayersError(e instanceof Error ? e.message : String(e)); }
  }, [scenarioL2]);
  const loadExercises = useCallback(async () => {
    try { const list = await listSimulationExercises(); setExercises(list); if (!activeId && list.length) { /* don't auto-select */ } } catch { /* ignore */ }
  }, [activeId]);

  useEffect(() => { loadLayers(); loadExercises(); const id = setInterval(loadExercises, 3000); return () => clearInterval(id); }, [loadLayers, loadExercises]);

  // poll active exercise status
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

  // keep fps in sync with layer defaults
  useEffect(() => {
    if (layer === 1) setFps(5);
    else if (layer === 2) setFps(8);
    else setFps(5);
  }, [layer]);

  const startExercise = useCallback(async () => {
    setStarting(true); setCvError("");
    try {
      const body: Record<string, unknown> = { layer, fps };
      if (layer === 1) {
        if (sequenceL1) body["sequence"] = sequenceL1;
        body["frames"] = frames;
      } else if (layer === 2) {
        body["scenario"] = scenarioL2;
        body["frames"] = frames;
      } else {
        // layer 3: fps only, backend creates ingest-only exercise
      }
      const s = await createSimulationExercise(body as never);
      setActiveId(s.id);
      setStatus(s);
      await loadExercises();
    } catch (e) { setCvError(e instanceof Error ? e.message : String(e)); } finally { setStarting(false); }
  }, [layer, fps, sequenceL1, frames, scenarioL2, loadExercises]);

  const stopActive = useCallback(async () => {
    if (!activeId) return;
    try { await stopSimulationExercise(activeId); const s = await getSimulationExercise(activeId); setStatus(s); await loadExercises(); } catch (e) { setCvError(e instanceof Error ? e.message : String(e)); }
  }, [activeId, loadExercises]);

  // derive display frame for L1/L2 (latest), and for L3 overlay
  const displayFrame = status?.recent_frames?.[0] ?? null;
  const displayResult = displayFrame ? (status?.recent_results?.find((r) => r.frame_id === displayFrame.frame_id) ?? status?.recent_results?.[0] ?? null) : null;

  // L3 ingest handler
  const handleL3Frame = useCallback(async (b64: string, gt: { class_name: string; bbox: number[] }[], w: number, h: number) => {
    if (!activeId || !status || status.layer !== 3 || status.state !== "running") return;
    l3GtRef.current = gt;
    setLastIngestedGt(gt);
    try {
      const res = await ingestSimulationFrame(activeId, { image_b64: b64, gt, width: w, height: h });
      lastIngestedRef.current = res.frame_id;
    } catch {
      // ingest can fail if exercise stopped or Kafka down – surface once
    }
  }, [activeId, status]);

  // for L3 overlay, prefer the detection result matching the last ingested frame; fall back to latest result
  const l3Result = activeId && status?.layer === 3
    ? (status.recent_results.find((r) => r.frame_id === lastIngestedRef.current) ?? status.recent_results[0] ?? null)
    : null;

  return (
    <main style={{ padding: "1.5rem", maxWidth: 1280, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Simulation</h1>
      <p style={{ color: "#8b949e", fontSize: 13, marginBottom: "1rem", lineHeight: 1.5 }}>
        <b>CV Lab</b> streams frames through the <em>real product pipeline</em> — <code>inference.frames → trained model → inference.detections → detections store → alerts</code> — and scores live against ground truth.
        <span style={{ marginLeft: 8, color: "#58a6ff" }}>Layers 1/2/3 exercise the same path a physical camera does; lessons flow back into the product.</span>
      </p>

      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", borderBottom: "1px solid #21262d", paddingBottom: "0.5rem" }}>
        <button type="button" onClick={() => setTab("cv")} style={{ padding: "0.45rem 0.9rem", borderRadius: 6, border: "1px solid #30363d", background: tab === "cv" ? "#1f6feb" : "#21262d", color: "#e6edf3", fontWeight: tab === "cv" ? 600 : 400 }}>CV Lab — live pipeline</button>
        <button type="button" onClick={() => setTab("legacy")} style={{ padding: "0.45rem 0.9rem", borderRadius: 6, border: "1px solid #30363d", background: tab === "legacy" ? "#1f6feb" : "#21262d", color: "#e6edf3", fontWeight: tab === "legacy" ? 600 : 400 }}>Agent Replay (legacy)</button>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#8b949e", alignSelf: "center" }}>Real product path · hardware-ready · lessons-back</span>
      </div>

      {tab === "legacy" ? (
        <LegacyReplayViewer />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          {/* layers health + controls */}
          <section style={{ border: "1px solid #21262d", borderRadius: 8, padding: "0.9rem", background: "#0d1117" }}>
            {layersError ? (
              <div style={{ padding: "0.6rem", background: "rgba(248,81,73,0.12)", border: "1px solid rgba(248,81,73,0.4)", borderRadius: 6, fontSize: 13, color: "#f85149", marginBottom: "0.75rem" }}>
                Simulation service unavailable: {layersError}. Start it with <code>docker compose up -d simulation-service</code> or run locally on <code>:8014</code>. The inference + Kafka stack must also be up for detections to flow.
              </div>
            ) : !layers ? (
              <p style={{ fontSize: 13, color: "#8b949e" }}>Loading simulation layers…</p>
            ) : (
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center", marginBottom: "0.9rem" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.4 }}>Layer</span>
                {[1, 2, 3].map((n) => {
                  const isActive = layer === n;
                  const label = n === 1 ? "① Real footage (VisDrone corpus)" : n === 2 ? "② Synthetic compose" : "③ 3D world (browser)";
                  const avail = n === 1 ? (layers["1"] as unknown as { available: boolean })?.available : true;
                  return (
                    <button key={n} type="button" onClick={() => setLayer(n as 1 | 2 | 3)} disabled={n === 1 && !avail} title={n === 1 && !avail ? "Corpus not available on this host – build it with backend/ml/build_corpus.py" : ""} style={{ padding: "0.5rem 0.75rem", borderRadius: 20, border: `1.5px solid ${isActive ? "#1f6feb" : "#30363d"}`, background: isActive ? "rgba(31,111,235,0.18)" : "#21262d", color: isActive ? "#58a6ff" : "#e6edf3", fontWeight: isActive ? 600 : 400, opacity: n === 1 && !avail ? 0.5 : 1, fontSize: 13 }}>{label}</button>
                  );
                })}
                <span style={{ marginLeft: "auto", fontSize: 12, color: "#8b949e" }}>
                  {layer === 1 ? "Real aerial footage + real GT — zero synthetic risk, exercises the model you trained." : layer === 2 ? "Procedural aerial world — exact GT, tests rare classes & what-ifs before you fly." : "Photoreal 3D world in your browser — FPV frames take the same path as the aircraft."}
                </span>
              </div>
            )}

            <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", alignItems: "end" }}>
              {layer === 1 && layers && (
                <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>Sequence (VisDrone)<select value={sequenceL1} onChange={(e) => setSequenceL1(e.target.value)} style={{ minWidth: 200, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}><option value="">All sequences (548 frames)</option>{((layers["1"] as unknown as { sequences: string[] })?.sequences ?? []).map((s) => <option key={s} value={s}>{s}</option>)}</select><span style={{ fontSize: 11, color: "#6e7681" }}>Each sequence is a pseudo-flight at 5 fps.</span></label>
              )}
              {layer === 2 && layers && (
                <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>Scenario<select value={scenarioL2} onChange={(e) => setScenarioL2(e.target.value)} style={{ minWidth: 200, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}>{((layers["2"] as unknown as { scenarios: string[] })?.scenarios ?? ["railway-yard", "market-square"]).map((s) => <option key={s} value={s}>{s.replace("-", " ")}</option>)}</select></label>
              )}
              <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>FPS<input type="number" min={1} max={15} value={fps} onChange={(e) => setFps(Math.max(1, Math.min(15, Number(e.target.value) || 5)))} style={{ width: 80, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }} /></label>
              {layer !== 3 && <label style={{ fontSize: 12, color: "#8b949e", display: "flex", flexDirection: "column", gap: 4 }}>Frames<input type="number" min={10} max={2000} value={frames} onChange={(e) => setFrames(Math.max(10, Math.min(2000, Number(e.target.value) || 60)))} style={{ width: 90, padding: "0.45rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }} /></label>}
              {layer === 3 && <span style={{ fontSize: 12, color: "#8b949e", paddingBottom: 6 }}>L3 streams until you stop — GT is computed by projecting the 3D world through the drone camera.</span>}
              <button type="button" onClick={startExercise} disabled={starting || (layer === 3 && !!activeId && status?.state === "running")} style={{ padding: "0.5rem 0.9rem", background: "#1f6feb", color: "#fff", border: "1px solid #1f6feb", borderRadius: 6, fontWeight: 600, opacity: starting ? 0.6 : 1 }}>{starting ? "Starting…" : layer === 3 ? "Start 3D ingest" : `Start Layer ${layer}`}</button>
              {activeId && <button type="button" onClick={stopActive} style={{ padding: "0.5rem 0.9rem", background: "#21262d", color: "#e6edf3", border: "1px solid #30363d", borderRadius: 6 }}>Stop</button>}
              {activeId && status && <span style={{ fontSize: 12, color: status.state === "running" ? "#3fb950" : "#8b949e", fontWeight: 600 }}>{status.state === "running" ? "● running" : status.state} · {status.frame_index}{status.frames_total ? ` / ${status.frames_total}` : ""} frames</span>}
            </div>
            {cvError && <div style={{ marginTop: "0.6rem", padding: "0.5rem", background: "rgba(248,81,73,0.12)", border: "1px solid rgba(248,81,73,0.35)", borderRadius: 6, fontSize: 13, color: "#f85149", whiteSpace: "pre-wrap" }}>{cvError}</div>}
            {status?.last_error && <div style={{ marginTop: "0.4rem", fontSize: 12, color: "#d29922" }}>Last error: {status.last_error}</div>}
          </section>

          {/* exercise list */}
          <section style={{ border: "1px solid #21262d", borderRadius: 8, padding: "0.75rem", background: "#0d1117" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
              <h3 style={{ fontSize: 13, fontWeight: 600, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.4, margin: 0 }}>Recent exercises</h3>
              <button type="button" onClick={loadExercises} style={{ marginLeft: "auto", fontSize: 12, padding: "0.3rem 0.6rem" }}>Refresh</button>
            </div>
            {exercises.length === 0 ? (
              <p style={{ fontSize: 13, color: "#6e7681" }}>No exercises yet — start one above. Each exercise publishes to <code>inference.frames</code> so the real model scores it.</p>
            ) : (
              <div style={{ display: "flex", gap: "0.5rem", overflowX: "auto", paddingBottom: "0.25rem" }}>
                {exercises.slice(0, 12).map((ex) => (
                  <button key={ex.id} type="button" onClick={() => setActiveId(ex.id)} style={{ flex: "0 0 auto", textAlign: "left", padding: "0.55rem 0.7rem", minWidth: 170, borderRadius: 6, border: `1.5px solid ${activeId === ex.id ? "#1f6feb" : "#30363d"}`, background: activeId === ex.id ? "rgba(31,111,235,0.12)" : "#161b22", color: "#e6edf3" }}>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>L{ex.layer} · {ex.scenario} <span style={{ fontWeight: 400, color: ex.state === "running" ? "#3fb950" : "#8b949e" }}>· {ex.state}</span></div>
                    <div style={{ fontSize: 11, color: "#8b949e", marginTop: 2 }}>{ex.frame_index}{ex.frames_total ? `/${ex.frames_total}` : ""} frames · P {ex.precision.toFixed(2)} R {ex.recall.toFixed(2)} · {ex.alerts_candidates} alerts</div>
                    <div style={{ fontSize: 11, color: "#6e7681", marginTop: 2, fontFamily: "ui-monospace, monospace" }}>{ex.id}</div>
                  </button>
                ))}
              </div>
            )}
          </section>

          {/* live panels */}
          {activeId && status ? (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: "1rem", alignItems: "start" }}>
              <div style={{ minWidth: 0 }}>
                {status.layer === 3 ? (
                  <div style={{ position: "relative" }}>
                    <BrowserWorld active={status.state === "running"} onFrame={handleL3Frame} />
                    {/* overlay detections on top of the 3D canvas – clone GT/dets from status */}
                    <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
                      {/* show GT from last ingested (client) until server echoes scored GT, so operator sees what was sent */}
                      {(l3Result?.gts ?? lastIngestedGt).length > 0 && (
                        <div style={{ position: "absolute", inset: 0 }}>
                          {(l3Result?.gts ?? lastIngestedGt).map((g, i) => { const [x0, y0, x1, y1] = g.bbox; return <div key={`gt-${i}`} style={{ position: "absolute", left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, border: "1.5px dashed rgba(88,166,255,0.9)", background: "rgba(88,166,255,0.07)", boxSizing: "border-box" }} />; })}
                          {(l3Result?.detections ?? []).map((d, i) => { const [x0, y0, x1, y1] = d.bbox; const col = d.matched ? "#3fb950" : "#f85149"; const bg = d.matched ? "rgba(63,185,80,0.14)" : "rgba(248,81,73,0.16)"; return <div key={`d-${i}`} style={{ position: "absolute", left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`, border: `1.9px solid ${col}`, background: bg, boxSizing: "border-box" }}><span style={{ position: "absolute", top: -16, left: 0, fontSize: 10, fontWeight: 700, color: "#e6edf3", background: col, padding: "1px 4px", borderRadius: 3, whiteSpace: "nowrap" }}>{d.class_name} {d.confidence.toFixed(2)}{d.matched ? " ✓" : ""}</span></div>; })}
                        </div>
                      )}
                      {l3Result && <div style={{ position: "absolute", bottom: 8, left: 8, background: "rgba(0,0,0,0.72)", color: "#e6edf3", fontSize: 11, padding: "4px 7px", borderRadius: 4 }}>TP {l3Result.tp} · FP {l3Result.fp} · FN {l3Result.fn} · GT {l3Result.gt}{l3Result.latency_ms != null ? ` · ${l3Result.latency_ms}ms` : ""}{l3Result.alerts ? ` · ${l3Result.alerts} alerts` : ""}</div>}
                    </div>
                    <p style={{ fontSize: 11, color: "#6e7681", marginTop: "0.4rem" }}>FPV frames are captured at ~5 fps, base64-JPEG’d, and ingested via the gateway into <code>inference.frames</code>. Detections overlay comes back on <code>inference.detections</code> and is drawn here.</p>
                  </div>
                ) : (
                  <>
                    <FrameOverlay frame={displayFrame} result={displayResult} />
                    <p style={{ fontSize: 11, color: "#6e7681", marginTop: "0.4rem" }}>Dashed blue = ground truth (corpus or composed). Solid green = true positive (matched, IoU ≥ 0.5), red = false positive. Confidence shown per box. Latency is camera → detection round-trip through Kafka.</p>
                  </>
                )}
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <div style={{ border: "1px solid #30363d", borderRadius: 8, background: "#0d1117", padding: "0.7rem" }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: "0.5rem" }}>Scoreboard — live</div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", fontSize: 13 }}>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>Precision</div><div style={{ fontWeight: 700, color: status.scoreboard.overall.precision >= 0.7 ? "#3fb950" : "#d29922" }}>{status.scoreboard.overall.precision.toFixed(3)}</div></div>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>Recall</div><div style={{ fontWeight: 700, color: status.scoreboard.overall.recall >= 0.7 ? "#3fb950" : "#d29922" }}>{status.scoreboard.overall.recall.toFixed(3)}</div></div>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>F1</div><div style={{ fontWeight: 600 }}>{status.scoreboard.overall.f1.toFixed(3)}</div></div>
                    <div><div style={{ color: "#8b949e", fontSize: 11 }}>Alerts (≥0.7)</div><div style={{ fontWeight: 600 }}>{status.alerts.candidates}</div></div>
                  </div>
                  <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.6rem", fontSize: 12, flexWrap: "wrap" }}>
                    <span style={{ padding: "2px 6px", borderRadius: 10, background: "#21262d", border: "1px solid #30363d" }}>TP {status.scoreboard.overall.tp}</span>
                    <span style={{ padding: "2px 6px", borderRadius: 10, background: "#21262d", border: "1px solid #30363d" }}>FP {status.scoreboard.overall.fp}</span>
                    <span style={{ padding: "2px 6px", borderRadius: 10, background: "#21262d", border: "1px solid #30363d" }}>FN {status.scoreboard.overall.fn}</span>
                    <span style={{ padding: "2px 6px", borderRadius: 10, background: "#21262d", border: "1px solid #30363d" }}>GT {status.scoreboard.overall.gt}</span>
                  </div>
                  {Object.keys(status.scoreboard.per_class).length > 0 && (
                    <table style={{ width: "100%", marginTop: "0.6rem", fontSize: 11, borderCollapse: "collapse" }}>
                      <thead><tr style={{ color: "#8b949e", textAlign: "left" }}><th style={{ padding: "3px 4px" }}>Class</th><th style={{ padding: "3px 4px" }}>P</th><th style={{ padding: "3px 4px" }}>R</th><th style={{ padding: "3px 4px" }}>TP/FP/FN</th></tr></thead>
                      <tbody>{Object.entries(status.scoreboard.per_class).slice(0, 8).map(([cls, cc]) => <tr key={cls} style={{ borderTop: "1px solid #21262d" }}><td style={{ padding: "3px 4px", color: "#e6edf3" }}>{cls}</td><td style={{ padding: "3px 4px" }}>{cc.precision.toFixed(2)}</td><td style={{ padding: "3px 4px" }}>{cc.recall.toFixed(2)}</td><td style={{ padding: "3px 4px", color: "#8b949e" }}>{cc.tp}/{cc.fp}/{cc.fn}</td></tr>)}</tbody>
                    </table>
                  )}
                </div>

                <div style={{ border: "1px solid #30363d", borderRadius: 8, background: "#0d1117", padding: "0.7rem" }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: "0.35rem" }}>Latency (camera → detection)</div>
                  <div style={{ fontSize: 13 }}>p50 {status.latency_ms.p50 != null ? `${status.latency_ms.p50} ms` : "—"} · p95 {status.latency_ms.p95 != null ? `${status.latency_ms.p95} ms` : "—"} · scored {status.latency_ms.count}/{status.metrics.frames_sent} ({status.metrics.scoring_drain}%)</div>
                  <div style={{ fontSize: 11, color: "#6e7681", marginTop: 4 }}>SLOs: detection→row &lt;250 ms · camera→alert &lt;1.5 s (Phase 4 on Orin). MPS numbers are smoke only.</div>
                </div>

                <div style={{ border: "1px solid #30363d", borderRadius: 8, background: "#0d1117", padding: "0.7rem" }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#8b949e", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: "0.35rem" }}>Lessons → product</div>
                  {status.lessons.length === 0 ? <p style={{ fontSize: 12, color: "#6e7681", margin: 0 }}>No lessons yet — needs ≥5 GT per class and a few scored frames. Watch for <em>false-positive bias</em> (threshold/zone to tighten) and <em>recall gaps</em> (data to grow).</p> : (
                    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.4rem" }}>
                      {status.lessons.map((l, i) => (
                        <li key={i} style={{ fontSize: 12, padding: "0.45rem 0.55rem", borderRadius: 6, border: `1px solid ${l.kind === "false_positive_bias" ? "rgba(248,81,73,0.35)" : l.kind === "recall_gap" ? "rgba(210,153,34,0.35)" : "rgba(88,166,255,0.3)"}`, background: l.kind === "false_positive_bias" ? "rgba(248,81,73,0.08)" : l.kind === "recall_gap" ? "rgba(210,153,34,0.08)" : "rgba(88,166,255,0.06)" }}>
                          <span style={{ fontWeight: 600 }}>{l.kind === "false_positive_bias" ? "FP bias" : l.kind === "recall_gap" ? "Recall gap" : l.kind}</span>{l.class_name ? <> · <code>{l.class_name as string}</code></> : null} — {l.hint as string}
                          <span style={{ color: "#8b949e" }}> {Object.entries(l).filter(([k]) => !["kind", "class_name", "hint"].includes(k)).map(([k, v]) => `${k} ${String(v)}`).join(" · ")}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p style={{ fontSize: 11, color: "#6e7681", marginTop: "0.45rem", lineHeight: 1.4 }}>These are the same signals the hardware team uses before a site flight: tighten thresholds / zone-masks for FP-heavy classes, grow data for recall gaps, watch alert pressure per hour.</p>
                </div>
              </div>
            </div>
          ) : (
            <div style={{ border: "1px dashed #30363d", borderRadius: 8, padding: "1rem", background: "rgba(13,17,23,0.6)", textAlign: "center" }}>
              <p style={{ fontSize: 13, color: "#8b949e", margin: 0 }}>No active CV exercise. Start a Layer above, or pick a recent exercise to inspect its live scoreboard.</p>
              <p style={{ fontSize: 12, color: "#6e7681", marginTop: "0.4rem" }}>All layers feed <code>inference.frames</code> so the real model + alert + persistence path is exercised — then lessons flow back into thresholds, zones and the next training set.</p>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
