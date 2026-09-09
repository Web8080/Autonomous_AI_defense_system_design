/**
 * Procedural PBR textures generated on a canvas at runtime.
 *
 * Generated rather than loaded because the dashboard must work offline and in
 * air-gapped customer deployments, and because shipping a texture pack would
 * dwarf the rest of the bundle. The cost is that these are noise-based rather
 * than photographic, which is the main reason the result reads as "good game"
 * rather than photoreal. Every generator is seeded, so textures are stable.
 */
import * as THREE from "three";
import { Rng } from "./worldSpec";

function canvas(size: number): { c: HTMLCanvasElement; ctx: CanvasRenderingContext2D } {
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("2D canvas context unavailable");
  return { c, ctx };
}

function finish(c: HTMLCanvasElement, repeat: number): THREE.CanvasTexture {
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  return tex;
}

function blobNoise(
  ctx: CanvasRenderingContext2D, size: number, rng: Rng, count: number,
  colors: string[], radius: [number, number], alpha: [number, number],
) {
  for (let i = 0; i < count; i++) {
    const x = rng.range(0, size);
    const y = rng.range(0, size);
    const r = rng.range(radius[0], radius[1]);
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rng.pick(colors));
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.globalAlpha = rng.range(alpha[0], alpha[1]);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/** Fine per-pixel grain: what stops large surfaces looking like plastic. */
function grain(ctx: CanvasRenderingContext2D, size: number, rng: Rng, strength: number) {
  const img = ctx.getImageData(0, 0, size, size);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const n = (rng.next() - 0.5) * strength;
    d[i] = Math.max(0, Math.min(255, d[i] + n));
    d[i + 1] = Math.max(0, Math.min(255, d[i + 1] + n));
    d[i + 2] = Math.max(0, Math.min(255, d[i + 2] + n));
  }
  ctx.putImageData(img, 0, 0);
}

export function grassTexture(seed: number, base: string, patches: string[], repeat = 14): THREE.CanvasTexture {
  const size = 512;
  const { c, ctx } = canvas(size);
  const rng = new Rng(seed);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, size, size);
  // Large, soft, low-alpha patches: high-contrast blobs tile visibly.
  blobNoise(ctx, size, rng, 260, patches, [40, 170], [0.06, 0.22]);
  for (let i = 0; i < 2600; i++) {
    const x = rng.range(0, size);
    const y = rng.range(0, size);
    ctx.strokeStyle = rng.pick(patches);
    ctx.globalAlpha = rng.range(0.08, 0.3);
    ctx.lineWidth = rng.range(0.5, 1.4);
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + rng.range(-3, 3), y + rng.range(-5, -1));
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  grain(ctx, size, rng, 16);
  return finish(c, repeat);
}

