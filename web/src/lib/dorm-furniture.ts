import * as THREE from 'three'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'

import { CELL, FURNITURE, type Kind } from './dorm.ts'
import { ball, box, canvas, outline, solid, toon, turned } from './dorm-shapes.ts'

/** The furniture of the 3D 小窝 (lib/dorm.ts says how large it is and what one does on it): each in its own cells, its
 * far corner at 0, 0, facing +z. */

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

// -- more of it ----------------------------------------------------------------------------------------------

const WOOD = '#c9956c'
const DARK = '#a97b58'
const PALE = '#fffaf2'

function legs(g: THREE.Group, xs: number[], zs: number[], h: number, color = '#b07f58', r = 0.032) {
  for (const x of xs) for (const z of zs) g.add(turned([[r * 0.75, 0], [r, h]], color, x, 0, z))
}

/** A stool or a small chair at x, z; `back`: it has one, behind. */
function seat(g: THREE.Group, x: number, z: number, color: string, back = false) {
  g.add(box(0.46, 0.07, 0.46, color, x, 0.34, z, 0.035))
  for (const dx of [-0.17, 0.17]) for (const dz of [-0.17, 0.17]) g.add(turned([[0.02, 0], [0.028, 0.35]], DARK, x + dx, 0, z + dz))
  if (!back) return
  g.add(box(0.44, 0.16, 0.05, WOOD, x, 0.68, z - 0.2, 0.025))
  for (const dx of [-0.17, 0.17]) g.add(box(0.045, 0.5, 0.045, DARK, x + dx, 0.36, z - 0.2, 0.02))
}

function armchair() {
  const g = new THREE.Group()
  const [body, cushion] = ['#8fb3a5', '#a6c6b8']
  g.add(box(0.92, 0.26, 0.86, body, 0.5, 0.1, 0.5, 0.08))
  g.add(box(0.9, 0.86, 0.24, body, 0.5, 0.1, 0.16, 0.11))
  for (const x of [0.15, 0.85]) g.add(box(0.22, 0.5, 0.84, body, x, 0.1, 0.52, 0.1))
  g.add(box(0.52, 0.14, 0.62, cushion, 0.5, 0.3, 0.58, 0.06))
  g.add(box(0.5, 0.38, 0.16, cushion, 0.5, 0.44, 0.32, 0.08))
  legs(g, [0.16, 0.84], [0.16, 0.84], 0.12, DARK, 0.04)
  return g
}

function bench() {
  const g = new THREE.Group()
  for (const z of [0.14, 0.27, 0.4]) g.add(box(1.4, 0.05, 0.11, WOOD, 0.75, 0.34, z, 0.02))
  for (const y of [0.52, 0.68]) g.add(box(1.4, 0.1, 0.04, WOOD, 0.75, y, 0.07, 0.02))
  for (const x of [0.14, 1.36]) {
    g.add(box(0.06, 0.8, 0.06, DARK, x, 0, 0.07, 0.02))
    g.add(box(0.06, 0.35, 0.06, DARK, x, 0, 0.4, 0.02))
    g.add(box(0.06, 0.05, 0.42, DARK, x, 0.5, 0.24, 0.02))
  }
  return g
}

function bed() {
  const g = new THREE.Group()
  const [w, d] = [1.5, 2.5]
  g.add(box(w - 0.06, 0.95, 0.12, WOOD, w / 2, 0, 0.08, 0.05))
  g.add(box(w - 0.06, 0.4, 0.1, WOOD, w / 2, 0, d - 0.07, 0.04))
  g.add(box(w - 0.1, 0.2, d - 0.16, DARK, w / 2, 0.1, d / 2, 0.04))
  g.add(box(w - 0.14, 0.2, d - 0.28, PALE, w / 2, 0.27, d / 2 + 0.02, 0.09))
  const pillow = box(0.78, 0.2, 0.42, '#f2d9dc', w / 2, 0.46, 0.4, 0.1)
  pillow.rotation.x = -0.12
  g.add(pillow)
  // The quilt: over the bed, turned down at the top.
  g.add(box(w - 0.06, 0.2, d - 1.15, '#a9b9d8', w / 2, 0.34, d / 2 + 0.42, 0.09))
  g.add(box(w - 0.05, 0.22, 0.26, '#dfe6f2', w / 2, 0.34, 0.98, 0.1))
  for (let n = 0; n < 3; n++) g.add(box(w - 0.045, 0.206, 0.06, '#c3d0e8', w / 2, 0.34, 1.35 + n * 0.36, 0.028, { line: false, cast: false }))
  return g
}

