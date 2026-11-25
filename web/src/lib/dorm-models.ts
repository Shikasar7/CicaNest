import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'

import { CELL, FURNITURE, type Kind, type RoomPlan } from './dorm.ts'

/** The room and its furniture for lib/dorm-scene.ts, built from simple rounded shapes: toon shading and a thin
 * dark outline, to go with the companions' drawn pictures. No model files. */

const INK = 0x5a4650
const OUTLINE = 0.011
export const WALL_H = 3

const steps = new THREE.DataTexture(new Uint8Array([170, 222, 255]), 3, 1, THREE.RedFormat)
steps.minFilter = steps.magFilter = THREE.NearestFilter
steps.needsUpdate = true

const outline = new THREE.MeshBasicMaterial({ color: INK, side: THREE.BackSide })
outline.onBeforeCompile = (shader) => {
  shader.vertexShader = shader.vertexShader.replace(
    '#include <begin_vertex>',
    `vec3 transformed = position + normalize(normal) * ${OUTLINE.toFixed(4)};`,
  )
}

export function toon(color: THREE.ColorRepresentation, more: THREE.MeshToonMaterialParameters = {}) {
  return new THREE.MeshToonMaterial({ color, gradientMap: steps, ...more })
}

type Solid = { line?: boolean; cast?: boolean; material?: THREE.Material }

function solid(geometry: THREE.BufferGeometry, color: THREE.ColorRepresentation, { line = true, cast = true, material }: Solid = {}) {
  const mesh = new THREE.Mesh(geometry, material ?? toon(color))
  mesh.castShadow = cast
  mesh.receiveShadow = true
  if (line) mesh.add(new THREE.Mesh(geometry, outline))
  return mesh
}

/** A box with round edges, standing at x, y, z (its bottom's middle). */
function box(w: number, h: number, d: number, color: THREE.ColorRepresentation, x = 0, y = 0, z = 0, round = 0.05, more: Solid = {}) {
  const mesh = solid(new RoundedBoxGeometry(w, h, d, 3, Math.min(round, w / 2.01, h / 2.01, d / 2.01)), color, more)
  mesh.position.set(x, y + h / 2, z)
  return mesh
}

/** Something round, turned from its outline: [radius, height] from the bottom up. */
function turned(points: [number, number][], color: THREE.ColorRepresentation, x = 0, y = 0, z = 0, more: Solid = {}) {
  const geometry = new THREE.LatheGeometry(
    points.map(([r, h]) => new THREE.Vector2(r, h)),
    28,
  )
  const mesh = solid(geometry, color, more)
  mesh.position.set(x, y, z)
  return mesh
}

function canvas(w: number, h: number, draw: (g: CanvasRenderingContext2D) => void) {
  const el = document.createElement('canvas')
  el.width = w
  el.height = h
  draw(el.getContext('2d')!)
  const texture = new THREE.CanvasTexture(el)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 8
  return texture
}

// -- furniture: each in its own cells, its corner at 0, 0 -----------------------------------------------------

function sofa() {
  const g = new THREE.Group()
  const [w, d] = [FURNITURE.sofa.w * CELL, FURNITURE.sofa.d * CELL]
  const [body, seat] = ['#e7a7a0', '#f0b9b0']
  g.add(box(w - 0.06, 0.26, d - 0.08, body, w / 2, 0.1, d / 2, 0.08))
  g.add(box(w - 0.1, 0.85, 0.28, body, w / 2, 0.1, 0.16, 0.12))
  for (const x of [0.16, w - 0.16]) g.add(box(0.28, 0.56, d - 0.06, body, x, 0.1, d / 2, 0.12))
  for (const x of [w / 2 - 0.48, w / 2 + 0.48]) {
    g.add(box(0.94, 0.16, d - 0.34, seat, x, 0.3, d / 2 + 0.12, 0.07))
    g.add(box(0.9, 0.4, 0.18, seat, x, 0.44, 0.34, 0.09))
  }
  const cushion = (x: number, color: string, turn: number) => {
    const c = box(0.38, 0.38, 0.14, color, x, 0.46, 0.5, 0.07)
    c.rotation.set(-0.25, 0, turn)
    g.add(c)
  }
  cushion(w / 2 - 0.02, '#f7e3bf', 0.12)
  cushion(w - 0.46, '#a8c8dc', -0.2)
  for (const x of [0.2, w - 0.2]) for (const z of [0.18, d - 0.2]) g.add(turned([[0.03, 0], [0.045, 0.12]], '#a97b58', x, 0, z))
  return g
}

