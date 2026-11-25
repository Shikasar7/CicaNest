import * as THREE from 'three'

import { flooringOf, hangingOf, wallpaperOf, type FloorKind, type Flooring, type HangKind, type Pattern, type WallKind } from './dorm-look.ts'
import { ball, box, canvas, toon, turned } from './dorm-shapes.ts'

/** Wallpaper, floors and what hangs on the walls of the 3D 小窝 (lib/dorm-look.ts), drawn in code. */

type Pen = CanvasRenderingContext2D

const SIZE = 256
/** How large a piece of wallpaper or floor is before it repeats, in metres. */
export const PAPER = 1.4
export const TILE = 1.2

function dot(g: Pen, x: number, y: number, r: number) {
  g.beginPath()
  g.arc(x, y, r, 0, 7)
  g.fill()
}

/** At x, y and wherever it goes over the edge, so that the pieces join. */
function around(x: number, y: number, r: number, draw: (x: number, y: number) => void) {
  for (const dx of [-SIZE, 0, SIZE]) {
    for (const dy of [-SIZE, 0, SIZE]) {
      const [px, py] = [x + dx, y + dy]
      if (px > -r && px < SIZE + r && py > -r && py < SIZE + r) draw(px, py)
    }
  }
}

const SCATTER = [[40, 50], [168, 34], [104, 120], [226, 112], [30, 178], [150, 198], [232, 222], [88, 236]]

function paper(g: Pen, pattern: Pattern) {
  g.fillStyle = pattern.base
  g.fillRect(0, 0, SIZE, SIZE)
  if (pattern.kind === 'plain') return
  g.fillStyle = g.strokeStyle = pattern.on
  switch (pattern.kind) {
    case 'stripes':
      for (const x of [0, 128]) g.fillRect(x + 38, 0, 52, SIZE)
      break
    case 'dots':
      for (let row = 0; row < 4; row++) for (let n = 0; n < 4; n++) around(n * 64 + (row % 2) * 32 + 16, row * 64 + 32, 9, (x, y) => dot(g, x, y, 8))
      break
    case 'checks':
      g.globalAlpha = 0.5
      for (let n = 0; n < 4; n++) {
        g.fillRect(n * 64, 0, 32, SIZE)
        g.fillRect(0, n * 64, SIZE, 32)
      }
      g.globalAlpha = 1
      break
    case 'stars':
      for (const [n, [x, y]] of SCATTER.entries()) {
        around(x, y, 14, (px, py) => {
          const r = n % 3 ? 5 : 11
          g.beginPath()
          for (let k = 0; k < 10; k++) {
            const [a, far] = [(k * Math.PI) / 5 - Math.PI / 2, k % 2 ? r * 0.45 : r]
            g.lineTo(px + Math.cos(a) * far, py + Math.sin(a) * far)
          }
          g.fill()
        })
      }
      break
    case 'leaves':
      for (const [n, [x, y]] of SCATTER.entries()) {
        around(x, y, 26, (px, py) => {
          g.save()
          g.translate(px, py)
          g.rotate(n * 1.3)
          for (const side of [-1, 1]) {
            g.beginPath()
            g.ellipse(side * 11, 0, 13, 5.5, side * 0.5, 0, 7)
            g.fill()
          }
          g.restore()
        })
      }
      break
    case 'flowers':
      for (const [n, [x, y]] of SCATTER.entries()) {
        around(x, y, 20, (px, py) => {
          g.fillStyle = pattern.more ?? pattern.on
          g.beginPath()
          g.ellipse(px + 12, py + 9, 9, 4, 0.7 + n, 0, 7)
          g.fill()
          g.fillStyle = pattern.on
          for (let k = 0; k < 5; k++) dot(g, px + Math.cos(k * 1.257 + n) * 7, py + Math.sin(k * 1.257 + n) * 7, 5.5)
          g.fillStyle = '#f7dc9a'
          dot(g, px, py, 4)
        })
      }
      break
    case 'bricks':
      for (let row = 0; row < 8; row++) {
        g.fillRect(0, row * 32, SIZE, 3)
        for (let n = 0; n < 4; n++) g.fillRect(n * 64 + (row % 2) * 32, row * 32, 3, 32)
      }
      break
    case 'waves':
      g.lineWidth = 4
      for (let row = 0; row < 8; row++) {
        g.beginPath()
        for (let x = -8; x <= SIZE + 8; x += 4) g.lineTo(x, row * 32 + 16 + Math.sin(((x + (row % 2) * 32) / SIZE) * Math.PI * 8) * 6)
        g.stroke()
      }
      break
  }
}

