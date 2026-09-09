/**
 * Builds a three.js scene from a WorldSpec.
 *
 * Kept separate from the React component so the scene graph can be constructed
 * and inspected without a renderer, and so a future offline renderer can reuse
 * the same geometry decisions. Actor meshes carry their spec on `userData` so
 * ground-truth projection reads real world-space dimensions.
 */
import * as THREE from "three";
import { Sky } from "three/examples/jsm/objects/Sky.js";
import {
  ActorSpec, BuildingSpec, PropSpec, Rng, WorldSpec, sunPosition, isDaylight,
} from "./worldSpec";
import {
  asphaltTexture, concreteTexture, facadeTexture, grassTexture,
  gravelTexture, metalTexture, roughnessTexture,
} from "./textures";

export interface BuiltScene {
  scene: THREE.Scene;
  sky: Sky;
  sun: THREE.DirectionalLight;
  hemi: THREE.HemisphereLight;
  actors: { group: THREE.Group; spec: ActorSpec }[];
  rain: THREE.Points | null;
  streetlights: THREE.PointLight[];
  dispose: () => void;
}

/** World coordinates are 0..W / 0..D; scene coordinates are centred on origin. */
export function toScene(spec: WorldSpec, x: number, z: number): [number, number] {
  return [x - spec.terrain.width / 2, z - spec.terrain.depth / 2];
}

function buildPerson(spec: ActorSpec, rng: Rng): THREE.Group {
  const g = new THREE.Group();
  const skin = new THREE.MeshStandardMaterial({ color: 0xc68642, roughness: 0.85 });
  const cloth = new THREE.MeshStandardMaterial({
    color: new THREE.Color(spec.color), roughness: 0.9, metalness: 0.02,
  });
  const trouser = new THREE.MeshStandardMaterial({
    color: new THREE.Color(rng.pick(["#2b3a52", "#1f2933", "#3d3d3d", "#4a3b2a"])), roughness: 0.9,
  });

  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.42, 4, 10), cloth);
  torso.position.y = 1.16;
  torso.scale.set(1.25, 1, 0.72);
  torso.castShadow = true;
  g.add(torso);

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.105, 12, 10), skin);
  head.position.y = 1.62;
  head.castShadow = true;
  g.add(head);

  // Limbs matter at low altitude: they are what makes a walking figure legible
  // from 30-40m. A single capsule reads as a post.
  for (const side of [-1, 1]) {
    const leg = new THREE.Mesh(new THREE.CapsuleGeometry(0.075, 0.55, 3, 6), trouser);
    leg.position.set(side * 0.085, 0.53, 0);
    leg.castShadow = true;
    leg.name = side < 0 ? "legL" : "legR";
    g.add(leg);

    const arm = new THREE.Mesh(new THREE.CapsuleGeometry(0.058, 0.44, 3, 6), cloth);
    arm.position.set(side * 0.235, 1.16, 0);
    arm.castShadow = true;
    arm.name = side < 0 ? "armL" : "armR";
    g.add(arm);
  }
  return g;
}