function table() {
  const g = new THREE.Group()
  const [w, d] = [FURNITURE.table.w * CELL, FURNITURE.table.d * CELL]
  const wood = '#c9956c'
  g.add(box(w - 0.12, 0.08, d - 0.16, wood, w / 2, 0.36, d / 2, 0.04))
  g.add(box(w - 0.3, 0.04, d - 0.34, '#b98660', w / 2, 0.16, d / 2, 0.02))
  for (const x of [0.16, w - 0.16]) for (const z of [0.18, d - 0.18]) g.add(turned([[0.028, 0], [0.04, 0.37]], '#b07f58', x, 0, z))
  // A pot of tea, two cups, a book.
  const pot = (x: number, z: number) => {
    g.add(turned([[0, 0], [0.09, 0.01], [0.125, 0.07], [0.1, 0.14], [0.04, 0.165], [0, 0.17]], '#f4efe6', x, 0.44, z))
    g.add(turned([[0, 0], [0.028, 0.01], [0.02, 0.035], [0, 0.045]], '#d9b98a', x, 0.605, z))
    const spout = box(0.12, 0.035, 0.035, '#f4efe6', x + 0.14, 0.51, z, 0.017)
    spout.rotation.z = 0.6
    g.add(spout)
  }
  pot(0.62, 0.42)
  for (const [x, z] of [[0.92, 0.62], [0.95, 0.32]]) {
    g.add(turned([[0.07, 0], [0.085, 0.012], [0.03, 0.016]], '#e8ddd0', x, 0.44, z))
    g.add(turned([[0, 0], [0.035, 0.004], [0.052, 0.07], [0.046, 0.07], [0.03, 0.02], [0, 0.02]], '#f4efe6', x, 0.452, z))
  }
  g.add(box(0.28, 0.035, 0.2, '#8fb3a5', 0.34, 0.44, 0.62, 0.012))
  const open = box(0.26, 0.03, 0.19, '#d98f8a', 0.35, 0.475, 0.63, 0.012)
  open.rotation.y = 0.35
  g.add(open)
  return g
}

/** What is on: a boat on the sea. */
function programme() {
  return canvas(320, 178, (c) => {
    const sky = c.createLinearGradient(0, 0, 0, 178)
    sky.addColorStop(0, '#9fd0f3')
    sky.addColorStop(0.6, '#f9e6cf')
    c.fillStyle = sky
    c.fillRect(0, 0, 320, 178)
    c.fillStyle = '#fff3c9'
    c.beginPath()
    c.arc(236, 78, 26, 0, 7)
    c.fill()
    c.fillStyle = '#5c9cc4'
    c.fillRect(0, 108, 320, 70)
    c.strokeStyle = 'rgba(255,255,255,.55)'
    c.lineWidth = 4
    for (const [x, y] of [[40, 126], [196, 134], [96, 154], [248, 160]]) {
      c.beginPath()
      c.moveTo(x, y)
      c.quadraticCurveTo(x + 16, y - 7, x + 32, y)
      c.stroke()
    }
    c.fillStyle = '#a9674f'
    c.beginPath()
    c.moveTo(84, 100)
    c.lineTo(168, 100)
    c.lineTo(152, 122)
    c.lineTo(100, 122)
    c.fill()
    c.fillStyle = '#fffaf2'
    c.beginPath()
    c.moveTo(124, 36)
    c.lineTo(124, 94)
    c.lineTo(164, 94)
    c.fill()
    c.fillStyle = '#f0a79a'
    c.beginPath()
    c.moveTo(118, 46)
    c.lineTo(118, 94)
    c.lineTo(90, 94)
    c.fill()
  })
}