function floor(g: Pen, { pattern, base, on }: Flooring) {
  g.fillStyle = base
  g.fillRect(0, 0, SIZE, SIZE)
  let n = 5
  const next = () => (n = (n * 61 + 29) % 101)
  const shade = (much: number) => {
    const by = ((next() / 101) * 2 - 1) * much
    g.fillStyle = by > 0 ? `rgba(255,255,255,${by})` : `rgba(60,30,10,${-by})`
  }
  g.strokeStyle = on
  switch (pattern) {
    case 'planks':
    case 'deck': {
      const rows = pattern === 'deck' ? 4 : 5
      const tall = SIZE / rows
      for (let row = 0; row < rows; row++) {
        const cut = pattern === 'deck' ? 0 : [40, 190, 110, 230, 70][row]
        for (const from of [cut - SIZE, cut]) {
          shade(0.09)
          g.fillRect(from, row * tall, SIZE, tall)
        }
        g.globalAlpha = 0.45
        g.lineWidth = pattern === 'deck' ? 5 : 2
        g.beginPath()
        g.moveTo(0, row * tall)
        g.lineTo(SIZE, row * tall)
        if (pattern === 'planks') {
          g.moveTo(cut, row * tall)
          g.lineTo(cut, (row + 1) * tall)
        }
        g.stroke()
        g.globalAlpha = 0.14
        g.lineWidth = 1.5
        for (const at of [0.35, 0.68]) {
          g.beginPath()
          g.moveTo(0, (row + at) * tall)
          g.quadraticCurveTo(SIZE / 2, (row + at) * tall + (next() % 7) - 3, SIZE, (row + at) * tall)
          g.stroke()
        }
        g.globalAlpha = 1
      }
      break
    }
    case 'herringbone':
      g.lineWidth = 2
      for (let col = 0; col < 4; col++) {
        for (let row = -2; row < 10; row++) {
          const [x, y] = [col * 64, row * 32]
          g.beginPath()
          if (col % 2) [[0, 64], [64, 0], [64, 32], [0, 96]].forEach(([dx, dy]) => g.lineTo(x + dx, y + dy))
          else [[0, 0], [64, 64], [64, 96], [0, 32]].forEach(([dx, dy]) => g.lineTo(x + dx, y + dy))
          g.closePath()
          g.fillStyle = (row + 12) % 4 === 0 || (row + 12) % 4 === 3 ? 'rgba(255,255,255,.1)' : 'rgba(90,50,20,.07)'
          g.fill()
          g.globalAlpha = 0.4
          g.stroke()
          g.globalAlpha = 1
        }
      }
      break
    case 'tiles':
      for (let x = 0; x < 2; x++) {
        for (let y = 0; y < 2; y++) {
          g.fillStyle = (x + y) % 2 ? on : base
          g.fillRect(x * 128, y * 128, 128, 128)
          g.strokeStyle = 'rgba(110,110,110,.28)'
          g.lineWidth = 3
          g.strokeRect(x * 128, y * 128, 128, 128)
        }
      }
      break
    case 'stone':
      g.lineWidth = 6
      for (const [x, y, w, h] of [[0, 0, 150, 96], [150, 0, 106, 130], [0, 96, 92, 160], [92, 130, 100, 126], [192, 130, 64, 126], [92, 96, 58, 34]]) {
        shade(0.1)
        g.fillRect(x, y, w, h)
        g.globalAlpha = 0.7
        g.strokeRect(x, y, w, h)
        g.globalAlpha = 1
      }
      break
    case 'carpet':
      g.fillStyle = on
      g.globalAlpha = 0.55
      for (let k = 0; k < 900; k++) {
        const [x, y] = [(k * 97.3) % SIZE, (k * 41.7 + Math.floor(k / 9) * 7) % SIZE]
        g.fillRect(x, y, 2.5, 2.5)
      }
      g.globalAlpha = 1
      break
    case 'tatami':
      g.lineWidth = 1
      g.globalAlpha = 0.22
      for (let y = 0; y < SIZE; y += 5) {
        g.beginPath()
        g.moveTo(0, y)
        g.lineTo(SIZE, y)
        g.stroke()
      }
      g.globalAlpha = 1
      g.fillStyle = on
      for (const [x, y, w, h] of [[0, 0, SIZE, 9], [0, 128, SIZE, 9], [122, 0, 9, 128], [0, 128, 9, 128]]) g.fillRect(x, y, w, h)
      break
  }
}