function dining() {
  const g = new THREE.Group()
  for (const [x, z] of [[0.55, 0.28], [1.45, 0.28]]) seat(g, x, z, '#e9c9a4')
  for (const [x, z] of [[0.55, 1.72], [1.45, 1.72]]) seat(g, x, z, '#e9c9a4')
  g.add(box(1.7, 0.08, 0.92, WOOD, 1, 0.56, 1, 0.04))
  legs(g, [0.28, 1.72], [0.66, 1.34], 0.57)
  g.add(box(0.5, 0.012, 0.9, '#f2d9dc', 1, 0.64, 1, 0.005, { line: false, cast: false }))
  // A bowl of fruit, plates.
  g.add(turned([[0.06, 0], [0.16, 0.09], [0.15, 0.09], [0.05, 0.02], [0, 0.02]], PALE, 1, 0.65, 1))
  for (const [x, z, color] of [[0.95, 0.97, '#e08a72'], [1.06, 1.03, '#e9c46a'], [1.0, 0.93, '#9cbf7a']] as const) g.add(ball(0.06, color, x, 0.75, z))
  for (const [x, z] of [[0.5, 0.75], [1.5, 0.75], [0.5, 1.25], [1.5, 1.25]]) {
    g.add(turned([[0.06, 0], [0.12, 0.018], [0.11, 0.018], [0, 0.008]], PALE, x, 0.645, z))
  }
  return g
}

function bistro() {
  const g = new THREE.Group()
  seat(g, 0.3, 0.5, '#b7d0c4', true)
  seat(g, 1.7, 0.5, '#b7d0c4', true)
  g.add(turned([[0.2, 0], [0.22, 0.02], [0.03, 0.06], [0.03, 0.52], [0, 0.52]], DARK, 1, 0, 0.5))
  g.add(turned([[0, 0], [0.4, 0.005], [0.42, 0.03], [0.4, 0.05], [0, 0.05]], PALE, 1, 0.52, 0.5))
  g.add(turned([[0.035, 0], [0.06, 0.1], [0.03, 0.16], [0.025, 0.16], [0, 0.1]], '#9db8d9', 1, 0.57, 0.42))
  for (const [x, z, color] of [[0.97, 0.4, '#e7a7a0'], [1.04, 0.45, '#e9c46a'], [1, 0.36, '#f4efe6']] as const) g.add(ball(0.045, color, x, 0.8, z))
  for (const x of [0.78, 1.22]) g.add(turned([[0, 0], [0.035, 0.004], [0.052, 0.07], [0.046, 0.07], [0.03, 0.02], [0, 0.02]], PALE, x, 0.57, 0.6))
  return g
}

function chess() {
  const g = new THREE.Group()
  seat(g, 0.3, 0.5, '#e7b7a0')
  seat(g, 1.7, 0.5, '#e7b7a0')
  g.add(box(0.78, 0.07, 0.78, WOOD, 1, 0.5, 0.5, 0.03))
  legs(g, [0.7, 1.3], [0.2, 0.8], 0.5)
  const board = new THREE.Mesh(
    new THREE.PlaneGeometry(0.6, 0.6).rotateX(-Math.PI / 2),
    toon('#ffffff', {
      map: canvas(256, 256, (c2) => {
        c2.fillStyle = '#f0d9b0'
        c2.fillRect(0, 0, 256, 256)
        c2.strokeStyle = '#9b6f4c'
        c2.lineWidth = 3
        for (let n = 0; n < 9; n++) {
          const at = 16 + n * 28
          c2.beginPath()
          c2.moveTo(at, 16)
          c2.lineTo(at, 240)
          c2.moveTo(16, at)
          c2.lineTo(240, at)
          c2.stroke()
        }
      }),
    }),
  )
  board.position.set(1, 0.573, 0.5)
  board.receiveShadow = true
  g.add(board)
  const stones = [[3, 4], [4, 4], [5, 5], [4, 3], [3, 5], [5, 3], [6, 4], [2, 4], [4, 6]]
  for (const [n, [x, z]] of stones.entries()) {
    g.add(turned([[0, 0], [0.022, 0.004], [0.024, 0.012], [0, 0.022]], n % 2 ? '#fffaf2' : '#4a4456', 0.7 + 0.0375 + x * 0.0656, 0.574, 0.2 + 0.0375 + z * 0.0656, { line: false, cast: false }))
  }
  return g
}