function tv() {
  const g = new THREE.Group()
  const d = FURNITURE.tv.d * CELL
  const wood = '#b98a66'
  g.add(box(0.42, 0.4, d - 0.1, wood, 0.25, 0.08, d / 2, 0.04))
  for (const z of [0.2, d - 0.2]) g.add(box(0.3, 0.08, 0.08, '#8f6a4c', 0.25, 0, z, 0.02))
  for (const z of [d * 0.27, d * 0.73]) {
    g.add(box(0.03, 0.26, d * 0.4, '#cfa27c', 0.455, 0.15, z, 0.012))
    g.add(box(0.03, 0.03, 0.14, '#8f6a4c', 0.48, 0.27, z, 0.012))
  }
  g.add(box(0.2, 0.04, 0.5, '#3f3b4b', 0.25, 0.48, d / 2, 0.02))
  g.add(box(0.06, 0.14, 0.12, '#3f3b4b', 0.25, 0.5, d / 2, 0.02))
  g.add(box(0.07, 0.8, 1.36, '#3f3b4b', 0.25, 0.6, d / 2, 0.03))
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.26, 0.7), new THREE.MeshBasicMaterial({ color: '#2a2833', map: programme() }))
  screen.rotation.y = Math.PI / 2
  screen.position.set(0.292, 1, d / 2)
  screen.name = 'screen'
  g.add(screen)
  // A little house and a figurine beside it.
  g.add(turned([[0, 0], [0.07, 0.01], [0.085, 0.1], [0.05, 0.19], [0, 0.2]], '#e9c46a', 0.26, 0.48, 0.16))
  g.add(box(0.14, 0.2, 0.14, '#9db8d9', 0.26, 0.48, d - 0.16, 0.03))
  return g
}

function lamp() {
  const g = new THREE.Group()
  const c = CELL / 2
  g.add(turned([[0, 0], [0.16, 0.005], [0.17, 0.035], [0.03, 0.06], [0.016, 0.09], [0.016, 1.5], [0, 1.5]], '#8f6a4c', c, 0, c))
  const shade = turned([[0.23, 0], [0.235, 0.015], [0.15, 0.34], [0.14, 0.345]], '#fff0cf', c, 1.38, c, {
    material: toon('#fff0cf', { side: THREE.DoubleSide, emissive: '#ffcf7d', emissiveIntensity: 0 }),
    line: false,
  })
  shade.name = 'shade'
  g.add(shade)
  g.add(turned([[0.235, 0], [0.245, 0.01], [0.235, 0.02]], '#d9b98a', c, 1.375, c, { line: false }))
  g.add(turned([[0.145, 0], [0.152, 0.008], [0.145, 0.016]], '#d9b98a', c, 1.715, c, { line: false }))
  return g
}

function shelf() {
  const g = new THREE.Group()
  const w = FURNITURE.shelf.w * CELL
  const wood = '#c79a72'
  const [d, h] = [0.4, 1.75]
  g.add(box(w - 0.1, h, 0.04, '#a97f5c', w / 2, 0, 0.05, 0.01))
  for (const x of [0.07, w - 0.07]) g.add(box(0.06, h, d, wood, x, 0, d / 2 + 0.02, 0.02))
  const boards = [0.04, 0.46, 0.88, 1.3, 1.7]
  for (const y of boards) g.add(box(w - 0.1, 0.05, d, wood, w / 2, y, d / 2 + 0.02, 0.02))
  const colours = ['#d98f8a', '#8fb3a5', '#e9c46a', '#9db8d9', '#b9a8dd', '#f0d6b8', '#c97b84', '#7f9ac2']
  let n = 7
  const next = () => (n = (n * 73 + 41) % 97)
  for (const [row, y] of boards.slice(0, 4).entries()) {
    let x = 0.14
    const stop = row === 1 ? w * 0.6 : row === 3 ? w * 0.5 : w - 0.2
    while (x < stop) {
      const thick = 0.045 + (next() % 5) * 0.009
      const tall = 0.24 + (next() % 6) * 0.018
      const book = box(thick, tall, 0.24, colours[next() % colours.length], x + thick / 2, y + 0.05, 0.22, 0.008, { cast: false })
      if (next() % 9 === 0) book.rotation.z = -0.14
      g.add(book)
      x += thick + 0.008 + (next() % 11 === 0 ? 0.06 : 0)
    }
  }
  // A clock, a small plant, a box.
  g.add(turned([[0, 0], [0.11, 0.01], [0.12, 0.04], [0, 0.05]], '#f4efe6', w - 0.36, 0.64, 0.25).rotateX(Math.PI / 2))
  g.add(turned([[0.06, 0], [0.085, 0.1], [0.07, 0.1], [0, 0.09]], '#c9785c', w - 0.34, 1.35, 0.22))
  for (const [x, y, z] of [[0, 0.16, 0], [0.07, 0.13, 0.03], [-0.07, 0.14, 0.02]]) g.add(ball(0.08, '#7fae84', w - 0.34 + x, 1.35 + y, 0.22 + z))
  g.add(box(0.3, 0.18, 0.24, '#e8ddd0', 0.4, 1.75, 0.22, 0.03))
  return g
}

