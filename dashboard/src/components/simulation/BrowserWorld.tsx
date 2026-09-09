"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

type GTBox = { class_name: string; bbox: number[] };

type Props = {
  active: boolean;
  ingestFps?: number;
  onFrame: (image_b64: string, gt: GTBox[], width: number, height: number) => void;
};

// World spec mirrors backend railway-yard but in 3D
const WORLD_W = 240;
const WORLD_H = 140;
const SPEC = {
  roads: [
    { points: [[0, 70], [120, 70], [240, 85]] as [number, number][], width: 9, color: "#4a443c" },
    { points: [[60, 0], [60, 140]] as [number, number][], width: 6, color: "#3c3731" },
  ],
  people: [
    { path: [[10, 70], [40, 72], [80, 68]] as [number, number][], speed: 1.2, color: "#e2574c" },
    { path: [[55, 30], [55, 80], [70, 90]] as [number, number][], speed: 1.0, color: "#4cb3a0" },
    { path: [[150, 20], [150, 55], [160, 70]] as [number, number][], speed: 1.1, color: "#8f6bd6" },
    { path: [[200, 140], [200, 90], [175, 75]] as [number, number][], speed: 0.9, color: "#f0a04b" },
    { path: [[30, 130], [45, 120], [60, 135]] as [number, number][], speed: 0.8, color: "#d98cb3" },
    { path: [[110, 120], [130, 90], [140, 110]] as [number, number][], speed: 1.4, color: "#e8e3d5" },
  ],
  vehicles: [
    { path: [[0, 70], [240, 85]] as [number, number][], speed: 7.0, bwh: [4.6, 2.2] as [number, number], color: "#3f86e0" },
    { path: [[240, 70], [0, 70]] as [number, number][], speed: 5.0, bwh: [4.6, 2.2] as [number, number], color: "#d64550" },
    { path: [[60, 140], [60, 0]] as [number, number][], speed: 5.5, bwh: [4.2, 2.0] as [number, number], color: "#2f6b3c" },
  ],
  dronePath: [[0, 70], [120, 70], [240, 85]] as [number, number][],
  droneSpeed: 12,
  droneAltitude: 38,
};

