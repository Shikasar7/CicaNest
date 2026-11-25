import * as THREE from 'three'

import { CELL, type RoomPlan } from './dorm.ts'
import { flooringOf, wallpaperOf, type Look } from './dorm-look.ts'
import { floorTexture, PAPER, paperTexture, TILE } from './dorm-paint.ts'
import { ball, box, canvas, toon } from './dorm-shapes.ts'

/** The rooms of the 3D 小窝, without their furniture. */

export const WALL_H = 3

const WINDOW = { z: 1.75, w: 1.5, y: 0.95, h: 1.35 } // on the left wall, its middle

export type Shell = {
  group: THREE.Group
  /** What is seen through the window, and where its light falls; none on a balcony. */
  sky: THREE.Mesh | null
  patch: THREE.Mesh | null
  /** Other paper, another floor. */
  dress: (look: Look) => void
}

const THICK = 0.16

/** Floor, walls, window, door. */
export function shell(room: RoomPlan): Shell {
  const g = new THREE.Group()
  const [w, d] = [room.w * CELL, room.d * CELL]
  const [top, edge] = [toon('#ffffff'), toon('#ffffff')]
  const floor = new THREE.Mesh(new THREE.BoxGeometry(w, 0.24, d), [edge, edge, top, edge, edge, edge])
  floor.position.set(w / 2, -0.12, d / 2)
  floor.receiveShadow = true
  g.add(floor)

  const [paper, paperLeft, side] = [toon('#ffffff'), toon('#f3ece6'), toon('#f7efe4')]
  const back = new THREE.Mesh(new THREE.BoxGeometry(w, WALL_H, THICK), [side, side, side, side, paper, side])
  back.position.set(w / 2, WALL_H / 2, -THICK / 2)
  back.receiveShadow = true
  g.add(back)
  const plain = { line: false, cast: false }
  const [cap, skirting] = ['#fffaf2', '#fff6ea']
  g.add(box(w, 0.14, 0.05, skirting, w / 2, 0, 0.025, 0.015, plain))
  g.add(box(w + THICK, 0.06, THICK + 0.04, cap, w / 2 - THICK / 2, WALL_H, -THICK / 2, 0.02, plain))

  let sky: THREE.Mesh | null = null
  let patch: THREE.Mesh | null = null
  if (room.open) railing(g, d)
  else {
    g.add(box(0.05, 0.14, d, skirting, 0.025, 0, d / 2, 0.015, plain))
    g.add(box(THICK + 0.04, 0.06, d + THICK, cap, -THICK / 2, WALL_H, d / 2 - THICK / 2, 0.02, plain))
    sky = leftWall(g, paperLeft, side, d)
    patch = light(g)
  }

  // The door, in the right wall: of glass, to a balcony.
  const door = (room.door.x + 0.5) * CELL
  g.add(box(0.98, 2.16, 0.06, '#fffaf2', door, 0, 0.02, 0.02))
  g.add(box(0.8, 2.04, 0.06, room.open ? '#8fb3c9' : '#c79a72', door, 0, 0.05, 0.03))
  for (const y of [0.22, 1.16]) g.add(box(0.5, 0.7, 0.03, room.open ? '#cfe6f2' : '#b98a66', door, y, 0.08, 0.02, { line: false }))
  g.add(ball(0.04, '#e9c46a', door - 0.3, 1.02, 0.12))

  const boards = new THREE.Group()
  g.add(boards)
  const dress = (look: Look) => {
    for (const material of [paper, paperLeft, top]) material.map?.dispose()
    paper.map = paperTexture(look.wall)
    paper.map.repeat.set(w / PAPER, WALL_H / PAPER)
    paperLeft.map = paperTexture(look.wall)
    paperLeft.map.repeat.set(1 / PAPER, 1 / PAPER) // its corners are in metres
    top.map = floorTexture(look.floor)
    top.map.repeat.set(w / TILE, d / TILE)
    edge.color.set(flooringOf(look.floor).edge)
    for (const material of [paper, paperLeft, top]) material.needsUpdate = true
    boards.traverse((part) => {
      if (!(part instanceof THREE.Mesh)) return
      part.geometry.dispose()
      ;(part.material as THREE.Material).dispose()
    })
    boards.clear()
    const color = wallpaperOf(look.wall).boards
    if (color) panel(boards, room, color)
  }
  return { group: g, sky, patch, dress }
}