function repeated(draw: (g: Pen) => void): THREE.Texture {
  const texture = canvas(SIZE, SIZE, draw)
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping
  return texture
}

export const paperTexture = (kind: WallKind) => repeated((g) => paper(g, wallpaperOf(kind).pattern))
export const floorTexture = (kind: FloorKind) => repeated((g) => floor(g, flooringOf(kind)))

function swatch(draw: (g: Pen) => void): string {
  const el = document.createElement('canvas')
  el.width = el.height = SIZE
  draw(el.getContext('2d')!)
  return el.toDataURL('image/png')
}

/** Small pictures of them, to choose from. */
export function paperSwatch(kind: WallKind): string {
  const { pattern, boards } = wallpaperOf(kind)
  return swatch((g) => {
    paper(g, pattern)
    if (!boards) return
    g.fillStyle = boards
    g.fillRect(0, 170, SIZE, 86)
    g.fillStyle = 'rgba(80,50,30,.18)'
    for (let x = 0; x < SIZE; x += 43) g.fillRect(x, 178, 4, 78)
    g.fillRect(0, 166, SIZE, 6)
  })
}

export const floorSwatch = (kind: FloorKind) => swatch((g) => floor(g, flooringOf(kind)))

// -- what hangs there: its lower left corner at 0, 0, facing +z -----------------------------------------------

const Q = 0.25

function framed(kind: HangKind, draw: (g: Pen, w: number, h: number) => void, wood = '#a97b58') {
  const g = new THREE.Group()
  const [w, h] = [hangingOf(kind).w * Q, hangingOf(kind).h * Q]
  g.add(box(w - 0.06, h - 0.06, 0.05, wood, w / 2, 0.03, 0.03, 0.02))
  const [pw, ph] = [256, Math.round((256 * (h - 0.16)) / (w - 0.16))]
  const art = new THREE.Mesh(
    new THREE.PlaneGeometry(w - 0.16, h - 0.16),
    toon('#ffffff', { map: canvas(pw, ph, (pen) => draw(pen, pw, ph)) }),
  )
  art.position.set(w / 2, h / 2, 0.058)
  g.add(art)
  return g
}

const fill = (g: Pen, color: string, x: number, y: number, w: number, h: number) => {
  g.fillStyle = color
  g.fillRect(x, y, w, h)
}

const oval = (g: Pen, color: string, x: number, y: number, rx: number, ry: number, turn = 0) => {
  g.fillStyle = color
  g.beginPath()
  g.ellipse(x, y, rx, ry, turn, 0, 7)
  g.fill()
}

const shape = (g: Pen, color: string, ...points: [number, number][]) => {
  g.fillStyle = color
  g.beginPath()
  for (const [x, y] of points) g.lineTo(x, y)
  g.fill()
}

