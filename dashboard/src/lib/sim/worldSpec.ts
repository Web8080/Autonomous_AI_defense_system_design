/**
 * Renderer-agnostic description of a simulated world.
 *
 * Nothing in this file imports three.js. That is deliberate: the same spec is
 * consumed by the browser renderer today and can drive an offline high-fidelity
 * renderer later without the scene definition being rewritten.
 *
 * Everything is derived from a seed. The previous implementation used
 * Math.random() for building heights, so the world differed on every mount and
 * a synthetic dataset could not be reproduced from its metadata. Given the
 * frames feed the training and evaluation pipeline, reproducibility is a
 * correctness requirement, not a nicety.
 */

/**
 * The 10 VisDrone classes the detector is trained on, per
 * manifests/frozen-visdrone.json. Ground truth must use exactly these strings.
 *
 * The previous renderer emitted "person" and "car". "person" is not a VisDrone
 * class at all (it splits into `pedestrian` for a walking individual and
 * `people` for a static or grouped one), so synthetic evaluation silently
 * scored against a label the model can never predict.
 */
export const VISDRONE_CLASSES = [
  "pedestrian",
  "people",
  "bicycle",
  "car",
  "van",
  "truck",
  "tricycle",
  "awning-tricycle",
  "bus",
  "motor",
] as const;

export type VisDroneClass = (typeof VISDRONE_CLASSES)[number];

/** Deterministic RNG (mulberry32): small, fast, adequate for scene layout. */
export class Rng {
  private state: number;

  constructor(seed: number) {
    this.state = seed >>> 0;
  }