function desk() {
  const g = new THREE.Group()
  seat(g, 0.75, 0.3, '#c3b4e0', true)
  g.add(box(1.4, 0.07, 0.56, WOOD, 0.75, 0.6, 0.68, 0.03))
  g.add(box(1.28, 0.34, 0.04, '#b98660', 0.75, 0.26, 0.9, 0.015))
  for (const x of [0.14, 1.36]) g.add(box(0.06, 0.6, 0.5, DARK, x, 0, 0.68, 0.025))
  // A lamp, books, a pencil pot.
  g.add(turned([[0.08, 0], [0.085, 0.02], [0.015, 0.03], [0.015, 0.3], [0, 0.3]], '#4a4456', 1.24, 0.67, 0.66))
  g.add(turned([[0.11, 0], [0.04, 0.1], [0, 0.11]], '#e9c46a', 1.2, 0.93, 0.66))
  for (const [n, color] of ['#d98f8a', '#8fb3a5', '#9db8d9'].entries()) g.add(box(0.28, 0.045, 0.2, color, 0.3, 0.67 + n * 0.046, 0.66, 0.01))
  g.add(turned([[0.04, 0], [0.045, 0.1], [0.04, 0.1], [0.035, 0.02], [0, 0.02]], '#b9a8dd', 0.98, 0.67, 0.78))
  for (const [x, tilt] of [[0.97, 0.15], [0.99, -0.12]]) {
    const pencil = box(0.015, 0.2, 0.015, '#e08a72', x, 0.7, 0.78, 0.005, { line: false })
    pencil.rotation.z = tilt
    g.add(pencil)
  }
  return g
}

function easel() {
  const g = new THREE.Group()
  for (const [x, tilt] of [[0.3, 0.12], [0.7, -0.12]]) {
    const leg = box(0.05, 1.35, 0.05, WOOD, x, 0, 0.2, 0.02)
    leg.rotation.z = -tilt
    g.add(leg)
  }
  const rear = box(0.04, 1.2, 0.04, DARK, 0.5, 0, 0.08, 0.015)
  rear.rotation.x = 0.1
  g.add(rear)
  g.add(box(0.62, 0.05, 0.12, DARK, 0.5, 0.48, 0.24, 0.02))
  g.add(box(0.72, 0.62, 0.04, '#e8ddd0', 0.5, 0.53, 0.25, 0.01))
  const art = new THREE.Mesh(
    new THREE.PlaneGeometry(0.64, 0.54),
    toon('#ffffff', {
      map: canvas(256, 216, (c2) => {
        c2.fillStyle = '#fdf6ea'
        c2.fillRect(0, 0, 256, 216)
        c2.fillStyle = '#bcd9ea'
        c2.fillRect(0, 0, 256, 108)
        c2.fillStyle = '#a9cda5'
        c2.beginPath()
        c2.ellipse(90, 200, 150, 96, 0, 0, 7)
        c2.fill()
        c2.fillStyle = '#f0b3a8'
        for (const [x, y] of [[150, 130], [176, 120], [164, 102], [190, 140]]) {
          c2.beginPath()
          c2.arc(x, y, 22, 0, 7)
          c2.fill()
        }
        c2.fillStyle = '#8f6a4c'
        c2.fillRect(164, 148, 12, 50)
      }),
    }),
  )
  art.position.set(0.5, 0.84, 0.273)
  g.add(art)
  return g
}

function tv() {
  const g = new THREE.Group()
  const w = FURNITURE.tv.w * CELL
  g.add(box(w - 0.1, 0.4, 0.42, '#b98a66', w / 2, 0.08, 0.25, 0.04))
  for (const x of [0.2, w - 0.2]) g.add(box(0.08, 0.08, 0.3, '#8f6a4c', x, 0, 0.25, 0.02))
  for (const x of [w * 0.27, w * 0.73]) {
    g.add(box(w * 0.4, 0.26, 0.03, '#cfa27c', x, 0.15, 0.455, 0.012))
    g.add(box(0.14, 0.03, 0.03, '#8f6a4c', x, 0.27, 0.48, 0.012))
  }
  g.add(box(0.5, 0.04, 0.2, '#3f3b4b', w / 2, 0.48, 0.25, 0.02))
  g.add(box(0.12, 0.14, 0.06, '#3f3b4b', w / 2, 0.5, 0.25, 0.02))
  g.add(box(1.36, 0.8, 0.07, '#3f3b4b', w / 2, 0.6, 0.25, 0.03))
  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(1.26, 0.7),
    new THREE.MeshBasicMaterial({ color: '#2a2833', map: programme() }),
  )
  screen.position.set(w / 2, 1, 0.292)
  screen.name = 'screen'
  g.add(screen)
  g.add(turned([[0, 0], [0.07, 0.01], [0.085, 0.1], [0.05, 0.19], [0, 0.2]], '#e9c46a', w - 0.16, 0.48, 0.26))
  g.add(box(0.14, 0.2, 0.14, '#9db8d9', 0.16, 0.48, 0.26, 0.03))
  return g
}