function ball(r: number, color: string, x: number, y: number, z: number) {
  const mesh = solid(new THREE.SphereGeometry(r, 18, 14), color)
  mesh.position.set(x, y, z)
  return mesh
}

function plant() {
  const g = new THREE.Group()
  const c = CELL / 2
  g.add(turned([[0, 0], [0.13, 0.005], [0.19, 0.3], [0.2, 0.34], [0.17, 0.34], [0.15, 0.28], [0, 0.28]], '#c9785c', c, 0, c))
  const leaf = new THREE.SphereGeometry(1, 14, 10)
  for (let n = 0; n < 11; n++) {
    const turn = n * 2.399
    const up = n < 5 ? 0.35 : n < 9 ? 0.75 : 1.2
    const holder = new THREE.Group()
    const mesh = solid(leaf.clone().scale(0.11, 0.34, 0.035), n % 2 ? '#7fae84' : '#6a9d76')
    mesh.position.y = 0.34
    holder.add(mesh)
    holder.rotation.set(0, turn, 0)
    holder.position.set(c, 0.3, c)
    mesh.rotation.x = 1.35 - up
    mesh.position.set(0, 0.16 + up * 0.28, 0.3 - up * 0.22)
    g.add(holder)
  }
  return g
}

function beanbag() {
  const g = new THREE.Group()
  const [x, z] = [(FURNITURE.beanbag.w * CELL) / 2, (FURNITURE.beanbag.d * CELL) / 2]
  // A soft bag: wide below, its back drawn up.
  const geometry = new THREE.SphereGeometry(0.5, 40, 28)
  const at = geometry.attributes.position as THREE.BufferAttribute
  for (let n = 0; n < at.count; n++) {
    let [px, py, pz] = [at.getX(n), at.getY(n), at.getZ(n)]
    const rear = THREE.MathUtils.smoothstep(-pz, -0.05, 0.42)
    py = py < 0 ? py * 0.36 : py * (0.34 + rear * 0.95)
    const wide = 1.06 - Math.max(0, py) * 0.35
    px *= wide
    pz = pz * 1.02 - Math.max(0, py) * 0.12
    at.setXYZ(n, px, py + 0.18, pz)
  }
  geometry.computeVertexNormals()
  const bag = solid(geometry, '#b9a8dd')
  bag.position.set(x, 0, z)
  g.add(bag)
  return g
}

function rug() {
  const g = new THREE.Group()
  const [w, d] = [FURNITURE.rug.w * CELL, FURNITURE.rug.d * CELL]
  const map = canvas(512, 440, (c) => {
    c.fillStyle = '#dfe6f2'
    c.fillRect(0, 0, 512, 440)
    c.strokeStyle = '#a9b9d8'
    c.lineWidth = 16
    c.strokeRect(22, 22, 468, 396)
    c.strokeStyle = '#f3d9d6'
    c.lineWidth = 6
    c.strokeRect(46, 46, 420, 348)
    c.fillStyle = '#c3d0e8'
    for (let x = 0; x < 6; x++) {
      for (let y = 0; y < 5; y++) {
        const [px, py] = [96 + x * 64, 92 + y * 64]
        c.beginPath()
        c.moveTo(px, py - 13)
        c.lineTo(px + 13, py)
        c.lineTo(px, py + 13)
        c.lineTo(px - 13, py)
        c.fill()
      }
    }
  })
  const mesh = box(w - 0.1, 0.03, d - 0.1, '#ffffff', w / 2, 0, d / 2, 0.014, { cast: false, line: false })
  ;(mesh.material as THREE.MeshToonMaterial).color.set('#b7c4dd')
  g.add(mesh)
  const top = new THREE.Mesh(new THREE.PlaneGeometry(w - 0.14, d - 0.14), toon('#ffffff', { map }))
  top.rotation.x = -Math.PI / 2
  top.position.set(w / 2, 0.032, d / 2)
  top.receiveShadow = true
  g.add(top)
  return g
}