const PAINT: Partial<Record<HangKind, (g: Pen, w: number, h: number) => void>> = {
  hills: (g, w, h) => {
    fill(g, '#cfe3ee', 0, 0, w, h)
    oval(g, '#fff3d6', w * 0.74, h * 0.28, 24, 24)
    oval(g, '#9cbfa7', w * 0.27, h, w * 0.47, h * 0.4)
    oval(g, '#7fa98f', w * 0.78, h * 1.06, w * 0.43, h * 0.34)
  },
  sea: (g, w, h) => {
    fill(g, '#f9e3cf', 0, 0, w, h)
    oval(g, '#fff6dc', w * 0.3, h * 0.36, 22, 22)
    fill(g, '#8fc0d6', 0, h * 0.5, w, h)
    fill(g, '#78aec8', 0, h * 0.64, w, h)
    oval(g, '#f2dcb8', w * 0.8, h * 1.05, w * 0.55, h * 0.24)
    shape(g, '#fffaf2', [w * 0.6, h * 0.22], [w * 0.6, h * 0.55], [w * 0.76, h * 0.55])
    shape(g, '#c9785c', [w * 0.52, h * 0.57], [w * 0.8, h * 0.57], [w * 0.74, h * 0.66], [w * 0.57, h * 0.66])
  },
  flower: (g, w, h) => {
    fill(g, '#f7e7ea', 0, 0, w, h)
    fill(g, '#7fae84', w / 2 - 5, h * 0.5, 10, h * 0.42)
    oval(g, '#8fbf8a', w * 0.62, h * 0.72, 32, 12, -0.6)
    for (let n = 0; n < 6; n++) oval(g, '#d98f8a', w / 2 + Math.cos(n * 1.047) * 44, h * 0.38 + Math.sin(n * 1.047) * 44, 33, 33)
    oval(g, '#f2cf7e', w / 2, h * 0.38, 27, 27)
  },
  cat: (g, w, h) => {
    fill(g, '#e4edf4', 0, 0, w, h)
    oval(g, '#6f6880', w / 2, h * 0.98, w * 0.3, h * 0.38)
    oval(g, '#6f6880', w / 2, h * 0.5, w * 0.25, h * 0.22)
    shape(g, '#6f6880', [w * 0.27, h * 0.46], [w * 0.3, h * 0.2], [w * 0.45, h * 0.34])
    shape(g, '#6f6880', [w * 0.73, h * 0.46], [w * 0.7, h * 0.2], [w * 0.55, h * 0.34])
    for (const x of [0.41, 0.59]) {
      oval(g, '#f6e6a8', w * x, h * 0.49, 13, 15)
      oval(g, '#3a3546', w * x, h * 0.49, 5, 13)
    }
    oval(g, '#e9a6a0', w / 2, h * 0.57, 7, 5)
  },
  moon: (g, w, h) => {
    fill(g, '#3a4275', 0, 0, w, h)
    oval(g, '#f6e7b4', w * 0.5, h * 0.34, 46, 46)
    oval(g, '#3a4275', w * 0.62, h * 0.3, 40, 40)
    for (const [x, y, r] of [[0.2, 0.2, 4], [0.8, 0.5, 5], [0.28, 0.58, 4], [0.66, 0.68, 3]]) oval(g, '#fff6d6', w * x, h * y, r, r)
    oval(g, '#2a3160', w * 0.3, h * 1.02, w * 0.5, h * 0.2)
  },
  fruit: (g, w, h) => {
    fill(g, '#fbf0dc', 0, 0, w, h)
    fill(g, '#e4c9a6', 0, h * 0.72, w, h)
    oval(g, '#e08a72', w * 0.36, h * 0.56, 36, 35)
    oval(g, '#e9c46a', w * 0.6, h * 0.6, 34, 31)
    oval(g, '#9cbf7a', w * 0.76, h * 0.66, 23, 21)
    oval(g, '#7fae84', w * 0.4, h * 0.28, 17, 7, -0.5)
  },
  town: (g, w, h) => {
    fill(g, '#f6dccb', 0, 0, w, h)
    oval(g, '#fff0cf', w * 0.82, h * 0.26, 18, 18)
    const houses: [number, number, string, string][] = [[0.04, 0.42, '#e9b7a4', '#b5695a'], [0.22, 0.56, '#f3e2c4', '#8f6a4c'], [0.4, 0.36, '#bcd3de', '#6f86a3'], [0.6, 0.52, '#d9e2c6', '#7f9a6e'], [0.78, 0.44, '#e3cfe6', '#8f7aa3']]
    for (const [x, y, wall, roof] of houses) {
      fill(g, wall, w * x, h * y, w * 0.17, h)
      shape(g, roof, [w * (x - 0.02), h * y], [w * (x + 0.085), h * (y - 0.2)], [w * (x + 0.19), h * y])
      for (const dy of [0.12, 0.32]) fill(g, '#fff6dc', w * (x + 0.055), h * (y + dy), w * 0.05, h * 0.1)
    }
  },
  calendar: (g, w, h) => {
    fill(g, '#fffaf2', 0, 0, w, h)
    fill(g, '#d98f8a', 0, 0, w, h * 0.3)
    oval(g, '#fff3d6', w / 2, h * 0.15, 22, 22)
    for (let row = 0; row < 5; row++) for (let n = 0; n < 6; n++) fill(g, row === 2 && n === 3 ? '#d98f8a' : '#d8cfc4', 22 + n * 37, h * 0.38 + row * h * 0.115, 24, h * 0.07)
  },
}