function drawers(w: number, rows: number, columns: number, h: number, color: string, face: string) {
  const g = new THREE.Group()
  g.add(box(w - 0.06, h, 0.44, color, w / 2, 0.06, 0.24, 0.03))
  for (const x of [0.12, w - 0.12]) g.add(box(0.07, 0.07, 0.36, DARK, x, 0, 0.24, 0.02))
  const [fw, fh] = [(w - 0.14) / columns, (h - 0.08) / rows]
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < columns; col++) {
      const [x, y] = [0.07 + fw * (col + 0.5), 0.1 + fh * row]
      g.add(box(fw - 0.03, fh - 0.03, 0.03, face, x, y + 0.015, 0.465, 0.012))
      g.add(ball(0.022, '#e9c46a', rows > 1 ? x : x + (col % 2 ? -1 : 1) * (fw / 2 - 0.08), y + fh / 2, 0.5))
    }
  }
  return g
}

function dresser() {
  const g = drawers(1, 3, 1, 0.8, '#d7b28c', '#e6c7a4')
  g.add(turned([[0.06, 0], [0.085, 0.1], [0.07, 0.1], [0, 0.09]], '#9db8d9', 0.26, 0.86, 0.24))
  for (const [x, y, z] of [[0, 0.17, 0], [0.06, 0.14, 0.03], [-0.06, 0.15, 0.02]]) g.add(ball(0.075, '#8dba8f', 0.26 + x, 0.86 + y, 0.24 + z))
  g.add(box(0.26, 0.2, 0.04, DARK, 0.7, 0.86, 0.22, 0.015))
  g.add(box(0.2, 0.14, 0.01, '#f7e7ea', 0.7, 0.89, 0.246, 0.003, { line: false, cast: false }))
  return g
}

function wardrobe() {
  return drawers(1.5, 1, 2, 1.85, '#d7b28c', '#e6c7a4')
}

function nightstand() {
  const g = drawers(0.5, 2, 1, 0.42, '#d7b28c', '#e6c7a4')
  g.add(turned([[0.06, 0], [0.07, 0.02], [0.02, 0.04], [0.02, 0.14], [0, 0.14]], PALE, 0.25, 0.48, 0.24))
  const shade = turned([[0.12, 0], [0.07, 0.16], [0, 0.165]], '#fff0cf', 0.25, 0.6, 0.24, {
    material: toon('#fff0cf', { emissive: '#ffcf7d', emissiveIntensity: 0 }),
    line: false,
  })
  shade.name = 'shade'
  g.add(shade)
  return g
}

function kitchen(front: string) {
  const g = new THREE.Group()
  g.add(box(0.98, 0.72, 0.44, front, 0.5, 0.06, 0.24, 0.02))
  g.add(box(0.96, 0.06, 0.38, DARK, 0.5, 0, 0.22, 0.01))
  g.add(box(1, 0.06, 0.5, '#f1ece4', 0.5, 0.78, 0.25, 0.02))
  for (const x of [0.27, 0.73]) {
    g.add(box(0.42, 0.6, 0.03, '#f6fbf5', x, 0.12, 0.465, 0.012))
    g.add(box(0.16, 0.025, 0.03, '#8fa79a', x, 0.62, 0.49, 0.01))
  }
  return g
}

function counter() {
  const g = kitchen('#cfe0d3')
  g.add(box(0.42, 0.03, 0.28, WOOD, 0.36, 0.84, 0.26, 0.012))
  for (const [x, z] of [[0.28, 0.22], [0.36, 0.3], [0.44, 0.24]]) g.add(ball(0.04, '#e08a72', x, 0.9, z))
  g.add(turned([[0.06, 0], [0.065, 0.2], [0.05, 0.22], [0, 0.23]], PALE, 0.74, 0.84, 0.18))
  g.add(turned([[0.05, 0], [0.055, 0.15], [0.04, 0.17], [0, 0.18]], '#e9c46a', 0.88, 0.84, 0.2))
  return g
}