const MAKE: Record<Kind, () => THREE.Group> = { sofa, table, tv, lamp, shelf, plant, beanbag, rug }

export function furniture(kind: Kind): THREE.Group {
  const g = MAKE[kind]()
  g.name = kind
  return g
}

// -- the room ------------------------------------------------------------------------------------------------

function floorTexture(w: number, d: number) {
  return canvas(1024, Math.round((1024 * d) / w), (c) => {
    const [W, H] = [c.canvas.width, c.canvas.height]
    const rows = 12
    const tall = H / rows
    let n = 3
    const next = () => (n = (n * 61 + 29) % 101)
    for (let row = 0; row < rows; row++) {
      let x = -((next() / 101) * 240)
      while (x < W) {
        const long = 200 + next() * 1.6
        c.fillStyle = `hsl(${27 + (next() % 5)} ${42 + (next() % 8)}% ${69 + (next() % 7)}%)`
        c.fillRect(x, row * tall, long, tall)
        c.strokeStyle = 'rgba(120,78,52,.32)'
        c.lineWidth = 2
        c.strokeRect(x, row * tall, long, tall)
        c.strokeStyle = 'rgba(140,96,66,.12)'
        for (let k = 0; k < 2; k++) {
          const y = row * tall + tall * (0.3 + k * 0.35)
          c.beginPath()
          c.moveTo(x + 14, y)
          c.quadraticCurveTo(x + long / 2, y + (next() % 9) - 4, x + long - 14, y)
          c.stroke()
        }
        x += long
      }
    }
  })
}

const WINDOW = { z: 1.75, w: 1.5, y: 0.95, h: 1.35 } // on the left wall, its middle