  next(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0;
    let t = this.state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  range(min: number, max: number): number {
    return min + this.next() * (max - min);
  }

  int(min: number, max: number): number {
    return Math.floor(this.range(min, max + 1));
  }

  pick<T>(items: readonly T[]): T {
    return items[Math.floor(this.next() * items.length)];
  }

  bool(probability = 0.5): boolean {
    return this.next() < probability;
  }
}

export type Vec2 = [number, number];

export type Weather = "clear" | "partly_cloudy" | "overcast" | "rain" | "fog";

export interface EnvironmentSpec {
  /** Local solar time, 0-24. Drives sun elevation and colour. */
  timeOfDay: number;
  weather: Weather;
  turbidity: number;
  rayleigh: number;
  fogDensity: number;
  rainIntensity: number;
  /** Ground wetness, 0-1. Raises reflectivity and darkens albedo. */
  groundWetness: number;
  windSpeedMs: number;
  windDirectionDeg: number;
}

export interface TerrainSpec {
  width: number;
  depth: number;
  /** Low-frequency height variation, metres. Flat worlds read as fake. */
  reliefAmplitude: number;
  baseColor: string;
  patchColors: string[];
}

export interface RoadSpec {
  points: Vec2[];
  width: number;
  markings: boolean;
  surface: "asphalt" | "gravel" | "concrete";
}

export interface RailSpec {
  points: Vec2[];
  gauge: number;
  sleeperSpacing: number;
}

export interface BuildingSpec {
  position: Vec2;
  width: number;
  depth: number;
  height: number;
  rotationDeg: number;
  style: "warehouse" | "office" | "substation" | "shed";
  color: string;
  roofColor: string;
  hasWindows: boolean;
}

export type PropKind =
  | "pylon" | "streetlight" | "fence" | "container"
  | "tree" | "bush" | "transformer" | "sign";

export interface PropSpec {
  kind: PropKind;
  position: Vec2;
  rotationDeg: number;
  scale: number;
}

export interface ActorSpec {
  id: string;
  /** Must be a VisDrone class so ground truth matches the detector's labels. */
  className: VisDroneClass;
  path: Vec2[];
  speedMs: number;
  /** Metres: along-track, vertical, cross-track. Drives the GT box. */
  size: [number, number, number];
  color: string;
  phase: number;
  jitter: number;
}

export interface CameraSpec {
  path: Vec2[];
  speedMs: number;
  altitudeM: number;
  fovDeg: number;
  /** Gimbal pitch below horizon, degrees. 90 = straight down. */
  gimbalPitchDeg: number;
  vibration: number;
  sensorWidth: number;
  sensorHeight: number;
}

export interface WorldSpec {
  seed: number;
  scenario: string;
  environment: EnvironmentSpec;
  terrain: TerrainSpec;
  roads: RoadSpec[];
  rails: RailSpec[];
  buildings: BuildingSpec[];
  props: PropSpec[];
  actors: ActorSpec[];
  camera: CameraSpec;
}

export interface WorldOptions {
  timeOfDay?: number;
  weather?: Weather;
  altitudeM?: number;
  /** Multiplier on default actor count. Higher = denser, harder scenes. */
  density?: number;
  scenario?: "railway_yard" | "substation" | "solar_farm" | "perimeter";
}

const WEATHERS: Weather[] = ["clear", "partly_cloudy", "overcast", "rain", "fog"];

function makeEnvironment(rng: Rng, opts: WorldOptions): EnvironmentSpec {
  const timeOfDay = opts.timeOfDay ?? rng.range(5.0, 21.0);
  const weather = opts.weather ?? rng.pick(WEATHERS);
  const raining = weather === "rain";
  const foggy = weather === "fog";
  const overcast = weather === "overcast" || raining;

  return {
    timeOfDay,
    weather,
    turbidity: foggy ? rng.range(8, 14) : overcast ? rng.range(5, 9) : rng.range(2, 5),
    rayleigh: overcast ? rng.range(0.5, 1.5) : rng.range(1.5, 3.5),
    fogDensity: foggy ? rng.range(0.012, 0.03) : raining ? rng.range(0.004, 0.01) : rng.range(0.0008, 0.003),
    rainIntensity: raining ? rng.range(3000, 9000) : 0,
    groundWetness: raining ? rng.range(0.55, 0.95) : overcast ? rng.range(0.1, 0.35) : rng.range(0, 0.1),
    windSpeedMs: rng.range(0, 12),
    windDirectionDeg: rng.range(0, 360),
  };
}

const VEHICLE_TEMPLATES: {
  className: VisDroneClass;
  size: [number, number, number];
  speed: [number, number];
  weight: number;
}[] = [
  { className: "car", size: [4.4, 1.5, 1.8], speed: [6, 14], weight: 5 },
  { className: "van", size: [5.4, 2.2, 2.0], speed: [5, 12], weight: 2 },
  { className: "truck", size: [8.5, 3.2, 2.5], speed: [4, 9], weight: 1.5 },
  { className: "bus", size: [11.0, 3.2, 2.55], speed: [4, 9], weight: 0.8 },
  { className: "motor", size: [2.0, 1.3, 0.8], speed: [7, 16], weight: 1.5 },
  { className: "bicycle", size: [1.7, 1.1, 0.6], speed: [3, 6], weight: 1.2 },
  { className: "tricycle", size: [2.6, 1.5, 1.1], speed: [3, 7], weight: 0.7 },
  { className: "awning-tricycle", size: [2.8, 2.0, 1.3], speed: [3, 6], weight: 0.5 },
];

function weightedVehicle(rng: Rng) {
  const total = VEHICLE_TEMPLATES.reduce((s, t) => s + t.weight, 0);
  let r = rng.next() * total;
  for (const t of VEHICLE_TEMPLATES) {
    r -= t.weight;
    if (r <= 0) return t;
  }
  return VEHICLE_TEMPLATES[0];
}

const PERSON_COLORS = [
  "#c0392b", "#2c3e50", "#e67e22", "#16a085", "#8e44ad",
  "#f1c40f", "#34495e", "#d35400", "#7f8c8d", "#2980b9",
];
const VEHICLE_COLORS = [
  "#b7c0c7", "#2c3e50", "#7f8c8d", "#c0392b", "#27548a",
  "#ecf0f1", "#1e2a33", "#8e9aa3", "#34495e", "#a03c2f",
];

function randomPath(rng: Rng, w: number, d: number, segments: number): Vec2[] {
  const pts: Vec2[] = [];
  let x = rng.range(0, w);
  let z = rng.range(0, d);
  pts.push([x, z]);
  for (let i = 0; i < segments; i++) {
    x = Math.max(0, Math.min(w, x + rng.range(-w * 0.3, w * 0.3)));
    z = Math.max(0, Math.min(d, z + rng.range(-d * 0.3, d * 0.3)));
    pts.push([x, z]);
  }
  return pts;
}

/**
 * Build a complete world from a seed. Same seed and options always produce the
 * same world, so a synthetic dataset can be regenerated from `{seed, options}`
 * alone rather than shipping the frames.
 */
export function generateWorld(seed: number, opts: WorldOptions = {}): WorldSpec {
  const rng = new Rng(seed);
  const scenario = opts.scenario ?? "railway_yard";

  const W = 260;
  const D = 160;
  const density = opts.density ?? 1.0;
  const environment = makeEnvironment(rng, opts);

  const terrain: TerrainSpec = {
    width: W,
    depth: D,
    reliefAmplitude: rng.range(0.6, 2.4),
    baseColor: "#4a5236",
    patchColors: ["#525a3c", "#414a30", "#5a6142", "#464e34"],
  };

  const roads: RoadSpec[] = [
    {
      points: [[0, 78], [90, 74], [170, 82], [260, 92]],
      width: rng.range(7, 10),
      markings: true,
      surface: "asphalt",
    },
    {
      points: [[70, 0], [66, 60], [72, 120], [68, 160]],
      width: rng.range(5, 7),
      markings: false,
      surface: rng.pick(["gravel", "concrete"] as const),
    },
  ];

  const rails: RailSpec[] =
    scenario === "railway_yard"
      ? [
          { points: [[0, 40], [120, 44], [260, 52]], gauge: 1.435, sleeperSpacing: 0.65 },
          { points: [[0, 50], [120, 54], [260, 62]], gauge: 1.435, sleeperSpacing: 0.65 },
        ]
      : [];

  const buildings: BuildingSpec[] = [];
  const buildingCount = rng.int(7, 12);
  for (let i = 0; i < buildingCount; i++) {
    const style = rng.pick(["warehouse", "office", "substation", "shed"] as const);
    const isLarge = style === "warehouse";
    const onNorth = rng.bool();
    buildings.push({
      position: [rng.range(10, W - 10), onNorth ? rng.range(5, 32) : rng.range(D - 34, D - 6)],
      width: isLarge ? rng.range(22, 40) : rng.range(8, 20),
      depth: isLarge ? rng.range(14, 26) : rng.range(7, 16),
      height: style === "office" ? rng.range(9, 18) : style === "shed" ? rng.range(3, 5) : rng.range(6, 11),
      rotationDeg: rng.bool(0.7) ? rng.range(-4, 4) : rng.range(85, 95),
      style,
      color: rng.pick(["#8a8f94", "#6f7378", "#9aa0a6", "#7d8489", "#a8a0a5"]),
      roofColor: rng.pick(["#4a4f54", "#3d4247", "#565b60"]),
      hasWindows: style === "office" || style === "warehouse",
    });
  }

  // Static clutter. Real aerial scenes are full of small objects that create
  // occlusion and shadow; a bare plane is the single biggest tell of a fake.
  const props: PropSpec[] = [];
  const pushProps = (kind: PropKind, count: number, band?: [number, number]) => {
    for (let i = 0; i < count; i++) {
      props.push({
        kind,
        position: [rng.range(2, W - 2), band ? rng.range(band[0], band[1]) : rng.range(2, D - 2)],
        rotationDeg: rng.range(0, 360),
        scale: rng.range(0.85, 1.25),
      });
    }
  };
  pushProps("tree", rng.int(18, 34));
  pushProps("bush", rng.int(20, 40));
  pushProps("streetlight", rng.int(8, 14), [70, 96]);
  pushProps("pylon", rng.int(3, 6));
  pushProps("container", rng.int(6, 14));
  pushProps("transformer", scenario === "substation" ? rng.int(6, 12) : rng.int(1, 3));
  pushProps("sign", rng.int(4, 9), [68, 98]);

  const actors: ActorSpec[] = [];

  const pedestrianCount = Math.round(rng.int(8, 16) * density);
  for (let i = 0; i < pedestrianCount; i++) {
    // VisDrone distinguishes a moving individual (`pedestrian`) from a static
    // or grouped one (`people`). Model both so class balance is realistic.
    const isGrouped = rng.bool(0.3);
    actors.push({
      id: `ped-${i}`,
      className: isGrouped ? "people" : "pedestrian",
      path: randomPath(rng, W, D, rng.int(2, 5)),
      speedMs: isGrouped ? rng.range(0.2, 0.8) : rng.range(0.9, 1.8),
      size: [0.5, 1.75, 0.4],
      color: rng.pick(PERSON_COLORS),
      phase: rng.range(0, 200),
      jitter: rng.range(0.1, 0.4),
    });
  }

  const vehicleCount = Math.round(rng.int(6, 12) * density);
  for (let i = 0; i < vehicleCount; i++) {
    const t = weightedVehicle(rng);
    const onRoad = rng.bool(0.75);
    const road = rng.pick(roads);
    const path = onRoad
      ? (rng.bool() ? road.points : [...road.points].reverse())
      : randomPath(rng, W, D, rng.int(2, 4));
    actors.push({
      id: `veh-${i}`,
      className: t.className,
      path,
      speedMs: rng.range(t.speed[0], t.speed[1]),
      size: t.size,
      color: rng.pick(VEHICLE_COLORS),
      phase: rng.range(0, 400),
      jitter: 0,
    });
  }

  const camera: CameraSpec = {
    path: [[10, 70], [80, 68], [150, 80], [230, 90]],
    speedMs: rng.range(8, 16),
    altitudeM: opts.altitudeM ?? rng.range(28, 75),
    fovDeg: rng.range(52, 68),
    gimbalPitchDeg: rng.range(38, 72),
    vibration: rng.range(0.002, 0.012),
    sensorWidth: 960,
    sensorHeight: 540,
  };

  return { seed, scenario, environment, terrain, roads, rails, buildings, props, actors, camera };
}

/**
 * Approximate sun elevation and azimuth from local solar time. Not an
 * ephemeris: a smooth arc that is low and warm near sunrise and sunset and high
 * near noon, which is what the renderer needs.
 */
export function sunPosition(timeOfDay: number): { elevationDeg: number; azimuthDeg: number } {
  const dayFraction = (timeOfDay - 6) / 12;
  const elevationDeg = Math.sin(dayFraction * Math.PI) * 62;
  const azimuthDeg = 90 + dayFraction * 180;
  return { elevationDeg, azimuthDeg };
}

export function isDaylight(timeOfDay: number): boolean {
  return sunPosition(timeOfDay).elevationDeg > 0.5;
}

export function pathLength(points: Vec2[]): number {
  let total = 0;
  for (let i = 0; i < points.length - 1; i++) {
    total += Math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]);
  }
  return total;
}

