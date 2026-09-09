"use client";

/**
 * Photoreal-leaning FPV renderer for the Layer 3 simulation exercise.
 *
 * The camera is the drone. Frames are captured at a fixed rate and pushed
 * through the real inference pipeline together with ground truth, so an
 * exercise doubles as a labelled synthetic dataset.
 *
 * Scope note: this is browser WebGL, not a game engine. It reaches "good modern
 * indie game" rather than GTA-grade, because the assets are procedural rather
 * than authored and the lighting is real-time rather than baked. The scene spec
 * is deliberately renderer-agnostic (see lib/sim/worldSpec.ts) so the same world
 * can later be handed to an offline high-fidelity renderer without redefining it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { SMAAPass } from "three/examples/jsm/postprocessing/SMAAPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { VignetteShader } from "three/examples/jsm/shaders/VignetteShader.js";

import {
  generateWorld, isDaylight, pathAt, sunPosition,
  type Weather, type WorldSpec,
} from "@/lib/sim/worldSpec";
import { buildScene, toScene, type BuiltScene } from "@/lib/sim/sceneBuilder";
import { extractGroundTruth, type GtBox } from "@/lib/sim/groundTruth";

type Props = {
  active: boolean;
  ingestFps?: number;
  /** Fix the world across restarts; omit for a new world each run. */
  seed?: number;
  timeOfDay?: number;
  weather?: Weather;
  /** Actor count multiplier. Above 1 produces crowded, harder frames. */
  density?: number;
  onFrame: (image_b64: string, gt: GtBox[], width: number, height: number) => void;
  /** Surfaces the active world so the exercise record can store its seed. */
  onWorldReady?: (spec: WorldSpec) => void;
};

const FRAME_W = 960;
const FRAME_H = 540;