function buildVehicle(spec: ActorSpec, rng: Rng): THREE.Group {
  const g = new THREE.Group();
  const [len, , wid] = spec.size;
  const cls = spec.className;

  const bodyMat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(spec.color), roughness: 0.35, metalness: 0.55,
  });
  const glassMat = new THREE.MeshStandardMaterial({
    color: 0x1b2733, roughness: 0.08, metalness: 0.3, transparent: true, opacity: 0.85,
  });
  const tyreMat = new THREE.MeshStandardMaterial({ color: 0x14161a, roughness: 0.95 });

  if (cls === "motor" || cls === "bicycle") {
    const frame = new THREE.Mesh(new THREE.BoxGeometry(len * 0.75, 0.16, 0.16), bodyMat);
    frame.position.y = 0.58;
    frame.castShadow = true;
    g.add(frame);
    const rider = new THREE.Mesh(new THREE.CapsuleGeometry(0.16, 0.5, 3, 8), bodyMat);
    rider.position.set(-len * 0.05, 1.0, 0);
    rider.castShadow = true;
    g.add(rider);
    for (const dx of [-len * 0.35, len * 0.35]) {
      const wheel = new THREE.Mesh(new THREE.TorusGeometry(0.32, 0.06, 8, 16), tyreMat);
      wheel.rotation.y = Math.PI / 2;
      wheel.position.set(dx, 0.32, 0);
      wheel.castShadow = true;
      g.add(wheel);
    }
    return g;
  }

  const isBoxy = cls === "truck" || cls === "bus" || cls === "van";
  const bodyH = isBoxy ? spec.size[1] * 0.72 : spec.size[1] * 0.52;
  const bodyY = cls === "truck" || cls === "bus" ? 0.75 : 0.52;

  const body = new THREE.Mesh(new THREE.BoxGeometry(len, bodyH, wid), bodyMat);
  body.position.y = bodyY + bodyH / 2;
  body.castShadow = true;
  body.receiveShadow = true;
  g.add(body);

  if (cls === "car" || cls === "van") {
    // Greenhouse: a smaller inset upper box. Its shadow is a strong cue that
    // the object is a vehicle rather than a crate when seen from above.
    const cabLen = cls === "car" ? len * 0.5 : len * 0.42;
    const cab = new THREE.Mesh(new THREE.BoxGeometry(cabLen, spec.size[1] * 0.42, wid * 0.9), glassMat);
    cab.position.set(cls === "car" ? -len * 0.04 : len * 0.2, bodyY + bodyH + spec.size[1] * 0.18, 0);
    cab.castShadow = true;
    g.add(cab);
  } else if (cls === "truck") {
    const cab = new THREE.Mesh(new THREE.BoxGeometry(len * 0.26, spec.size[1] * 0.55, wid * 0.98), bodyMat);
    cab.position.set(len * 0.36, bodyY + bodyH * 0.9, 0);
    cab.castShadow = true;
    g.add(cab);
  } else if (cls === "awning-tricycle") {
    const awn = new THREE.Mesh(
      new THREE.BoxGeometry(len * 0.8, 0.08, wid * 1.05),
      new THREE.MeshStandardMaterial({ color: rng.pick([0xd94f4f, 0x3f7fd9, 0xe8c33a]), roughness: 0.8 }),
    );
    awn.position.y = spec.size[1] * 0.95;
    awn.castShadow = true;
    g.add(awn);
  }

  const wheelR = cls === "truck" || cls === "bus" ? 0.46 : 0.33;
  const axles = cls === "bus" || cls === "truck" ? [-len * 0.32, len * 0.3] : [-len * 0.31, len * 0.31];
  for (const dx of axles) {
    for (const dz of [-wid / 2 + 0.08, wid / 2 - 0.08]) {
      const wheel = new THREE.Mesh(new THREE.CylinderGeometry(wheelR, wheelR, 0.2, 12), tyreMat);
      wheel.rotation.x = Math.PI / 2;
      wheel.position.set(dx, wheelR, dz);
      wheel.castShadow = true;
      g.add(wheel);
    }
  }
  return g;
}

export function buildActor(spec: ActorSpec, rng: Rng): THREE.Group {
  const g = spec.className === "pedestrian" || spec.className === "people"
    ? buildPerson(spec, rng)
    : buildVehicle(spec, rng);
  g.userData.spec = spec;
  return g;
}