export function pathAt(points: Vec2[], distance: number): { pos: Vec2; headingRad: number } {
  if (points.length === 0) return { pos: [0, 0], headingRad: 0 };
  if (points.length === 1) return { pos: points[0], headingRad: 0 };

  const total = pathLength(points);
  if (total <= 0) return { pos: points[0], headingRad: 0 };

  // Ping-pong rather than wrap: teleporting from the end back to the start
  // produces an impossible jump that a tracker would have to explain.
  const cycle = total * 2;
  let d = ((distance % cycle) + cycle) % cycle;
  let reversed = false;
  if (d > total) {
    d = cycle - d;
    reversed = true;
  }

  for (let i = 0; i < points.length - 1; i++) {
    const [ax, az] = points[i];
    const [bx, bz] = points[i + 1];
    const len = Math.hypot(bx - ax, bz - az);
    if (len <= 0) continue;
    if (d <= len) {
      const t = d / len;
      const heading = Math.atan2(bz - az, bx - ax);
      return {
        pos: [ax + (bx - ax) * t, az + (bz - az) * t],
        headingRad: reversed ? heading + Math.PI : heading,
      };
    }
    d -= len;
  }
  const last = points[points.length - 1];
  const prev = points[points.length - 2];
  const heading = Math.atan2(last[1] - prev[1], last[0] - prev[0]);
  return { pos: last, headingRad: reversed ? heading + Math.PI : heading };
}