/** Floor, two walls, the window, the door; `sky` is what is seen through the window. */
export function shell(room: RoomPlan): { group: THREE.Group; sky: THREE.Mesh; patch: THREE.Mesh } {
  const g = new THREE.Group()
  const [w, d] = [room.w * CELL, room.d * CELL]
  const floor = new THREE.Mesh(new THREE.BoxGeometry(w, 0.24, d), [
    toon('#b98a66'),
    toon('#b98a66'),
    toon('#ffffff', { map: floorTexture(w, d) }),
    toon('#8f6a4c'),
    toon('#b98a66'),
    toon('#b98a66'),
  ])
  floor.position.set(w / 2, -0.12, d / 2)
  floor.receiveShadow = true
  g.add(floor)

  const [left, right, cap, skirting] = ['#f4e2d2', '#f9ecdd', '#fffaf2', '#fff6ea']
  const thick = 0.16
  // The wall along z (left), with a hole for the window.
  const shape = new THREE.Shape([
    new THREE.Vector2(0, 0),
    new THREE.Vector2(d + thick, 0),
    new THREE.Vector2(d + thick, WALL_H),
    new THREE.Vector2(0, WALL_H),
  ])
  const z0 = thick + WINDOW.z - WINDOW.w / 2
  shape.holes.push(
    new THREE.Path([
      new THREE.Vector2(z0, WINDOW.y),
      new THREE.Vector2(z0, WINDOW.y + WINDOW.h),
      new THREE.Vector2(z0 + WINDOW.w, WINDOW.y + WINDOW.h),
      new THREE.Vector2(z0 + WINDOW.w, WINDOW.y),
    ]),
  )
  const wall = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: thick, bevelEnabled: false }), toon(left))
  wall.rotation.y = -Math.PI / 2
  wall.position.set(0, 0, -thick)
  wall.receiveShadow = true
  g.add(wall)
  const back = new THREE.Mesh(new THREE.BoxGeometry(w, WALL_H, thick), toon(right))
  back.position.set(w / 2, WALL_H / 2, -thick / 2)
  back.receiveShadow = true
  g.add(back)
  // Faint stripes on the paper, a skirting board, a light edge on top.
  for (let x = 0.35; x < w; x += 0.7) {
    const stripe = new THREE.Mesh(new THREE.PlaneGeometry(0.28, WALL_H - 0.16), toon('#f5e3d0'))
    stripe.position.set(x, WALL_H / 2 + 0.08, 0.003)
    stripe.receiveShadow = true
    g.add(stripe)
  }
  g.add(box(w, 0.14, 0.05, skirting, w / 2, 0, 0.025, 0.015, { line: false, cast: false }))
  g.add(box(0.05, 0.14, d, skirting, 0.025, 0, d / 2, 0.015, { line: false, cast: false }))
  g.add(box(w + thick, 0.06, thick + 0.04, cap, w / 2 - thick / 2, WALL_H, -thick / 2, 0.02, { line: false, cast: false }))
  g.add(box(thick + 0.04, 0.06, d + thick, cap, -thick / 2, WALL_H, d / 2 - thick / 2, 0.02, { line: false, cast: false }))

  // The window: what is outside, the frame, the sill, the curtains.
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(WINDOW.w + 1.6, WINDOW.h + 1.2), new THREE.MeshBasicMaterial())
  sky.rotation.y = Math.PI / 2
  sky.position.set(-thick - 0.02, WINDOW.y + WINDOW.h / 2, WINDOW.z)
  sky.scale.set(WINDOW.w / (WINDOW.w + 1.6), WINDOW.h / (WINDOW.h + 1.2), 1)
  g.add(sky)
  const frame = '#fffaf2'
  const bar = (y: number, z: number, h: number, len: number, deep = 0.1) => g.add(box(deep, h, len, frame, -0.02, y, z, 0.02))
  bar(WINDOW.y - 0.04, WINDOW.z, 0.08, WINDOW.w + 0.16, 0.22)
  bar(WINDOW.y + WINDOW.h - 0.04, WINDOW.z, 0.08, WINDOW.w + 0.12)
  for (const z of [-WINDOW.w / 2, 0, WINDOW.w / 2]) bar(WINDOW.y, WINDOW.z + z, WINDOW.h, z ? 0.08 : 0.05)
  bar(WINDOW.y + WINDOW.h * 0.56, WINDOW.z, 0.045, WINDOW.w)
  const rod = box(0.04, 0.04, WINDOW.w + 0.9, '#a97b58', 0.1, WINDOW.y + WINDOW.h + 0.14, WINDOW.z, 0.02)
  g.add(rod)
  for (const side of [-1, 1]) {
    for (let n = 0; n < 3; n++) {
      g.add(box(0.1, WINDOW.h + 0.5, 0.17, n % 2 ? '#e9c9c4' : '#f0d6d0', 0.1, WINDOW.y - 0.34, WINDOW.z + side * (WINDOW.w / 2 + 0.02 + n * 0.15), 0.05))
    }
  }

  // The door, at the right wall's end.
  const door = (room.door.x + 0.5) * CELL
  g.add(box(0.98, 2.16, 0.06, '#fffaf2', door, 0, 0.02, 0.02))
  g.add(box(0.8, 2.04, 0.06, '#c79a72', door, 0, 0.05, 0.03))
  for (const y of [0.22, 1.16]) g.add(box(0.5, 0.7, 0.03, '#b98a66', door, y, 0.08, 0.02, { line: false }))
  g.add(ball(0.04, '#e9c46a', door - 0.3, 1.02, 0.12))

  // Pictures over the sofa, and bunting.
  const picture = (x: number, y: number, pw: number, ph: number, draw: (c: CanvasRenderingContext2D) => void) => {
    g.add(box(pw + 0.1, ph + 0.1, 0.05, '#a97b58', x, y - 0.05, 0.03, 0.02))
    const art = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph), toon('#ffffff', { map: canvas(256, Math.round((256 * ph) / pw), draw) }))
    art.position.set(x, y + ph / 2, 0.058)
    g.add(art)
  }
  picture(2.75, 1.45, 0.72, 0.54, (c) => {
    c.fillStyle = '#cfe3ee'
    c.fillRect(0, 0, 256, 192)
    c.fillStyle = '#fff3d6'
    c.beginPath()
    c.arc(190, 52, 24, 0, 7)
    c.fill()
    c.fillStyle = '#9cbfa7'
    c.beginPath()
    c.ellipse(70, 190, 120, 70, 0, 0, 7)
    c.fill()
    c.fillStyle = '#7fa98f'
    c.beginPath()
    c.ellipse(200, 200, 110, 60, 0, 0, 7)
    c.fill()
  })
  picture(3.72, 1.6, 0.42, 0.5, (c) => {
    c.fillStyle = '#f7e7ea'
    c.fillRect(0, 0, 256, 305)
    c.fillStyle = '#d98f8a'
    for (let n = 0; n < 6; n++) {
      c.beginPath()
      c.ellipse(128 + Math.cos(n * 1.047) * 46, 130 + Math.sin(n * 1.047) * 46, 34, 34, 0, 0, 7)
      c.fill()
    }
    c.fillStyle = '#f2cf7e'
    c.beginPath()
    c.arc(128, 130, 28, 0, 7)
    c.fill()
    c.strokeStyle = '#7fae84'
    c.lineWidth = 10
    c.beginPath()
    c.moveTo(128, 210)
    c.lineTo(128, 290)
    c.stroke()
  })

  // Where the light of the window falls.
  const patch = new THREE.Mesh(
    new THREE.BufferGeometry().setAttribute(
      'position',
      new THREE.Float32BufferAttribute(
        [0.5, 0, WINDOW.z - 0.5, 0.5, 0, WINDOW.z + 0.75, 2.3, 0, WINDOW.z + 1.15, 2.3, 0, WINDOW.z - 0.1],
        3,
      ),
    ),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }),
  )
  ;(patch.geometry as THREE.BufferGeometry).setIndex([0, 1, 2, 0, 2, 3])
  patch.position.y = 0.04
  g.add(patch)
  return { group: g, sky, patch }
}

