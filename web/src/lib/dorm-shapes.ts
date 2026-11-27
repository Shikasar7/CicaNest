import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'

/** What the 3D 小窝 is built from: simple rounded shapes with toon shading and a thin dark outline, to go with the
 * companions' drawn pictures. No model files. */

const INK = 0x5a4650
const OUTLINE = 0.011
export const WALL_H = 3

const steps = new THREE.DataTexture(new Uint8Array([170, 222, 255]), 3, 1, THREE.RedFormat)
steps.minFilter = steps.magFilter = THREE.NearestFilter
steps.needsUpdate = true

export const outline = new THREE.MeshBasicMaterial({ color: INK, side: THREE.BackSide })
outline.onBeforeCompile = (shader) => {
  shader.vertexShader = shader.vertexShader.replace(
    '#include <begin_vertex>',
    `vec3 transformed = position + normalize(normal) * ${OUTLINE.toFixed(4)};`,
  )
}

export function toon(color: THREE.ColorRepresentation, more: THREE.MeshToonMaterialParameters = {}) {
  return new THREE.MeshToonMaterial({ color, gradientMap: steps, ...more })
}

export type Solid = { line?: boolean; cast?: boolean; material?: THREE.Material }

export function solid(geometry: THREE.BufferGeometry, color: THREE.ColorRepresentation, { line = true, cast = true, material }: Solid = {}) {
  const mesh = new THREE.Mesh(geometry, material ?? toon(color))
  mesh.castShadow = cast
  mesh.receiveShadow = true
  if (line) mesh.add(new THREE.Mesh(geometry, outline))
  return mesh
}

/** A box with round edges, standing at x, y, z (its bottom's middle). */
export function box(w: number, h: number, d: number, color: THREE.ColorRepresentation, x = 0, y = 0, z = 0, round = 0.05, more: Solid = {}) {
  const mesh = solid(new RoundedBoxGeometry(w, h, d, 3, Math.min(round, w / 2.01, h / 2.01, d / 2.01)), color, more)
  mesh.position.set(x, y + h / 2, z)
  return mesh
}

/** Something round, turned from its outline: [radius, height] from the bottom up. */
export function turned(points: [number, number][], color: THREE.ColorRepresentation, x = 0, y = 0, z = 0, more: Solid = {}) {
  const geometry = new THREE.LatheGeometry(
    points.map(([r, h]) => new THREE.Vector2(r, h)),
    28,
  )
  const mesh = solid(geometry, color, more)
  mesh.position.set(x, y, z)
  return mesh
}

export function canvas(w: number, h: number, draw: (g: CanvasRenderingContext2D) => void) {
  const el = document.createElement('canvas')
  el.width = w
  el.height = h
  draw(el.getContext('2d')!)
  const texture = new THREE.CanvasTexture(el)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 8
  return texture
}

export function ball(r: number, color: string, x: number, y: number, z: number) {
  const mesh = solid(new THREE.SphereGeometry(r, 18, 14), color)
  mesh.position.set(x, y, z)
  return mesh
}

/** A puffy five-pointed star facing +z, its middle at x, y, z. `glow`: it lights up with the lamps (named 'shade'). */
export function star(r: number, color: string, x: number, y: number, z: number, glow = false) {
  const shape = new THREE.Shape()
  for (let k = 0; k < 10; k++) {
    const [a, far] = [(k * Math.PI) / 5 + Math.PI / 2, k % 2 ? r * 0.5 : r]
    if (k) shape.lineTo(Math.cos(a) * far, Math.sin(a) * far)
    else shape.moveTo(Math.cos(a) * far, Math.sin(a) * far)
  }
  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth: r * 0.3,
    bevelEnabled: true,
    bevelThickness: r * 0.14,
    bevelSize: r * 0.12,
    bevelSegments: 2,
  })
  geometry.center()
  const mesh = solid(geometry, color, glow ? { material: toon(color, { emissive: color, emissiveIntensity: 0 }) } : {})
  if (glow) mesh.name = 'shade'
  mesh.position.set(x, y, z)
  return mesh
}