function sink() {
  const g = kitchen('#cfe0d3')
  g.add(box(0.56, 0.02, 0.34, '#b9c4c9', 0.5, 0.835, 0.27, 0.01))
  g.add(box(0.48, 0.02, 0.26, '#8f9ca3', 0.5, 0.84, 0.28, 0.008, { line: false }))
  g.add(box(0.035, 0.24, 0.035, '#c9d2d6', 0.5, 0.84, 0.07, 0.015))
  g.add(box(0.035, 0.035, 0.2, '#c9d2d6', 0.5, 1.06, 0.15, 0.015))
  g.add(turned([[0.04, 0], [0.045, 0.14], [0.02, 0.17], [0, 0.17]], '#8fc3b5', 0.88, 0.84, 0.14))
  return g
}

function stove() {
  const g = kitchen('#d9d4cc')
  g.add(box(0.8, 0.02, 0.38, '#4a4653', 0.5, 0.835, 0.25, 0.01))
  for (const x of [0.3, 0.7]) g.add(turned([[0.11, 0], [0.12, 0.012], [0.1, 0.014]], '#7a7482', x, 0.853, 0.25, { line: false }))
  g.add(turned([[0.1, 0], [0.13, 0.02], [0.135, 0.15], [0.125, 0.15], [0.12, 0.03], [0, 0.03]], '#e08a72', 0.3, 0.866, 0.25))
  g.add(turned([[0.13, 0], [0.05, 0.035], [0.02, 0.06], [0, 0.065]], '#e9a48f', 0.3, 1.016, 0.25))
  g.add(turned([[0.09, 0], [0.13, 0.05], [0.12, 0.05], [0, 0.015]], '#5a5563', 0.7, 0.866, 0.25))
  g.add(box(0.035, 0.025, 0.22, DARK, 0.7, 0.9, 0.44, 0.01))
  // The hood.
  g.add(box(0.8, 0.16, 0.4, '#e8e2d8', 0.5, 1.72, 0.2, 0.03))
  g.add(box(0.3, 0.9, 0.26, '#e8e2d8', 0.5, 1.86, 0.14, 0.03))
  return g
}

function fridge() {
  const g = new THREE.Group()
  g.add(box(0.84, 1.72, 0.46, '#bcd3de', 0.5, 0.04, 0.25, 0.06))
  g.add(box(0.8, 0.6, 0.04, '#cfe2ea', 0.5, 0.08, 0.48, 0.03))
  g.add(box(0.8, 0.98, 0.04, '#cfe2ea', 0.5, 0.74, 0.48, 0.03))
  for (const [y, h] of [[0.42, 0.18], [0.84, 0.3]]) g.add(box(0.035, h, 0.04, '#8fa5b0', 0.18, y, 0.52, 0.015))
  for (const [x, y, color] of [[0.6, 1.3, '#e9c46a'], [0.72, 1.16, '#e08a72'], [0.54, 1.1, '#9cbf7a']] as const) g.add(ball(0.03, color, x, y, 0.505))
  return g
}

function lantern() {
  const g = new THREE.Group()
  g.add(turned([[0.12, 0], [0.13, 0.03], [0.1, 0.05], [0, 0.05]], DARK, 0.25, 0, 0.25))
  const glass = turned([[0.09, 0], [0.11, 0.05], [0.11, 0.22], [0.09, 0.27], [0, 0.27]], '#fff0cf', 0.25, 0.05, 0.25, {
    material: toon('#fff0cf', { emissive: '#ffcf7d', emissiveIntensity: 0 }),
  })
  glass.name = 'shade'
  g.add(glass)
  g.add(turned([[0.13, 0], [0.04, 0.09], [0.015, 0.13], [0, 0.13]], DARK, 0.25, 0.32, 0.25))
  return g
}

function planter() {
  const g = new THREE.Group()
  g.add(box(1.4, 0.34, 0.38, '#c9785c', 0.75, 0, 0.25, 0.04))
  g.add(box(1.46, 0.07, 0.44, '#d98f72', 0.75, 0.31, 0.25, 0.03))
  g.add(box(1.32, 0.03, 0.3, '#7a5a48', 0.75, 0.36, 0.25, 0.01, { line: false }))
  const blossoms = ['#f0a7a0', '#f7d98f', '#c3b4e0', '#fffaf2']
  for (let n = 0; n < 7; n++) {
    const [x, z] = [0.2 + n * 0.185, 0.2 + (n % 2) * 0.1]
    g.add(ball(0.12 + (n % 3) * 0.015, n % 2 ? '#7fae84' : '#8fbf8a', x, 0.44 + (n % 3) * 0.03, z))
    g.add(ball(0.045, blossoms[n % 4], x + 0.04, 0.58 + (n % 3) * 0.03, z + 0.06))
    if (n % 2) g.add(ball(0.035, blossoms[(n + 1) % 4], x - 0.06, 0.53, z + 0.09))
  }
  return g
}