function interp(a: [number, number], b: [number, number], t: number): [number, number] {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function pathPos(points: [number, number][], dist: number): { pos: [number, number]; heading: number } {
  let total = 0;
  const segs: { p: [number, number]; q: [number, number]; len: number }[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const p = points[i], q = points[i + 1];
    const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
    total += len;
    segs.push({ p, q, len });
  }
  if (total <= 0) return { pos: points[0], heading: 0 };
  let remain = dist % total;
  for (const { p, q, len } of segs) {
    if (len <= 0) continue;
    if (remain <= len) {
      const pos = interp(p, q, remain / len);
      const heading = Math.atan2(q[1] - p[1], q[0] - p[0]);
      return { pos, heading };
    }
    remain -= len;
  }
  const last = segs[segs.length - 1];
  const heading = Math.atan2(last.q[1] - last.p[1], last.q[0] - last.p[0]);
  return { pos: points[points.length - 1], heading };
}

function worldToScene(x: number, zWorld: number): [number, number] {
  // center world at 0,0
  return [x - WORLD_W / 2, zWorld - WORLD_H / 2];
}

export default function BrowserWorld({ active, ingestFps = 5, onFrame }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const lastCaptureRef = useRef(0);
  const frameRef = useRef(0);
  const sceneRef = useRef<{
    renderer: THREE.WebGLRenderer;
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    people: THREE.Group[];
    vehicles: THREE.Group[];
    ground: THREE.Mesh;
  } | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const W = 640, H = 360;
    canvas.width = W;
    canvas.height = H;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    renderer.setSize(W, H, false);
    renderer.setClearColor(0x0a0e14, 1);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a2332);
    scene.fog = new THREE.Fog(0x1a2332, 80, 220);

    const camera = new THREE.PerspectiveCamera(58, W / H, 0.1, 500);
    // initial
    camera.position.set(0, SPEC.droneAltitude, 0);
    camera.lookAt(0, 0, 0);

    // lights
    const ambient = new THREE.AmbientLight(0xffffff, 0.85);
    scene.add(ambient);
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(40, 80, 30);
    dir.castShadow = true;
    dir.shadow.mapSize.set(1024, 1024);
    dir.shadow.camera.left = -80; dir.shadow.camera.right = 80;
    dir.shadow.camera.top = 80; dir.shadow.camera.bottom = -80;
    scene.add(dir);

    // ground
    const groundGeo = new THREE.PlaneGeometry(WORLD_W, WORLD_H);
    const groundMat = new THREE.MeshStandardMaterial({ color: 0x3a4a2b, roughness: 0.95 });
    const ground = new THREE.Mesh(groundGeo, groundMat);
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);

    // subtle grid texture via helper lines (roads will be on top)
    const grid = new THREE.GridHelper(Math.max(WORLD_W, WORLD_H), 24, 0x2f3a25, 0x2f3a25);
    // grid is at y=0, but Plane is at y=0 too -> raise grid slightly
    grid.position.y = 0.05;
    scene.add(grid);

    // roads as thin boxes slightly above ground
    for (const road of SPEC.roads) {
      const pts = road.points;
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i], b = pts[i + 1];
        const ax = worldToScene(a[0], a[1])[0], az = worldToScene(a[0], a[1])[1];
        const bx = worldToScene(b[0], b[1])[0], bz = worldToScene(b[0], b[1])[1];
        const len = Math.hypot(bx - ax, bz - az);
        const cx = (ax + bx) / 2, cz = (az + bz) / 2;
        const heading = Math.atan2(bz - az, bx - ax);
        const geom = new THREE.BoxGeometry(len, 0.12, road.width);
        const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(road.color) });
        const mesh = new THREE.Mesh(geom, mat);
        mesh.position.set(cx, 0.06, cz);
        mesh.rotation.y = -heading;
        mesh.receiveShadow = true;
        scene.add(mesh);
      }
    }

    // building boxes for flavour (around edges)
    const buildingMat = new THREE.MeshStandardMaterial({ color: 0x5a5f66 });
    const buildingPositions: [number, number, number, number, number][] = [
      [-80, 0, -50, 18, 12],
      [70, 0, -55, 22, 10],
      [-60, 0, 55, 16, 14],
      [85, 0, 45, 20, 12],
      [0, 0, -60, 30, 8],
    ];
    for (const [x, , z, w, d] of buildingPositions) {
      const h = 8 + Math.random() * 6;
      const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), buildingMat);
      b.position.set(x, h / 2, z);
      b.castShadow = true; b.receiveShadow = true;
      scene.add(b);
    }

    // people groups
    const people: THREE.Group[] = [];
    for (const p of SPEC.people) {
      const g = new THREE.Group();
      const body = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 1.1, 8), new THREE.MeshStandardMaterial({ color: new THREE.Color(p.color) }));
      body.position.y = 0.65;
      body.castShadow = true;
      const head = new THREE.Mesh(new THREE.SphereGeometry(0.32, 8, 8), new THREE.MeshStandardMaterial({ color: 0x1c1c1c }));
      head.position.y = 1.45;
      head.castShadow = true;
      g.add(body); g.add(head);
      // shadow blob on ground
      const shadow = new THREE.Mesh(new THREE.CircleGeometry(0.5, 8), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.18 }));
      shadow.rotation.x = -Math.PI / 2;
      shadow.position.y = 0.02;
      g.add(shadow);
      scene.add(g);
      people.push(g);
    }

    const vehicles: THREE.Group[] = [];
    for (const v of SPEC.vehicles) {
      const g = new THREE.Group();
      const bw = v.bwh[0], bh = v.bwh[1];
      const body = new THREE.Mesh(new THREE.BoxGeometry(bw, 0.9, bh), new THREE.MeshStandardMaterial({ color: new THREE.Color(v.color) }));
      body.position.y = 0.55;
      body.castShadow = true;
      body.receiveShadow = true;
      const cabin = new THREE.Mesh(new THREE.BoxGeometry(bw * 0.55, 0.5, bh * 0.88), new THREE.MeshStandardMaterial({ color: 0x1c1c1c }));
      cabin.position.y = 0.85;
      g.add(body); g.add(cabin);
      scene.add(g);
      vehicles.push(g);
    }

    sceneRef.current = { renderer, scene, camera, people, vehicles, ground };

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      renderer.dispose();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!active || !sceneRef.current) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { renderer, scene, camera, people, vehicles } = sceneRef.current;
    const W = 640, H = 360;
    let running = true;

    const tick = () => {
      if (!running) return;
      rafRef.current = requestAnimationFrame(tick);
      const t = frameRef.current++ / 60;
      // drone along path
      const droneDist = t * SPEC.droneSpeed;
      const { pos: dPos, heading: dHead } = pathPos(SPEC.dronePath, droneDist);
      const [dx, dz] = worldToScene(dPos[0], dPos[1]);
      const alt = SPEC.droneAltitude;
      camera.position.set(dx, alt, dz);
      // look slightly ahead and down
      const lookAhead = 18;
      const lx = dx + Math.cos(dHead) * lookAhead;
      const lz = dz + Math.sin(dHead) * lookAhead;
      camera.lookAt(lx, 0, lz);
      // slight drone tilt with heading
      camera.rotation.z = Math.sin(t * 0.7) * 0.04;

      // update people
      for (let i = 0; i < SPEC.people.length; i++) {
        const spec = SPEC.people[i];
        const { pos, heading } = pathPos(spec.path, t * spec.speed);
        const [x, z] = worldToScene(pos[0], pos[1]);
        const g = people[i];
        g.position.set(x, 0, z);
        g.rotation.y = -heading + Math.PI / 2;
        // bob
        g.position.y = Math.sin(t * 3 + i) * 0.05;
      }
      // vehicles
      for (let i = 0; i < SPEC.vehicles.length; i++) {
        const spec = SPEC.vehicles[i];
        const { pos, heading } = pathPos(spec.path, t * spec.speed);
        const [x, z] = worldToScene(pos[0], pos[1]);
        const g = vehicles[i];
        g.position.set(x, 0, z);
        g.rotation.y = -heading;
      }

      renderer.render(scene, camera);

      const now = performance.now();
      if (now - lastCaptureRef.current >= 1000 / ingestFps) {
        lastCaptureRef.current = now;
        try {
          const dataUrl = canvas.toDataURL("image/jpeg", 0.78);
          const b64 = dataUrl.split(",")[1] || "";
          // compute GT via projection
          const gt: GTBox[] = [];
          const projectBox = (center: THREE.Vector3, half: THREE.Vector3, className: string) => {
            const corners: THREE.Vector3[] = [];
            for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
              corners.push(new THREE.Vector3(center.x + sx * half.x, center.y + sy * half.y, center.z + sz * half.z));
            }
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            let anyInFront = false;
            for (const c of corners) {
              const p = c.clone().project(camera);
              // behind camera if z > 1 or < -1 and w negative? project handles perspective divide
              // Check if point is in front: p.z between -1 and 1 and c is in front of camera
              // Simple check: camera space z < 0 means in front (three camera looks -Z)
              const camSpace = c.clone().applyMatrix4(camera.matrixWorldInverse);
              if (camSpace.z > -0.1) continue; // behind or too close
              anyInFront = true;
              const sx = (p.x * 0.5 + 0.5);
              const sy = (1 - (p.y * 0.5 + 0.5));
              if (sx < minX) minX = sx;
              if (sy < minY) minY = sy;
              if (sx > maxX) maxX = sx;
              if (sy > maxY) maxY = sy;
            }
            if (!anyInFront) return;
            // if bbox is fully off screen, skip (clamped later, but zero area skip)
            if (maxX <= 0 || maxY <= 0 || minX >= 1 || minY >= 1) return;
            // clamp
            const x0 = Math.max(0, minX), y0 = Math.max(0, minY), x1 = Math.min(1, maxX), y1 = Math.min(1, maxY);
            if (x1 - x0 < 0.005 || y1 - y0 < 0.005) return;
            gt.push({ class_name: className, bbox: [x0, y0, x1, y1] });
          };
          // people GT: person bbox approx 0.7 x 1.7
          for (let i = 0; i < people.length; i++) {
            const g = people[i];
            const center = new THREE.Vector3(g.position.x, 0.85, g.position.z);
            const half = new THREE.Vector3(0.38, 0.85, 0.38);
            projectBox(center, half, "person");
          }
          for (let i = 0; i < vehicles.length; i++) {
            const g = vehicles[i];
            const spec = SPEC.vehicles[i];
            const bw = spec.bwh[0], bh = spec.bwh[1];
            const center = new THREE.Vector3(g.position.x, 0.7, g.position.z);
            const half = new THREE.Vector3(bw / 2, 0.55, bh / 2);
            projectBox(center, half, "car");
          }

          onFrame(b64, gt, W, H);
        } catch {
          // ignore capture errors
        }
      }
    };
    tick();
    return () => { running = false; if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [active, ingestFps, onFrame]);

  return (
    <div style={{ border: "1px solid #30363d", borderRadius: 8, overflow: "hidden", background: "#0f1419" }}>
      <canvas ref={canvasRef} width={640} height={360} style={{ display: "block", width: "100%", maxWidth: 640, height: 360, background: "#0a0e14" }} />
      {!active && (
        <div style={{ padding: "0.5rem", fontSize: 12, color: "#8b949e", textAlign: "center", borderTop: "1px solid #21262d" }}>
          3D world idle — start a Layer 3 exercise to stream FPV frames through the real pipeline.
        </div>
      )}
    </div>
  );
}