function clock() {
  const g = new THREE.Group()
  const face = (r: number, deep: number, color: string, z: number, line = true) => {
    const mesh = turned([[0, 0], [r, 0.004], [r, deep], [0, deep]], color, 0.25, 0.25, z, { line })
    mesh.rotation.x = Math.PI / 2
    g.add(mesh)
  }
  face(0.22, 0.05, '#a97b58', 0.01)
  face(0.185, 0.012, '#fffaf2', 0.06, false)
  const hand = (long: number, turn: number) => {
    const mesh = box(0.018, long, 0.01, '#4a4456', 0, 0, 0, 0.004, { line: false, cast: false })
    mesh.position.set(0.25 + Math.sin(turn) * long * 0.5, 0.25 + Math.cos(turn) * long * 0.5, 0.082)
    mesh.rotation.z = -turn
    g.add(mesh)
  }
  hand(0.1, -0.9)
  hand(0.15, 0.6)
  for (let n = 0; n < 12; n++) g.add(box(0.014, 0.014, 0.008, '#8f8494', 0.25 + Math.sin(n * 0.5236) * 0.155, 0.243 + Math.cos(n * 0.5236) * 0.155, 0.076, 0.003, { line: false, cast: false }))
  return g
}

function mirror() {
  const g = new THREE.Group()
  g.add(box(0.42, 0.94, 0.05, '#c9956c', 0.25, 0.03, 0.03, 0.2))
  g.add(box(0.34, 0.86, 0.02, '#cfe3ec', 0.25, 0.07, 0.055, 0.16, { line: false, cast: false }))
  const gleam = box(0.03, 0.4, 0.004, '#f4fbfe', 0.2, 0.34, 0.068, 0.002, { line: false, cast: false })
  gleam.rotation.z = -0.5
  g.add(gleam)
  return g
}

function rack() {
  const g = new THREE.Group()
  g.add(box(1.46, 0.05, 0.24, '#c9956c', 0.75, 0.04, 0.13, 0.02))
  for (const x of [0.2, 1.3]) g.add(box(0.05, 0.1, 0.18, '#a97b58', x, -0.04, 0.1, 0.02))
  for (const [n, color] of ['#e9c46a', '#9db8d9', '#d98f8a', '#8fb3a5'].entries()) g.add(box(0.15, 0.2, 0.15, color, 0.16 + n * 0.22, 0.09, 0.13, 0.05))
  g.add(turned([[0.05, 0], [0.07, 0.09], [0.06, 0.09], [0, 0.08]], '#c9785c', 1.14, 0.09, 0.13))
  for (const [x, y] of [[0, 0.14], [0.05, 0.11], [-0.05, 0.12]]) g.add(ball(0.06, '#7fae84', 1.14 + x, 0.09 + y, 0.13))
  g.add(box(0.12, 0.16, 0.03, '#f4efe6', 1.33, 0.09, 0.1, 0.012))
  return g
}

function hangplant() {
  const g = new THREE.Group()
  for (const x of [-0.07, 0.07]) {
    const cord = box(0.012, 0.3, 0.012, '#a97b58', 0.25 + x * 0.5, 0.44, 0.15, 0.004, { line: false })
    cord.rotation.z = x * 3
    g.add(cord)
  }
  g.add(turned([[0, 0], [0.09, 0.02], [0.13, 0.13], [0.11, 0.13], [0, 0.11]], '#f0d6b8', 0.25, 0.34, 0.16))
  for (const [n, [x, y]] of [[0, 0.5], [0.1, 0.47], [-0.1, 0.48], [0.15, 0.36], [-0.15, 0.34], [0.17, 0.22], [-0.16, 0.18], [0.05, 0.3]].entries()) {
    g.add(ball(0.075 - (n > 4 ? 0.02 : 0), n % 2 ? '#8fbf8a' : '#7fae84', 0.25 + x, y, 0.18 + (n % 3) * 0.03))
  }
  return g
}