export function asphaltTexture(seed: number, repeat = 14): THREE.CanvasTexture {
  const size = 512;
  const { c, ctx } = canvas(size);
  const rng = new Rng(seed);
  ctx.fillStyle = "#3b3d40";
  ctx.fillRect(0, 0, size, size);
  blobNoise(ctx, size, rng, 300, ["#4a4d51", "#313336", "#54585c"], [8, 46], [0.1, 0.4]);
  for (let i = 0; i < 5200; i++) {
    ctx.fillStyle = rng.bool() ? "#5b5f64" : "#2b2d30";
    ctx.globalAlpha = rng.range(0.15, 0.5);
    ctx.fillRect(rng.range(0, size), rng.range(0, size), rng.range(0.7, 2.2), rng.range(0.7, 2.2));
  }
  ctx.globalAlpha = 0.35;
  ctx.strokeStyle = "#242629";
  for (let i = 0; i < 14; i++) {
    ctx.lineWidth = rng.range(0.6, 1.8);
    let x = rng.range(0, size);
    let y = rng.range(0, size);
    ctx.beginPath();
    ctx.moveTo(x, y);
    for (let s = 0; s < 8; s++) {
      x += rng.range(-26, 26);
      y += rng.range(-26, 26);
      ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  grain(ctx, size, rng, 20);
  return finish(c, repeat);
}

export function concreteTexture(seed: number, repeat = 12): THREE.CanvasTexture {
  const size = 512;
  const { c, ctx } = canvas(size);
  const rng = new Rng(seed);
  ctx.fillStyle = "#9a9c9e";
  ctx.fillRect(0, 0, size, size);
  blobNoise(ctx, size, rng, 260, ["#a8aaac", "#8b8d8f", "#b2b4b6"], [14, 80], [0.1, 0.4]);
  ctx.strokeStyle = "#75777a";
  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 1.6;
  for (let i = 0; i <= 4; i++) {
    const p = (size / 4) * i;
    ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, size); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(size, p); ctx.stroke();
  }
  ctx.globalAlpha = 1;
  grain(ctx, size, rng, 14);
  return finish(c, repeat);
}

export function gravelTexture(seed: number, repeat = 26): THREE.CanvasTexture {
  const size = 512;
  const { c, ctx } = canvas(size);
  const rng = new Rng(seed);
  ctx.fillStyle = "#6b6459";
  ctx.fillRect(0, 0, size, size);
  for (let i = 0; i < 9000; i++) {
    const r = rng.range(0.8, 3.2);
    ctx.fillStyle = rng.pick(["#7d7568", "#5a544b", "#8b8375", "#4e4941", "#938a7b"]);
    ctx.globalAlpha = rng.range(0.4, 0.95);
    ctx.beginPath();
    ctx.ellipse(rng.range(0, size), rng.range(0, size), r, r * rng.range(0.6, 1.0), rng.range(0, Math.PI), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
  grain(ctx, size, rng, 18);
  return finish(c, repeat);
}

export function metalTexture(seed: number, base: string, repeat = 4): THREE.CanvasTexture {
  const size = 256;
  const { c, ctx } = canvas(size);
  const rng = new Rng(seed);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, size, size);
  // Corrugation: the signature look of industrial cladding from above.
  for (let x = 0; x < size; x += 8) {
    ctx.fillStyle = "rgba(0,0,0,0.16)";
    ctx.fillRect(x, 0, 3, size);
    ctx.fillStyle = "rgba(255,255,255,0.09)";
    ctx.fillRect(x + 3, 0, 2, size);
  }
  blobNoise(ctx, size, rng, 40, ["#7a4a2a", "#8b5a3a", "#5a3a22"], [4, 26], [0.05, 0.22]);
  grain(ctx, size, rng, 10);
  return finish(c, repeat);
}

/** Roughness map; wet ground is smoother, driving reflective sheen after rain. */
export function roughnessTexture(seed: number, min: number, max: number, repeat = 20): THREE.CanvasTexture {
  const size = 256;
  const { c, ctx } = canvas(size);
  const rng = new Rng(seed);
  const mid = Math.round(((min + max) / 2) * 255);
  ctx.fillStyle = `rgb(${mid},${mid},${mid})`;
  ctx.fillRect(0, 0, size, size);
  const lo = `rgb(${Math.round(min * 255)},${Math.round(min * 255)},${Math.round(min * 255)})`;
  const hi = `rgb(${Math.round(max * 255)},${Math.round(max * 255)},${Math.round(max * 255)})`;
  blobNoise(ctx, size, rng, 180, [lo, hi], [6, 50], [0.2, 0.7]);
  grain(ctx, size, rng, 22);
  return finish(c, repeat);
}

/** Building facade with windows that can glow after dark. */
export function facadeTexture(seed: number, base: string, lit: boolean, repeat = 1): THREE.CanvasTexture {
  const size = 256;
  const { c, ctx } = canvas(size);
  const rng = new Rng(seed);
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, size, size);
  blobNoise(ctx, size, rng, 90, ["#ffffff", "#000000"], [10, 50], [0.03, 0.1]);

  const cols = 8, rows = 6;
  const mw = size / cols, mh = size / rows;
  for (let r = 0; r < rows; r++) {
    for (let col = 0; col < cols; col++) {
      const on = lit && rng.bool(0.45);
      ctx.fillStyle = on
        ? `rgba(255,${210 + rng.int(0, 40)},${150 + rng.int(0, 60)},0.95)`
        : "rgba(30,36,42,0.9)";
      ctx.fillRect(col * mw + mw * 0.22, r * mh + mh * 0.2, mw * 0.56, mh * 0.5);
    }
  }
  grain(ctx, size, rng, 8);
  return finish(c, repeat);
}