/** Boards up to the hip, on both walls. */
function panel(g: THREE.Group, room: RoomPlan, color: string) {
  const [w, d] = [room.w * CELL, room.d * CELL]
  const dark = new THREE.Color(color).multiplyScalar(0.86).getStyle()
  const plain = { line: false, cast: false }
  const door = (room.door.x + 0.5) * CELL
  const high = 0.92
  for (const [from, to] of [[0, door - 0.49], [door + 0.49, w]]) {
    const long = to - from
    if (long < 0.05) continue
    g.add(box(long, high, 0.02, color, (from + to) / 2, 0, 0.011, 0.004, plain))
    g.add(box(long, 0.06, 0.05, dark, (from + to) / 2, high, 0.025, 0.02, plain))
  }
  for (let x = 0.35; x < w; x += 0.35) if (Math.abs(x - door) > 0.5) g.add(box(0.02, high, 0.006, dark, x, 0, 0.023, 0.002, plain))
  if (room.open) return
  g.add(box(0.02, high, d, color, 0.011, 0, d / 2, 0.004, plain))
  g.add(box(0.05, 0.06, d, dark, 0.025, high, d / 2, 0.02, plain))
  for (let z = 0.35; z < d; z += 0.35) g.add(box(0.006, high, 0.02, dark, 0.023, 0, z, 0.002, plain))
}

function railing(g: THREE.Group, d: number) {
  for (let z = 0.06; z <= d; z += 0.3) g.add(box(0.05, 0.9, 0.05, '#fffaf2', 0.03, 0, z, 0.02))
  g.add(box(0.09, 0.07, d, '#fffaf2', 0.03, 0.9, d / 2, 0.03))
  g.add(box(0.07, 0.06, d, '#fffaf2', 0.03, 0.12, d / 2, 0.02))
}

function leftWall(g: THREE.Group, paper: THREE.Material, side: THREE.Material, d: number): THREE.Mesh {
  const thick = THICK
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
  const wall = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: thick, bevelEnabled: false }), [paper, side])
  wall.rotation.y = -Math.PI / 2
  wall.position.set(0, 0, -thick)
  wall.receiveShadow = true
  g.add(wall)
  // What is outside, the frame, the sill, the curtains.
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(WINDOW.w, WINDOW.h), new THREE.MeshBasicMaterial())
  sky.rotation.y = Math.PI / 2
  sky.position.set(-thick - 0.02, WINDOW.y + WINDOW.h / 2, WINDOW.z)
  g.add(sky)
  const bar = (y: number, z: number, h: number, len: number, deep = 0.1) =>
    g.add(box(deep, h, len, '#fffaf2', -0.02, y, z, 0.02))
  bar(WINDOW.y - 0.04, WINDOW.z, 0.08, WINDOW.w + 0.16, 0.22)
  bar(WINDOW.y + WINDOW.h - 0.04, WINDOW.z, 0.08, WINDOW.w + 0.12)
  for (const z of [-WINDOW.w / 2, 0, WINDOW.w / 2]) bar(WINDOW.y, WINDOW.z + z, WINDOW.h, z ? 0.08 : 0.05)
  bar(WINDOW.y + WINDOW.h * 0.56, WINDOW.z, 0.045, WINDOW.w)
  g.add(box(0.04, 0.04, WINDOW.w + 0.9, '#a97b58', 0.1, WINDOW.y + WINDOW.h + 0.14, WINDOW.z, 0.02))
  for (const side of [-1, 1]) {
    for (let n = 0; n < 3; n++) {
      const z = WINDOW.z + side * (WINDOW.w / 2 + 0.02 + n * 0.15)
      g.add(box(0.1, WINDOW.h + 0.5, 0.17, n % 2 ? '#e9c9c4' : '#f0d6d0', 0.1, WINDOW.y - 0.34, z, 0.05))
    }
  }
  return sky
}

function light(g: THREE.Group): THREE.Mesh {
  const z = WINDOW.z
  const patch = new THREE.Mesh(
    new THREE.BufferGeometry().setAttribute(
      'position',
      new THREE.Float32BufferAttribute([0.5, 0, z - 0.5, 0.5, 0, z + 0.75, 2.3, 0, z + 1.15, 2.3, 0, z - 0.1], 3),
    ),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }),
  )
  patch.geometry.setIndex([0, 1, 2, 0, 2, 3])
  patch.position.y = 0.04
  g.add(patch)
  return patch
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