function flags() {
  const g = new THREE.Group()
  const colours = ['#e7a7a0', '#f0d59a', '#a8c8dc', '#b5d0b0', '#c3b4e0']
  for (let n = 0; n < 9; n++) {
    const [x, y] = [0.12 + n * 0.22, 0.44 - Math.sin(((n + 0.5) / 9) * Math.PI) * 0.16]
    const flag = new THREE.Mesh(
      new THREE.ShapeGeometry(new THREE.Shape([new THREE.Vector2(-0.09, 0), new THREE.Vector2(0.09, 0), new THREE.Vector2(0, -0.2)])),
      toon(colours[n % 5], { side: THREE.DoubleSide }),
    )
    flag.position.set(x, y, 0.04 + (n % 2) * 0.01)
    g.add(flag)
    const cord = box(0.22, 0.014, 0.014, '#a97b58', x, y - 0.004, 0.035, 0.005, { line: false, cast: false })
    cord.rotation.z = -Math.cos(((n + 0.5) / 9) * Math.PI) * 0.24
    g.add(cord)
  }
  return g
}

function lights() {
  const g = new THREE.Group()
  for (let n = 0; n < 7; n++) {
    const color = ['#ffe2a8', '#ffd0c2', '#d6e6ff'][n % 3]
    const bulb = ball(0.045, color, 0.15 + n * 0.37, 0.15 - Math.sin((n / 6) * Math.PI * 2) ** 2 * 0.06, 0.07)
    bulb.name = 'shade' // lit with the lamps
    const material = bulb.material as THREE.MeshToonMaterial
    material.emissive.set(color)
    material.emissiveIntensity = 0
    g.add(bulb)
    if (n < 6) g.add(box(0.37, 0.012, 0.012, '#8f7a64', 0.335 + n * 0.37, 0.18, 0.05, 0.004, { line: false, cast: false }))
  }
  return g
}

function wreath() {
  const g = new THREE.Group()
  for (let n = 0; n < 14; n++) {
    const a = (n / 14) * Math.PI * 2
    g.add(ball(0.06, n % 2 ? '#7fae84' : '#8fbf8a', 0.25 + Math.cos(a) * 0.15, 0.25 + Math.sin(a) * 0.15, 0.06))
    if (n % 3 === 0) g.add(ball(0.035, ['#e7a7a0', '#f0d59a', '#fffaf2'][n % 5 % 3], 0.25 + Math.cos(a) * 0.15, 0.25 + Math.sin(a) * 0.15, 0.11))
  }
  return g
}

function photos() {
  const g = new THREE.Group()
  const tints = ['#bcd3de', '#f0d6b8', '#e7c2bd', '#cfe0c8', '#d8cfe8', '#f3e2b4']
  for (let n = 0; n < 6; n++) {
    const [x, y] = [0.22 + (n % 3) * 0.4, n < 3 ? 0.42 : 0.06]
    const card = box(0.28, 0.28, 0.02, '#fffaf2', x, y, 0.03, 0.006)
    const art = box(0.22, 0.17, 0.006, tints[n], x, y + 0.08, 0.043, 0.002, { line: false, cast: false })
    card.rotation.z = art.rotation.z = ((n * 37) % 7 - 3) * 0.02
    g.add(card, art, ball(0.018, '#e08a72', x, y + 0.265, 0.05))
  }
  return g
}

export function hanging(kind: HangKind): THREE.Group {
  const paint = PAINT[kind]
  const g = paint
    ? framed(kind, paint, kind === 'calendar' ? '#e8ddd0' : kind === 'moon' ? '#5a5568' : '#a97b58')
    : ({ clock, mirror, shelf: rack, hangplant, flags, lights, wreath, photos } as Record<string, () => THREE.Group>)[kind]()
  g.name = kind
  return g
}