function buildBuilding(b: BuildingSpec, seed: number, lit: boolean): THREE.Group {
  const g = new THREE.Group();
  const wallTex = b.hasWindows ? facadeTexture(seed, b.color, lit) : metalTexture(seed, b.color);
  const wallMat = new THREE.MeshStandardMaterial({
    map: wallTex,
    roughness: b.style === "office" ? 0.5 : 0.78,
    metalness: b.style === "warehouse" ? 0.35 : 0.1,
  });
  if (b.hasWindows && lit) {
    wallMat.emissiveMap = wallTex;
    wallMat.emissive = new THREE.Color(0xffd9a0);
    wallMat.emissiveIntensity = 0.55;
  }

  const walls = new THREE.Mesh(new THREE.BoxGeometry(b.width, b.height, b.depth), wallMat);
  walls.position.y = b.height / 2;
  walls.castShadow = true;
  walls.receiveShadow = true;
  g.add(walls);

  // Roofs are most of what a drone sees, so leaving them as a bare box is the
  // single biggest realism loss.
  const roofMat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(b.roofColor), roughness: 0.9, metalness: 0.15,
  });
  const roof = new THREE.Mesh(new THREE.BoxGeometry(b.width * 1.02, 0.35, b.depth * 1.02), roofMat);
  roof.position.y = b.height + 0.17;
  roof.castShadow = true;
  roof.receiveShadow = true;
  g.add(roof);

  const rng = new Rng(seed ^ 0x9e3779b9);
  const units = rng.int(1, 4);
  for (let i = 0; i < units; i++) {
    const uw = rng.range(1.2, 3.0);
    const ud = rng.range(1.0, 2.4);
    const uh = rng.range(0.7, 1.6);
    const unit = new THREE.Mesh(
      new THREE.BoxGeometry(uw, uh, ud),
      new THREE.MeshStandardMaterial({ color: 0x8d9296, roughness: 0.7, metalness: 0.4 }),
    );
    unit.position.set(
      rng.range(-b.width / 2 + uw, b.width / 2 - uw),
      b.height + 0.35 + uh / 2,
      rng.range(-b.depth / 2 + ud, b.depth / 2 - ud),
    );
    unit.castShadow = true;
    g.add(unit);
  }

  g.rotation.y = THREE.MathUtils.degToRad(b.rotationDeg);
  return g;
}