export default function BrowserWorld({
  active,
  ingestFps = 5,
  seed,
  timeOfDay,
  weather,
  density = 1.0,
  onFrame,
  onWorldReady,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const lastCaptureRef = useRef(0);
  const startRef = useRef<number | null>(null);
  const [fps, setFps] = useState(0);
  const fpsAccum = useRef({ frames: 0, last: 0 });

  // A stable seed per mount unless the caller pins one, so restarting an
  // exercise does not silently change the world under a fixed seed.
  const worldSeed = useMemo(
    () => seed ?? Math.floor(Math.random() * 0xffffffff),
    [seed],
  );

  const spec = useMemo(
    () => generateWorld(worldSeed, { timeOfDay, weather, density }),
    [worldSeed, timeOfDay, weather, density],
  );

  const engineRef = useRef<{
    renderer: THREE.WebGLRenderer;
    composer: EffectComposer;
    camera: THREE.PerspectiveCamera;
    built: BuiltScene;
    occluders: THREE.Object3D[];
  } | null>(null);

  // Keep the latest callback without re-running the render loop effect.
  const onFrameRef = useRef(onFrame);
  useEffect(() => { onFrameRef.current = onFrame; }, [onFrame]);

  useEffect(() => { onWorldReady?.(spec); }, [spec, onWorldReady]);

  // ---- Build ---------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    canvas.width = FRAME_W;
    canvas.height = FRAME_H;

    const renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false, // SMAA handles this in the composer
      // Required for toDataURL to return the rendered frame rather than a blank
      // buffer, since the drawing buffer is otherwise cleared after present.
      preserveDrawingBuffer: true,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(1); // fixed, so captured frames are a known size
    renderer.setSize(FRAME_W, FRAME_H, false);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    // Filmic tonemapping is most of the difference between "3D render" and
    // "camera footage"; without it bright sky clips to flat white.
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    // ACES compresses highlights hard, so exposure has to be pushed above 1 in
    // daylight or a midday scene renders as dusk. Overcast scatters light and
    // needs less; night relies on streetlights and needs much more.
    renderer.toneMappingExposure = (() => {
      const e = spec.environment;
      if (!isDaylight(e.timeOfDay)) return 2.2;
      if (e.weather === "overcast" || e.weather === "rain") return 1.15;
      if (e.weather === "fog") return 1.35;
      return 1.45;
    })();
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const built = buildScene(spec);

    const camera = new THREE.PerspectiveCamera(
      spec.camera.fovDeg, FRAME_W / FRAME_H, 0.5, 2000,
    );

    // Occluders: everything solid except the sky and the actors themselves.
    const occluders: THREE.Object3D[] = [];
    built.scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh && o.name !== "sky") occluders.push(o);
    });

    const composer = new EffectComposer(renderer);
    composer.setSize(FRAME_W, FRAME_H);
    composer.addPass(new RenderPass(built.scene, camera));

    // Bloom: subtle. Overdone bloom is the classic tell of an amateur render.
    const bloom = new UnrealBloomPass(
      new THREE.Vector2(FRAME_W, FRAME_H),
      isDaylight(spec.environment.timeOfDay) ? 0.18 : 0.55,
      0.55,
      0.92,
    );
    composer.addPass(bloom);

    const vignette = new ShaderPass(VignetteShader);
    vignette.uniforms.offset.value = 1.05;
    vignette.uniforms.darkness.value = 1.15;
    composer.addPass(vignette);

    composer.addPass(new SMAAPass(FRAME_W, FRAME_H));
    composer.addPass(new OutputPass());

    engineRef.current = { renderer, composer, camera, built, occluders };

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      composer.dispose();
      built.dispose();
      renderer.dispose();
      engineRef.current = null;
    };
  }, [spec]);

  // ---- Animate -------------------------------------------------------------
  useEffect(() => {
    if (!active || !engineRef.current) return;
    const engine = engineRef.current;
    const { renderer, composer, camera, built, occluders } = engine;
    const canvas = canvasRef.current;
    if (!canvas) return;

    let running = true;
    startRef.current = performance.now();
    lastCaptureRef.current = 0;
    fpsAccum.current = { frames: 0, last: performance.now() };

    const groundY = spec.terrain.reliefAmplitude;
    const camSpec = spec.camera;
    const daylight = isDaylight(spec.environment.timeOfDay);
    const { elevationDeg } = sunPosition(spec.environment.timeOfDay);

    const tick = () => {
      if (!running) return;
      rafRef.current = requestAnimationFrame(tick);

      const now = performance.now();
      const t = (now - (startRef.current ?? now)) / 1000;

      // --- Drone camera ---
      const { pos, headingRad } = pathAt(camSpec.path, t * camSpec.speedMs);
      const [cx, cz] = toScene(spec, pos[0], pos[1]);
      const alt = groundY + camSpec.altitudeM;

      // Airframe vibration and slow drift. Perfectly smooth motion is the
      // single most obvious sign of synthetic footage.
      const vib = camSpec.vibration;
      const vx = Math.sin(t * 37.1) * vib + Math.sin(t * 11.3) * vib * 0.6;
      const vy = Math.cos(t * 41.7) * vib + Math.cos(t * 9.1) * vib * 0.5;
      const drift = Math.sin(t * 0.23) * 0.9;

      camera.position.set(cx, alt + Math.sin(t * 0.7) * 0.35, cz + drift);

      // Gimbal: pitch below horizon, yaw follows track.
      const pitch = THREE.MathUtils.degToRad(camSpec.gimbalPitchDeg);
      const look = new THREE.Vector3(
        cx + Math.cos(headingRad) * Math.cos(pitch) * 40,
        alt - Math.sin(pitch) * 40,
        cz + Math.sin(headingRad) * Math.cos(pitch) * 40,
      );
      camera.lookAt(look);
      camera.rotation.x += vy;
      camera.rotation.y += vx;
      camera.rotation.z += Math.sin(t * 0.9) * vib * 2.5;

      // Keep the sun's shadow frustum centred on the camera, otherwise shadows
      // vanish as the drone flies away from the world origin.
      built.sun.position.set(
        cx + Math.cos(THREE.MathUtils.degToRad(sunPosition(spec.environment.timeOfDay).azimuthDeg)) * 220,
        Math.max(40, Math.sin(THREE.MathUtils.degToRad(Math.max(elevationDeg, 5))) * 260),
        cz + 120,
      );
      built.sun.target.position.set(cx, groundY, cz);
      built.sun.target.updateMatrixWorld();

      // --- Actors ---
      for (const { group, spec: a } of built.actors) {
        const wobble = a.jitter > 0 ? Math.sin(t * 0.6 + a.phase) * a.jitter : 0;
        const dist = (t + a.phase) * Math.max(0, a.speedMs + wobble);
        const { pos: ap, headingRad: ah } = pathAt(a.path, dist);
        const [ax, az] = toScene(spec, ap[0], ap[1]);
        group.position.set(ax, groundY, az);
        group.rotation.y = -ah;

        // Walk cycle: swing limbs so pedestrians read as moving, not sliding.
        if (a.className === "pedestrian" || a.className === "people") {
          const swing = Math.sin(t * a.speedMs * 3.6 + a.phase) * 0.5;
          for (const child of group.children) {
            if (child.name === "legL" || child.name === "armR") child.rotation.x = swing;
            else if (child.name === "legR" || child.name === "armL") child.rotation.x = -swing;
          }
          group.position.y = groundY + Math.abs(Math.sin(t * a.speedMs * 3.6 + a.phase)) * 0.035;
        }
      }

      // --- Rain ---
      if (built.rain) {
        const p = built.rain.geometry.attributes.position as THREE.BufferAttribute;
        const arr = p.array as Float32Array;
        const fall = 22 / 60;
        const windX = Math.cos(THREE.MathUtils.degToRad(spec.environment.windDirectionDeg)) * spec.environment.windSpeedMs * 0.02;
        const windZ = Math.sin(THREE.MathUtils.degToRad(spec.environment.windDirectionDeg)) * spec.environment.windSpeedMs * 0.02;
        for (let i = 0; i < arr.length; i += 3) {
          arr[i] += windX;
          arr[i + 1] -= fall;
          arr[i + 2] += windZ;
          if (arr[i + 1] < 0) {
            arr[i + 1] = 90;
            arr[i] = cx + (Math.random() - 0.5) * spec.terrain.width;
            arr[i + 2] = cz + (Math.random() - 0.5) * spec.terrain.depth;
          }
        }
        p.needsUpdate = true;
      }

      composer.render();

      // --- FPS ---
      fpsAccum.current.frames++;
      if (now - fpsAccum.current.last >= 1000) {
        setFps(fpsAccum.current.frames);
        fpsAccum.current = { frames: 0, last: now };
      }

      // --- Capture + ground truth ---
      if (now - lastCaptureRef.current >= 1000 / ingestFps) {
        lastCaptureRef.current = now;
        try {
          const gt = extractGroundTruth(camera, built.actors, occluders, {
            frameWidth: FRAME_W,
            frameHeight: FRAME_H,
          });
          const b64 = canvas.toDataURL("image/jpeg", 0.82).split(",")[1] || "";
          if (b64) onFrameRef.current(b64, gt, FRAME_W, FRAME_H);
        } catch {
          // A capture failure must not kill the render loop.
        }
      }
    };

    tick();
    return () => {
      running = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [active, ingestFps, spec]);

  const env = spec.environment;
  const hhmm = `${String(Math.floor(env.timeOfDay)).padStart(2, "0")}:${String(
    Math.floor((env.timeOfDay % 1) * 60),
  ).padStart(2, "0")}`;

  return (
    <div style={{ border: "1px solid #30363d", borderRadius: 8, overflow: "hidden", background: "#0f1419" }}>
      <canvas
        ref={canvasRef}
        width={FRAME_W}
        height={FRAME_H}
        style={{ display: "block", width: "100%", aspectRatio: "16 / 9", background: "#0a0e14" }}
      />
      <div
        style={{
          display: "flex", flexWrap: "wrap", gap: "0.75rem",
          padding: "0.5rem 0.75rem", fontSize: 11.5, color: "#8b949e",
          borderTop: "1px solid #21262d", fontFamily: "ui-monospace, monospace",
        }}
      >
        <span>seed {spec.seed}</span>
        <span>{hhmm}</span>
        <span>{env.weather.replace("_", " ")}</span>
        <span>{Math.round(spec.camera.altitudeM)}m AGL</span>
        <span>{Math.round(spec.camera.gimbalPitchDeg)}&deg; gimbal</span>
        <span>{spec.actors.length} actors</span>
        {active ? <span style={{ color: "#3fb950" }}>{fps} fps</span> : <span>idle</span>}
      </div>
      {!active && (
        <div style={{ padding: "0.5rem", fontSize: 12, color: "#8b949e", textAlign: "center", borderTop: "1px solid #21262d" }}>
          World idle. Start a Layer 3 exercise to stream FPV frames through the live pipeline.
        </div>
      )}
    </div>
  );
}
