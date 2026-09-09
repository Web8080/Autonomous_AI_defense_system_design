/**
 * Ground-truth extraction: project actors into screen space and decide which
 * ones an annotator would actually have labelled.
 *
 * Three things the previous implementation got wrong, all of which corrupt a
 * synthetic dataset quietly rather than loudly:
 *
 *  1. Class names. It emitted "person" and "car". VisDrone has no "person"
 *     class, so every pedestrian box carried a label the detector cannot
 *     predict, and evaluation against it scored a guaranteed miss.
 *  2. Occlusion. Every actor was labelled whether or not anything stood between
 *     it and the camera, teaching the model to find objects behind buildings.
 *  3. Truncation. Boxes were clamped to the frame with no record that the
 *     object continued past the edge.
 */
import * as THREE from "three";
import type { ActorSpec, VisDroneClass } from "./worldSpec";

export interface GtBox {
  class_name: VisDroneClass;
  /** Normalised xyxy in [0,1], clamped to the frame. */
  bbox: [number, number, number, number];
  /** Fraction of sample points not blocked by other geometry. */
  visibility: number;
  /** True when the unclamped box extended beyond the frame. */
  truncated: boolean;
  /** Longest box edge in pixels, for slice analysis by object size. */
  pixelSize: number;
  actor_id: string;
}

export interface GtOptions {
  frameWidth: number;
  frameHeight: number;
  minVisibility?: number;
  minPixelSize?: number;
  occlusionSamples?: number;
}

const DEFAULTS = { minVisibility: 0.35, minPixelSize: 6, occlusionSamples: 5 };

/** Corners of the actor's oriented bounding box in world space. */
function orientedCorners(
  center: THREE.Vector3,
  size: [number, number, number],
  headingRad: number,
): THREE.Vector3[] {
  const [len, height, wid] = size;
  const hx = len / 2, hy = height / 2, hz = wid / 2;
  const cos = Math.cos(headingRad), sin = Math.sin(headingRad);
  const out: THREE.Vector3[] = [];
  for (const sx of [-1, 1]) {
    for (const sy of [-1, 1]) {
      for (const sz of [-1, 1]) {
        const lx = sx * hx, lz = sz * hz;
        out.push(new THREE.Vector3(
          center.x + (lx * cos - lz * sin),
          center.y + sy * hy,
          center.z + (lx * sin + lz * cos),
        ));
      }
    }
  }
  return out;
}

/**
 * Estimate visibility by casting rays from the camera to points spread over the
 * actor and counting how many arrive unobstructed.
 *
 * Deliberately cheap: a handful of rays per actor at the capture rate, not per
 * render frame. Exact per-pixel visibility would need a depth prepass, which is
 * not worth the complexity for a value that only gates inclusion.
 */
function estimateVisibility(
  camera: THREE.Camera,
  center: THREE.Vector3,
  size: [number, number, number],
  actorGroup: THREE.Object3D,
  occluders: THREE.Object3D[],
  raycaster: THREE.Raycaster,
  samples: number,
): number {
  const camPos = new THREE.Vector3();
  camera.getWorldPosition(camPos);

  const [len, height, wid] = size;
  const points: THREE.Vector3[] = [
    new THREE.Vector3(center.x, center.y + height * 0.35, center.z),
    new THREE.Vector3(center.x, center.y, center.z),
    new THREE.Vector3(center.x + len * 0.3, center.y, center.z),
    new THREE.Vector3(center.x - len * 0.3, center.y, center.z),
    new THREE.Vector3(center.x, center.y, center.z + wid * 0.3),
    new THREE.Vector3(center.x, center.y, center.z - wid * 0.3),
  ].slice(0, Math.max(1, samples));

  let visible = 0;
  const dir = new THREE.Vector3();
  for (const p of points) {
    dir.subVectors(p, camPos);
    const dist = dir.length();
    dir.normalize();
    raycaster.set(camPos, dir);
    raycaster.far = dist - 0.15; // stop just short of the target
    const hits = raycaster.intersectObjects(occluders, true);
    const blocked = hits.some((h) => {
      let o: THREE.Object3D | null = h.object;
      while (o) {
        if (o === actorGroup) return false; // the actor is not its own occluder
        o = o.parent;
      }
      return true;
    });
    if (!blocked) visible++;
  }
  return visible / points.length;
}

export function extractGroundTruth(
  camera: THREE.PerspectiveCamera,
  actors: { group: THREE.Group; spec: ActorSpec }[],
  occluders: THREE.Object3D[],
  opts: GtOptions,
): GtBox[] {
  const {
    frameWidth, frameHeight,
    minVisibility = DEFAULTS.minVisibility,
    minPixelSize = DEFAULTS.minPixelSize,
    occlusionSamples = DEFAULTS.occlusionSamples,
  } = opts;

  const raycaster = new THREE.Raycaster();
  const out: GtBox[] = [];

  camera.updateMatrixWorld();
  const frustum = new THREE.Frustum().setFromProjectionMatrix(
    new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse),
  );

  for (const { group, spec } of actors) {
    const size = spec.size;
    const center = new THREE.Vector3(group.position.x, size[1] / 2, group.position.z);

    // Cheap reject before any raycasting.
    const radius = Math.max(size[0], size[2]) * 0.75;
    if (!frustum.intersectsSphere(new THREE.Sphere(center, radius))) continue;

    const corners = orientedCorners(center, size, -group.rotation.y);

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    let anyInFront = false;
    for (const c of corners) {
      // three cameras look down -Z, so a point in front has negative z.
      const camSpace = c.clone().applyMatrix4(camera.matrixWorldInverse);
      if (camSpace.z > -0.05) continue;
      anyInFront = true;
      const p = c.clone().project(camera);
      const sx = p.x * 0.5 + 0.5;
      const sy = 1 - (p.y * 0.5 + 0.5);
      if (sx < minX) minX = sx;
      if (sy < minY) minY = sy;
      if (sx > maxX) maxX = sx;
      if (sy > maxY) maxY = sy;
    }
    if (!anyInFront) continue;
    if (maxX <= 0 || maxY <= 0 || minX >= 1 || minY >= 1) continue;

    const truncated = minX < 0 || minY < 0 || maxX > 1 || maxY > 1;
    const x0 = Math.max(0, minX), y0 = Math.max(0, minY);
    const x1 = Math.min(1, maxX), y1 = Math.min(1, maxY);
    if (x1 <= x0 || y1 <= y0) continue;

    const pixelSize = Math.max((x1 - x0) * frameWidth, (y1 - y0) * frameHeight);
    if (pixelSize < minPixelSize) continue;

    const visibility = estimateVisibility(
      camera, center, size, group, occluders, raycaster, occlusionSamples,
    );
    if (visibility < minVisibility) continue;

    out.push({
      class_name: spec.className,
      bbox: [
        Number(x0.toFixed(5)), Number(y0.toFixed(5)),
        Number(x1.toFixed(5)), Number(y1.toFixed(5)),
      ],
      visibility: Number(visibility.toFixed(3)),
      truncated,
      pixelSize: Math.round(pixelSize),
      actor_id: spec.id,
    });
  }

  return out;
}