function buildProp(p: PropSpec, seed: number): THREE.Object3D | null {
  const rng = new Rng(seed);
  switch (p.kind) {
    case "tree": {
      const g = new THREE.Group();
      const h = rng.range(4, 9) * p.scale;
      const trunk = new THREE.Mesh(
        new THREE.CylinderGeometry(0.13 * p.scale, 0.2 * p.scale, h * 0.45, 6),
        new THREE.MeshStandardMaterial({ color: 0x4a3728, roughness: 0.95 }),
      );
      trunk.position.y = h * 0.22;
      trunk.castShadow = true;
      g.add(trunk);
      const foliage = new THREE.MeshStandardMaterial({
        color: new THREE.Color(rng.pick(["#2f4a24", "#3a5a2c", "#27401f", "#456b33"])),
        roughness: 1.0, flatShading: true,
      });
      // Two offset blobs read better from above than a single ball.
      for (let i = 0; i < 2; i++) {
        const r = rng.range(1.4, 2.4) * p.scale;
        const blob = new THREE.Mesh(new THREE.IcosahedronGeometry(r, 1), foliage);
        blob.position.set(rng.range(-0.5, 0.5), h * 0.55 + i * r * 0.6, rng.range(-0.5, 0.5));
        blob.castShadow = true;
        g.add(blob);
      }
      return g;
    }
    case "bush": {
      const r = rng.range(0.5, 1.2) * p.scale;
      const m = new THREE.Mesh(
        new THREE.IcosahedronGeometry(r, 0),
        new THREE.MeshStandardMaterial({
          color: new THREE.Color(rng.pick(["#33502a", "#3d5c31", "#2b4423"])),
          roughness: 1.0, flatShading: true,
        }),
      );
      m.position.y = r * 0.7;
      m.castShadow = true;
      return m;
    }
    case "streetlight": {
      const g = new THREE.Group();
      const h = 7 * p.scale;
      const mat = new THREE.MeshStandardMaterial({ color: 0x6b7076, roughness: 0.6, metalness: 0.7 });
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.13, h, 8), mat);
      pole.position.y = h / 2;
      pole.castShadow = true;
      g.add(pole);
      const arm = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.1, 0.1), mat);
      arm.position.set(0.8, h - 0.15, 0);
      g.add(arm);
      const head = new THREE.Mesh(
        new THREE.BoxGeometry(0.5, 0.16, 0.28),
        new THREE.MeshStandardMaterial({ color: 0x2b2f33, roughness: 0.5 }),
      );
      head.position.set(1.55, h - 0.25, 0);
      g.add(head);
      return g;
    }
    case "container": {
      const w = rng.range(6, 12.2) * p.scale;
      const m = new THREE.Mesh(
        new THREE.BoxGeometry(w, 2.6 * p.scale, 2.44 * p.scale),
        new THREE.MeshStandardMaterial({
          map: metalTexture(seed, rng.pick(["#8a4a3a", "#3a5a8a", "#4a6b4a", "#8a7a3a", "#6b6b6b"]), 2),
          roughness: 0.7, metalness: 0.45,
        }),
      );
      m.position.y = 1.3 * p.scale;
      m.castShadow = true;
      m.receiveShadow = true;
      return m;
    }
    case "transformer": {
      const g = new THREE.Group();
      const mat = new THREE.MeshStandardMaterial({ color: 0x7d8287, roughness: 0.55, metalness: 0.65 });
      const box = new THREE.Mesh(new THREE.BoxGeometry(2.6 * p.scale, 2.2 * p.scale, 1.8 * p.scale), mat);
      box.position.y = 1.1 * p.scale;
      box.castShadow = true;
      g.add(box);
      for (const dx of [-0.7, 0, 0.7]) {
        const ins = new THREE.Mesh(
          new THREE.CylinderGeometry(0.12, 0.16, 1.1 * p.scale, 6),
          new THREE.MeshStandardMaterial({ color: 0x9a9a8a, roughness: 0.4 }),
        );
        ins.position.set(dx * p.scale, 2.2 * p.scale + 0.55, 0);
        ins.castShadow = true;
        g.add(ins);
      }
      return g;
    }
    case "pylon": {
      const g = new THREE.Group();
      const h = rng.range(16, 26) * p.scale;
      const mat = new THREE.MeshStandardMaterial({ color: 0x8b9095, roughness: 0.6, metalness: 0.8 });
      for (const [dx, dz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]] as [number, number][]) {
        const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.16, h, 5), mat);
        leg.position.set(dx * 1.5 * p.scale, h / 2, dz * 1.5 * p.scale);
        leg.rotation.z = -dx * 0.045;
        leg.rotation.x = dz * 0.045;
        leg.castShadow = true;
        g.add(leg);
      }
      for (const y of [h * 0.62, h * 0.82, h * 0.97]) {
        const arm = new THREE.Mesh(new THREE.BoxGeometry(9 * p.scale, 0.16, 0.5), mat);
        arm.position.y = y;
        arm.castShadow = true;
        g.add(arm);
      }
      return g;
    }
    case "fence": {
      const m = new THREE.Mesh(
        new THREE.BoxGeometry(8 * p.scale, 2.0 * p.scale, 0.08),
        new THREE.MeshStandardMaterial({
          color: 0x6f7378, roughness: 0.8, metalness: 0.5, transparent: true, opacity: 0.75,
        }),
      );
      m.position.y = 1.0 * p.scale;
      m.castShadow = true;
      return m;
    }
    case "sign": {
      const g = new THREE.Group();
      const pole = new THREE.Mesh(
        new THREE.CylinderGeometry(0.05, 0.05, 2.4 * p.scale, 6),
        new THREE.MeshStandardMaterial({ color: 0x8b9095, roughness: 0.6, metalness: 0.7 }),
      );
      pole.position.y = 1.2 * p.scale;
      pole.castShadow = true;
      g.add(pole);
      const plate = new THREE.Mesh(
        new THREE.BoxGeometry(0.75, 0.55, 0.04),
        new THREE.MeshStandardMaterial({ color: 0xd8dde2, roughness: 0.5 }),
      );
      plate.position.y = 2.3 * p.scale;
      plate.castShadow = true;
      g.add(plate);
      return g;
    }
    default:
      return null;
  }
}