function mat() {
  const g = new THREE.Group()
  const [w, d] = [FURNITURE.mat.w * CELL, FURNITURE.mat.d * CELL]
  const map = canvas(384, 288, (c2) => {
    c2.fillStyle = '#f3dfd0'
    c2.fillRect(0, 0, 384, 288)
    c2.strokeStyle = '#dba998'
    c2.lineWidth = 12
    c2.strokeRect(16, 16, 352, 256)
    c2.fillStyle = '#e9c3b2'
    for (let n = 0; n < 5; n++) {
      c2.beginPath()
      c2.arc(192 + Math.cos(n * 1.257) * 58, 144 + Math.sin(n * 1.257) * 58, 30, 0, 7)
      c2.fill()
    }
    c2.fillStyle = '#f7e6c4'
    c2.beginPath()
    c2.arc(192, 144, 32, 0, 7)
    c2.fill()
  })
  g.add(box(w - 0.1, 0.026, d - 0.1, '#dba998', w / 2, 0, d / 2, 0.012, { cast: false, line: false }))
  const top = new THREE.Mesh(new THREE.PlaneGeometry(w - 0.14, d - 0.14), toon('#ffffff', { map }))
  top.rotation.x = -Math.PI / 2
  top.position.set(w / 2, 0.028, d / 2)
  top.receiveShadow = true
  g.add(top)
  return g
}

const MAKE: Record<Kind, () => THREE.Group> = {
  sofa, armchair, beanbag, bench, bed, table, dining, bistro, chess, desk, easel, tv, shelf, dresser, wardrobe,
  nightstand, counter, sink, stove, fridge, lamp, lantern, plant, planter, rug, mat,
}

/** All of one piece in as few meshes as can be: its plain parts as one (coloured by vertex) with one outline; what
 * has a picture on it, or a name (to be switched on and off), by itself. */
function merged(made: THREE.Group): THREE.Group {
  const out = new THREE.Group()
  const parts: Record<'both' | 'line' | 'cast' | 'none', THREE.BufferGeometry[]> = { both: [], line: [], cast: [], none: [] }
  made.updateMatrixWorld(true)
  const single: THREE.Mesh[] = []
  made.traverse((thing) => {
    if (!(thing instanceof THREE.Mesh) || thing.material === outline) return
    const material = thing.material as THREE.MeshToonMaterial
    if (thing.name || material.map || !(material instanceof THREE.MeshToonMaterial)) {
      single.push(thing)
      return
    }
    const geometry = (thing.geometry.index ? thing.geometry.toNonIndexed() : thing.geometry.clone()) as THREE.BufferGeometry
    geometry.applyMatrix4(thing.matrixWorld)
    for (const name of Object.keys(geometry.attributes)) if (name !== 'position' && name !== 'normal') geometry.deleteAttribute(name)
    const count = geometry.attributes.position.count
    const colours = new Float32Array(count * 3)
    for (let n = 0; n < count; n++) material.color.toArray(colours, n * 3)
    geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3))
    const line = thing.children.some((child) => child instanceof THREE.Mesh && child.material === outline)
    parts[line ? (thing.castShadow ? 'both' : 'line') : thing.castShadow ? 'cast' : 'none'].push(geometry)
  })
  for (const [how, geometries] of Object.entries(parts)) {
    if (!geometries.length) continue
    const geometry = mergeGeometries(geometries)
    const mesh = new THREE.Mesh(geometry, toon('#ffffff', { vertexColors: true }))
    mesh.castShadow = how === 'both' || how === 'cast'
    mesh.receiveShadow = true
    if (how === 'both' || how === 'line') mesh.add(new THREE.Mesh(geometry, outline))
    out.add(mesh)
  }
  for (const mesh of single) {
    mesh.matrixWorld.decompose(mesh.position, mesh.quaternion, mesh.scale)
    out.add(mesh)
  }
  return out
}

export function furniture(kind: Kind): THREE.Group {
  const g = merged(MAKE[kind]())
  g.name = kind
  return g
}
