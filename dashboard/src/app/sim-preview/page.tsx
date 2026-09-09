"use client";

/**
 * Standalone world preview.
 *
 * Renders the simulated world with no backend, no auth and no exercise, so the
 * renderer can be inspected and tuned on its own. Frames are discarded; ground
 * truth is drawn as an overlay so label quality can be judged by eye, which is
 * the only practical way to catch boxes that are subtly wrong.
 */

import { useCallback, useMemo, useState } from "react";
import BrowserWorld from "@/components/simulation/BrowserWorld";
import type { GtBox } from "@/lib/sim/groundTruth";
import type { Weather, WorldSpec } from "@/lib/sim/worldSpec";

const WEATHERS: Weather[] = ["clear", "partly_cloudy", "overcast", "rain", "fog"];

export default function SimPreviewPage() {
  const [seed, setSeed] = useState(1337);
  const [timeOfDay, setTimeOfDay] = useState(14);
  const [weather, setWeather] = useState<Weather>("clear");
  const [density, setDensity] = useState(1.0);
  const [running, setRunning] = useState(true);
  const [gt, setGt] = useState<GtBox[]>([]);
  const [showBoxes, setShowBoxes] = useState(true);
  const [spec, setSpec] = useState<WorldSpec | null>(null);

  const onFrame = useCallback((_b64: string, boxes: GtBox[]) => setGt(boxes), []);
  const onWorldReady = useCallback((s: WorldSpec) => setSpec(s), []);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const b of gt) c[b.class_name] = (c[b.class_name] ?? 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [gt]);

  const label = { fontSize: 12, color: "#8b949e", display: "block", marginBottom: 2 };
  const field: React.CSSProperties = {
    background: "#0d1117", color: "#e6edf3", border: "1px solid #30363d",
    borderRadius: 6, padding: "4px 8px", fontSize: 13, width: "100%",
  };

  return (
    <main style={{ padding: "1.5rem", maxWidth: 1180, margin: "0 auto", color: "#e6edf3" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>World preview</h1>
      <p style={{ color: "#8b949e", fontSize: 13, marginBottom: "1rem" }}>
        Renderer and ground-truth inspection. No backend, no ingest &mdash; frames are discarded.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "0.75rem", marginBottom: "1rem" }}>
        <div>
          <label style={label} htmlFor="seed">Seed</label>
          <input id="seed" style={field} type="number" value={seed}
            onChange={(e) => setSeed(Number(e.target.value) || 0)} />
        </div>
        <div>
          <label style={label} htmlFor="tod">Time of day: {timeOfDay.toFixed(1)}</label>
          <input id="tod" style={field} type="range" min={0} max={23.9} step={0.1}
            value={timeOfDay} onChange={(e) => setTimeOfDay(Number(e.target.value))} />
        </div>
        <div>
          <label style={label} htmlFor="wx">Weather</label>
          <select id="wx" style={field} value={weather}
            onChange={(e) => setWeather(e.target.value as Weather)}>
            {WEATHERS.map((w) => <option key={w} value={w}>{w.replace("_", " ")}</option>)}
          </select>
        </div>
        <div>
          <label style={label} htmlFor="den">Density: {density.toFixed(1)}x</label>
          <input id="den" style={field} type="range" min={0.2} max={3} step={0.1}
            value={density} onChange={(e) => setDensity(Number(e.target.value))} />
        </div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
          <button style={{ ...field, cursor: "pointer", width: "auto" }} onClick={() => setRunning((r) => !r)}>
            {running ? "Pause" : "Play"}
          </button>
          <button style={{ ...field, cursor: "pointer", width: "auto" }} onClick={() => setSeed(Math.floor(Math.random() * 1e9))}>
            Reroll
          </button>
        </div>
        <div style={{ display: "flex", alignItems: "flex-end" }}>
          <label style={{ fontSize: 12, color: "#8b949e", cursor: "pointer" }}>
            <input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} /> GT boxes
          </label>
        </div>
      </div>

      <div style={{ position: "relative" }}>
        <BrowserWorld
          active={running}
          seed={seed}
          timeOfDay={timeOfDay}
          weather={weather}
          density={density}
          ingestFps={4}
          onFrame={onFrame}
          onWorldReady={onWorldReady}
        />
        {showBoxes && (
          <svg
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
            style={{ position: "absolute", inset: 0, width: "100%", height: "calc(100% - 62px)", pointerEvents: "none" }}
          >
            {gt.map((b, i) => {
              const [x0, y0, x1, y1] = b.bbox;
              // Dim heavily occluded boxes so poor labels stand out visually.
              const opacity = 0.35 + b.visibility * 0.65;
              return (
                <g key={`${b.actor_id}-${i}`} opacity={opacity}>
                  <rect
                    x={x0 * 1000} y={y0 * 1000}
                    width={(x1 - x0) * 1000} height={(y1 - y0) * 1000}
                    fill="none" stroke={b.truncated ? "#f0883e" : "#3fb950"}
                    strokeWidth={1.5} vectorEffect="non-scaling-stroke"
                  />
                  <text
                    x={x0 * 1000} y={y0 * 1000 - 3}
                    fill={b.truncated ? "#f0883e" : "#3fb950"}
                    style={{ fontSize: 11, fontFamily: "ui-monospace, monospace" }}
                  >
                    {b.class_name}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
      </div>

      <div style={{ marginTop: "1rem", display: "flex", gap: "1.5rem", flexWrap: "wrap", fontSize: 12.5, color: "#8b949e" }}>
        <span><b style={{ color: "#e6edf3" }}>{gt.length}</b> labelled</span>
        {counts.map(([cls, n]) => <span key={cls}>{cls}: <b style={{ color: "#e6edf3" }}>{n}</b></span>)}
        {spec && <span>{spec.buildings.length} buildings, {spec.props.length} props</span>}
      </div>
    </main>
  );
}
