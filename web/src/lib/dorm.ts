/** 3D 小窝: a room and its furniture as data, and how one gets about in it. Nothing here draws;
 * lib/dorm-scene.ts does. Units are cells of half a metre: x to the right wall's far end, z to the left wall's. */

import type { Pose } from './home-stage.ts'

export const CELL = 0.5
/** How tall one of them stands, in metres. */
export const TALL = 1.5

export type Cell = { x: number; z: number }

/** Somewhere to be. `at`: where her feet (or her seat) are, in cells; `y`: how high, in metres; `from`: the cell
 * she walks to first. */
export type Place = { id: string; pose: Pose; at: Cell; y: number; from: Cell }

export type Kind = 'sofa' | 'table' | 'tv' | 'lamp' | 'shelf' | 'plant' | 'beanbag' | 'rug'

type Use = { pose: Pose; x: number; z: number; y: number; from: Cell }

type Furniture = {
  /** Cells taken along x and z. */
  w: number
  d: number
  /** Walked over, not around (a rug). */
  flat?: boolean
  uses?: Use[]
}

/** What furniture there is: what it takes up and what one can do on it (from which cell beside it). */
export const FURNITURE: Record<Kind, Furniture> = {
  sofa: {
    w: 5,
    d: 2,
    uses: [
      { pose: 'sit', x: 1.6, z: 1.45, y: 0.4, from: { x: 1, z: 2 } },
      { pose: 'sit', x: 3.4, z: 1.45, y: 0.4, from: { x: 3, z: 2 } },
    ],
  },
  table: { w: 3, d: 2 },
  tv: { w: 1, d: 4 },
  lamp: { w: 1, d: 1 },
  shelf: { w: 3, d: 1 },
  plant: { w: 1, d: 1 },
  beanbag: { w: 2, d: 2, uses: [{ pose: 'sit', x: 1, z: 1.25, y: 0.3, from: { x: 2, z: 1 } }] },
  rug: { w: 7, d: 6, flat: true },
}

export type Piece = { kind: Kind; x: number; z: number }

export type RoomPlan = { name: string; w: number; d: number; door: Cell; pieces: Piece[]; stands: Cell[] }

export const LIVING_ROOM: RoomPlan = {
  name: '客厅',
  w: 14,
  d: 12,
  door: { x: 12, z: 0 },
  pieces: [
    { kind: 'rug', x: 3, z: 2 },
    { kind: 'sofa', x: 4, z: 0 },
    { kind: 'table', x: 5, z: 4 },
    { kind: 'lamp', x: 2, z: 0 },
    { kind: 'shelf', x: 9, z: 0 },
    { kind: 'tv', x: 0, z: 5 },
    { kind: 'plant', x: 0, z: 1 },
    { kind: 'beanbag', x: 4, z: 8 },
  ],
  stands: [
    { x: 2, z: 4 },
    { x: 10, z: 3 },
    { x: 9, z: 6 },
    { x: 12, z: 5 },
    { x: 8, z: 9 },
    { x: 11, z: 9 },
    { x: 2, z: 10 },
  ],
}

export function blockedCells(room: RoomPlan): Set<string> {
  const out = new Set<string>()
  for (const piece of room.pieces) {
    const { w, d, flat } = FURNITURE[piece.kind]
    if (flat) continue
    for (let x = piece.x; x < piece.x + w; x++) for (let z = piece.z; z < piece.z + d; z++) out.add(key({ x, z }))
  }
  return out
}

const key = ({ x, z }: Cell) => `${x},${z}`

export const middle = ({ x, z }: Cell): Cell => ({ x: x + 0.5, z: z + 0.5 })

/** Every place to be in the room: what the furniture offers, and where to stand. */
export function placesOf(room: RoomPlan): Place[] {
  const places: Place[] = room.pieces.flatMap((piece, n) =>
    (FURNITURE[piece.kind].uses ?? []).map((use, m) => ({
      id: `${piece.kind}${n}-${m}`,
      pose: use.pose,
      at: { x: piece.x + use.x, z: piece.z + use.z },
      y: use.y,
      from: { x: piece.x + use.from.x, z: piece.z + use.from.z },
    })),
  )
  return [
    ...places,
    ...room.stands.map((cell) => ({ id: `stand${key(cell)}`, pose: 'stand' as const, at: middle(cell), y: 0, from: cell })),
  ]
}

/** The way from one cell to another around the furniture, both ends included; [] when there is none. Diagonally
 * only where both cells beside are free (nobody cuts a corner of a table). */
export function path(room: RoomPlan, from: Cell, to: Cell, blocked = blockedCells(room)): Cell[] {
  const free = (c: Cell) => c.x >= 0 && c.z >= 0 && c.x < room.w && c.z < room.d && !blocked.has(key(c))
  if (!free(from) || !free(to)) return []
  const guess = (c: Cell) => Math.hypot(c.x - to.x, c.z - to.z)
  const open = [{ cell: from, cost: 0, total: guess(from) }]
  const best = new Map<string, number>([[key(from), 0]])
  const came = new Map<string, Cell>()
  while (open.length) {
    open.sort((a, b) => a.total - b.total)
    const { cell, cost } = open.shift()!
    if (cell.x === to.x && cell.z === to.z) {
      const way = [cell]
      for (let back = came.get(key(cell)); back; back = came.get(key(back))) way.unshift(back)
      return way
    }
    if (cost > (best.get(key(cell)) ?? Infinity)) continue
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        if (!dx && !dz) continue
        const next = { x: cell.x + dx, z: cell.z + dz }
        if (!free(next)) continue
        if (dx && dz && !(free({ x: cell.x + dx, z: cell.z }) && free({ x: cell.x, z: cell.z + dz }))) continue
        const sum = cost + Math.hypot(dx, dz)
        if (sum >= (best.get(key(next)) ?? Infinity)) continue
        best.set(key(next), sum)
        came.set(key(next), cell)
        open.push({ cell: next, cost: sum, total: sum + guess(next) })
      }
    }
  }
  return []
}

/** The points she goes through from where she is (`from`: a place, or the door) to another place: off the seat,
 * around the furniture, onto the seat. */
export function route(room: RoomPlan, from: { at: Cell; from: Cell; y: number }, to: Place): { at: Cell; y: number }[] {
  const way = path(room, from.from, to.from).map((cell) => ({ at: middle(cell), y: 0 }))
  if (!way.length) return [{ at: to.at, y: to.y }]
  return [...way, ...(to.pose === 'stand' ? [] : [{ at: to.at, y: to.y }])]
}