export function buildScene(spec: WorldSpec): BuiltScene {
  const scene = new THREE.Scene();
  const env = spec.environment;
  const daylight = isDaylight(env.timeOfDay);
  const { elevationDeg, azimuthDeg } = sunPosition(env.timeOfDay);
  const disposables: { dispose: () => void }[] = [];

  // --- Sky -----------------------------------------------------------------
  const sky = new Sky();
  sky.scale.setScalar(8000);
  sky.name = "sky";
  const u = sky.material.uniforms;
  u.turbidity.value = env.turbidity;
  u.rayleigh.value = env.rayleigh;
  u.mieCoefficient.value = env.weather === "fog" ? 0.03 : 0.005;
  u.mieDirectionalG.value = 0.8;

  const phi = THREE.MathUtils.degToRad(90 - Math.max(elevationDeg, -3));
  const theta = THREE.MathUtils.degToRad(azimuthDeg);
  const sunVec = new THREE.Vector3().setFromSphericalCoords(1, phi, theta);
  u.sunPosition.value.copy(sunVec);
  scene.add(sky);

  // Fog colour-matched to the horizon; otherwise distant geometry fades to a
  // grey that does not belong to the sky and the frame looks composited.
  const horizonColor = daylight
    ? new THREE.Color().setHSL(0.58, env.weather === "overcast" ? 0.05 : 0.35, env.weather === "overcast" ? 0.72 : 0.78)
    : new THREE.Color().setHSL(0.62, 0.35, 0.09);
  scene.fog = new THREE.FogExp2(horizonColor.getHex(), env.fogDensity);

  // --- Lighting ------------------------------------------------------------
  const sunIntensity = daylight
    ? Math.max(0.15, Math.sin(THREE.MathUtils.degToRad(Math.max(elevationDeg, 0))) *
        (env.weather === "overcast" || env.weather === "rain" ? 1.1 : 3.0))
    : 0.02;
  const warmth = 1 - Math.min(1, Math.max(0, elevationDeg) / 45);
  const sunColor = new THREE.Color().setHSL(0.08 + 0.06 * (1 - warmth), 0.55 * warmth + 0.05, 0.62);

  const sun = new THREE.DirectionalLight(sunColor, sunIntensity);
  sun.position.copy(sunVec).multiplyScalar(300);
  sun.castShadow = daylight;
  sun.shadow.mapSize.set(2048, 2048);
  const half = Math.max(spec.terrain.width, spec.terrain.depth) * 0.62;
  sun.shadow.camera.left = -half;
  sun.shadow.camera.right = half;
  sun.shadow.camera.top = half;
  sun.shadow.camera.bottom = -half;
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 900;
  sun.shadow.bias = -0.0006;
  sun.shadow.normalBias = 0.03;
  scene.add(sun);
  scene.add(sun.target);

  const hemi = new THREE.HemisphereLight(
    horizonColor.getHex(),
    daylight ? 0x4a4436 : 0x0a0d12,
    daylight ? (env.weather === "overcast" ? 1.5 : 0.85) : 0.12,
  );
  scene.add(hemi);

  // --- Terrain -------------------------------------------------------------
  // Displaced grid rather than a flat plane: even a metre of relief gives the
  // shadows something to fall across, which is most of the perceived realism.
  const segs = 96;
  const groundGeo = new THREE.PlaneGeometry(spec.terrain.width, spec.terrain.depth, segs, segs);
  const posAttr = groundGeo.attributes.position;
  const reliefRng = new Rng(spec.seed ^ 0x5bf03635);
  const a1 = reliefRng.range(0, Math.PI * 2);
  const a2 = reliefRng.range(0, Math.PI * 2);
  for (let i = 0; i < posAttr.count; i++) {
    const x = posAttr.getX(i);
    const y = posAttr.getY(i);
    const h =
      Math.sin(x * 0.035 + a1) * Math.cos(y * 0.028 + a2) * spec.terrain.reliefAmplitude +
      Math.sin(x * 0.11 + a2) * Math.cos(y * 0.09 + a1) * spec.terrain.reliefAmplitude * 0.25;
    posAttr.setZ(i, h);
  }
  groundGeo.computeVertexNormals();

  const grassTex = grassTexture(spec.seed, spec.terrain.baseColor, spec.terrain.patchColors);
  const groundRough = roughnessTexture(spec.seed ^ 0x1234, 0.62, 0.98);
  disposables.push(grassTex, groundRough, groundGeo);
  const ground = new THREE.Mesh(
    groundGeo,
    new THREE.MeshStandardMaterial({
      map: grassTex,
      roughnessMap: groundRough,
      roughness: 1 - env.groundWetness * 0.55,
      metalness: 0.0,
    }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  // --- Roads ---------------------------------------------------------------
  const roadTexCache: Record<string, THREE.Texture> = {};
  for (const road of spec.roads) {
    if (!roadTexCache[road.surface]) {
      roadTexCache[road.surface] =
        road.surface === "asphalt" ? asphaltTexture(spec.seed)
        : road.surface === "concrete" ? concreteTexture(spec.seed)
        : gravelTexture(spec.seed);
      disposables.push(roadTexCache[road.surface]);
    }
    const mat = new THREE.MeshStandardMaterial({
      map: roadTexCache[road.surface],
      roughness: (road.surface === "asphalt" ? 0.82 : 0.95) - env.groundWetness * 0.5,
      metalness: 0.02,
    });
    for (let i = 0; i < road.points.length - 1; i++) {
      const [ax, az] = toScene(spec, road.points[i][0], road.points[i][1]);
      const [bx, bz] = toScene(spec, road.points[i + 1][0], road.points[i + 1][1]);
      const len = Math.hypot(bx - ax, bz - az);
      const seg = new THREE.Mesh(new THREE.BoxGeometry(len, 0.08, road.width), mat);
      seg.position.set((ax + bx) / 2, spec.terrain.reliefAmplitude + 0.1, (az + bz) / 2);
      seg.rotation.y = -Math.atan2(bz - az, bx - ax);
      seg.receiveShadow = true;
      scene.add(seg);

      if (road.markings) {
        const dashMat = new THREE.MeshStandardMaterial({ color: 0xd8d3c4, roughness: 0.7 });
        const dashes = Math.max(1, Math.floor(len / 6));
        for (let d = 0; d < dashes; d++) {
          const t = (d + 0.5) / dashes;
          const dash = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.02, 0.16), dashMat);
          dash.position.set(ax + (bx - ax) * t, spec.terrain.reliefAmplitude + 0.15, az + (bz - az) * t);
          dash.rotation.y = -Math.atan2(bz - az, bx - ax);
          scene.add(dash);
        }
      }
    }
  }

  // --- Rails ---------------------------------------------------------------
  for (const rail of spec.rails) {
    const ballastMat = new THREE.MeshStandardMaterial({ map: gravelTexture(spec.seed ^ 0x77), roughness: 0.98 });
    const railMat = new THREE.MeshStandardMaterial({ color: 0x6e6257, roughness: 0.35, metalness: 0.85 });
    const sleeperMat = new THREE.MeshStandardMaterial({ color: 0x4a3f33, roughness: 0.95 });
    for (let i = 0; i < rail.points.length - 1; i++) {
      const [ax, az] = toScene(spec, rail.points[i][0], rail.points[i][1]);
      const [bx, bz] = toScene(spec, rail.points[i + 1][0], rail.points[i + 1][1]);
      const len = Math.hypot(bx - ax, bz - az);
      const ang = -Math.atan2(bz - az, bx - ax);
      const y = spec.terrain.reliefAmplitude + 0.1;

      const ballast = new THREE.Mesh(new THREE.BoxGeometry(len, 0.28, rail.gauge + 1.7), ballastMat);
      ballast.position.set((ax + bx) / 2, y + 0.14, (az + bz) / 2);
      ballast.rotation.y = ang;
      ballast.receiveShadow = true;
      scene.add(ballast);

      const sleepers = Math.max(1, Math.floor(len / rail.sleeperSpacing));
      const sleeperGeo = new THREE.BoxGeometry(0.26, 0.14, rail.gauge + 0.7);
      const sleeperMesh = new THREE.InstancedMesh(sleeperGeo, sleeperMat, sleepers);
      const m = new THREE.Matrix4();
      const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), ang);
      for (let s = 0; s < sleepers; s++) {
        const t = (s + 0.5) / sleepers;
        m.compose(
          new THREE.Vector3(ax + (bx - ax) * t, y + 0.33, az + (bz - az) * t),
          q, new THREE.Vector3(1, 1, 1),
        );
        sleeperMesh.setMatrixAt(s, m);
      }
      sleeperMesh.castShadow = true;
      sleeperMesh.receiveShadow = true;
      scene.add(sleeperMesh);
      disposables.push(sleeperGeo);

      for (const off of [-rail.gauge / 2, rail.gauge / 2]) {
        const r = new THREE.Mesh(new THREE.BoxGeometry(len, 0.12, 0.08), railMat);
        r.position.set(
          (ax + bx) / 2 + Math.sin(ang) * off,
          y + 0.46,
          (az + bz) / 2 + Math.cos(ang) * off,
        );
        r.rotation.y = ang;
        r.castShadow = true;
        scene.add(r);
      }
    }
  }

  // --- Buildings -----------------------------------------------------------
  spec.buildings.forEach((b, i) => {
    const g = buildBuilding(b, spec.seed + i * 7919, !daylight);
    const [x, z] = toScene(spec, b.position[0], b.position[1]);
    g.position.set(x, spec.terrain.reliefAmplitude, z);
    scene.add(g);
  });

  // --- Props ---------------------------------------------------------------
  const streetlights: THREE.PointLight[] = [];
  spec.props.forEach((p, i) => {
    const obj = buildProp(p, spec.seed + i * 104729);
    if (!obj) return;
    const [x, z] = toScene(spec, p.position[0], p.position[1]);
    obj.position.set(x, spec.terrain.reliefAmplitude, z);
    obj.rotation.y = THREE.MathUtils.degToRad(p.rotationDeg);
    scene.add(obj);

    // Capped: each point light is a real per-fragment cost even without shadows.
    if (p.kind === "streetlight" && !daylight && streetlights.length < 10) {
      const lamp = new THREE.PointLight(0xffc987, 22, 34, 2);
      lamp.position.set(x + 1.55, spec.terrain.reliefAmplitude + 6.6, z);
      scene.add(lamp);
      streetlights.push(lamp);
    }
  });

  // --- Actors --------------------------------------------------------------
  const actors: { group: THREE.Group; spec: ActorSpec }[] = [];
  spec.actors.forEach((a, i) => {
    const g = buildActor(a, new Rng(spec.seed + i * 31337));
    scene.add(g);
    actors.push({ group: g, spec: a });
  });

  // --- Rain ----------------------------------------------------------------
  let rain: THREE.Points | null = null;
  if (env.rainIntensity > 0) {
    const count = Math.floor(env.rainIntensity);
    const positions = new Float32Array(count * 3);
    const rainRng = new Rng(spec.seed ^ 0xa11ce);
    for (let i = 0; i < count; i++) {
      positions[i * 3] = rainRng.range(-spec.terrain.width / 2, spec.terrain.width / 2);
      positions[i * 3 + 1] = rainRng.range(0, 90);
      positions[i * 3 + 2] = rainRng.range(-spec.terrain.depth / 2, spec.terrain.depth / 2);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    rain = new THREE.Points(geo, new THREE.PointsMaterial({
      color: 0xaab8c8, size: 0.16, transparent: true, opacity: 0.5, depthWrite: false,
    }));
    scene.add(rain);
    disposables.push(geo);
  }

  const dispose = () => {
    for (const d of disposables) {
      try { d.dispose(); } catch { /* already disposed */ }
    }
    scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (mesh.geometry) mesh.geometry.dispose?.();
      const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(mat)) mat.forEach((m) => m.dispose?.());
      else mat?.dispose?.();
    });
  };

  return { scene, sky, sun, hemi, actors, rain, streetlights, dispose };
}