export type SkyKind = 'morning' | 'day' | 'evening' | 'night'

export function skyTexture(kind: SkyKind) {
  const [top, low] = {
    morning: ['#bfe0f5', '#ffe9c9'],
    day: ['#8fc9f0', '#dff2fb'],
    evening: ['#8a7fc0', '#ffb88a'],
    night: ['#1f2550', '#3d4684'],
  }[kind]
  return canvas(256, 232, (c) => {
    const fill = c.createLinearGradient(0, 0, 0, 232)
    fill.addColorStop(0, top)
    fill.addColorStop(1, low)
    c.fillStyle = fill
    c.fillRect(0, 0, 256, 232)
    if (kind === 'night') {
      c.fillStyle = '#fff7d9'
      for (let n = 0; n < 22; n++) c.fillRect((n * 97) % 251, (n * 53) % 150, n % 4 ? 2 : 3, n % 4 ? 2 : 3)
      c.beginPath()
      c.arc(188, 58, 24, 0, 7)
      c.fill()
      c.fillStyle = '#262d5c'
      c.beginPath()
      c.arc(176, 50, 22, 0, 7)
      c.fill()
    } else {
      c.fillStyle = kind === 'evening' ? 'rgba(255,214,186,.75)' : 'rgba(255,255,255,.9)'
      for (const [x, y, r] of [[60, 70, 18], [82, 62, 24], [108, 72, 17], [176, 110, 15], [196, 104, 20], [216, 112, 14]]) {
        c.beginPath()
        c.arc(x, y, r, 0, 7)
        c.fill()
      }
    }
    c.fillStyle = kind === 'night' ? '#252c58' : kind === 'evening' ? '#7c6a94' : '#9cc3a4'
    c.beginPath()
    c.ellipse(60, 240, 120, 58, 0, 0, 7)
    c.fill()
    c.beginPath()
    c.ellipse(210, 246, 110, 70, 0, 0, 7)
    c.fill()
  })
}
