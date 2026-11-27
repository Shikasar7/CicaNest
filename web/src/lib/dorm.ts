/** 3D 小窝: rooms and furniture as data, where furniture may stand, what one does on it and
 * how one gets there. Nothing here draws; lib/dorm-scene.ts does. Units are cells of half a metre: x along the
 * right wall, z along the left wall, 0 in the far corner. */

import { cleanLook, type FloorKind, type Hung, type Look, type WallKind } from './dorm-look.ts'
import type { Pose } from './home-stage.ts'

export const CELL = 0.5
/** How tall one of them stands, in metres. */
export const TALL = 1.5

export type Cell = { x: number; z: number }

// -- furniture -----------------------------------------------------------------------------------------------

/** What she may be doing there (activityOf). */
export type Activity =
  | 'sleep'
  | 'cook'
  | 'chess'
  | 'tv'
  | 'paint'
  | 'desk'
  | 'read'
  | 'tea'
  | 'eat'
  | 'water'
  | ''

/** Something one can do on a piece: where her feet or her seat are (cells of the piece, not turned), how high (in
 * metres), the cells she gets there from, the first that is free, and what it is for, its main purpose first. */
type Use = { pose: Pose; x: number; z: number; y: number; from: Cell[]; for: Activity[] }

type Furniture = {
  label: string
  group: '坐和躺' | '桌子' | '柜子' | '厨房' | '灯和花草' | '地毯' | '猫咪' | '摆件'
  /** Cells taken along x and z while it faces the front (turn 0). */
  w: number
  d: number
  /** Walked over, and other furniture stands on it. */
  flat?: boolean
  uses?: Use[]
}

const c = (x: number, z: number): Cell => ({ x, z })
const sit = (x: number, z: number, y: number, from: Cell[], ...what: Activity[]): Use => ({ pose: 'sit', x, z, y, from, for: what })
const stand = (x: number, z: number, from: Cell[], ...what: Activity[]): Use => ({ pose: 'stand', x, z, y: 0, from, for: what })

export const FURNITURE = {
  sofa: {
    label: '沙发',
    group: '坐和躺',
    w: 5,
    d: 2,
    uses: [
      sit(1.6, 1.45, 0.4, [c(1, 2), c(2, 2)], 'tv', 'read', 'tea', 'eat'),
      sit(3.4, 1.45, 0.4, [c(3, 2), c(2, 2)], 'tv', 'read', 'tea', 'eat'),
    ],
  },
  armchair: {
    label: '单人沙发',
    group: '坐和躺',
    w: 2,
    d: 2,
    uses: [sit(1, 1.4, 0.38, [c(0, 2), c(1, 2)], 'read', 'tea', 'tv')],
  },
  beanbag: {
    label: '懒人沙发',
    group: '坐和躺',
    w: 2,
    d: 2,
    uses: [sit(1, 1.25, 0.3, [c(2, 1), c(1, 2), c(-1, 1), c(0, 2)], 'tv', 'read')],
  },
  bench: {
    label: '长椅',
    group: '坐和躺',
    w: 3,
    d: 1,
    uses: [
      sit(0.9, 0.75, 0.36, [c(0, 1), c(1, 1)], 'read', 'tea'),
      sit(2.1, 0.75, 0.36, [c(2, 1), c(1, 1)], 'read', 'tea'),
    ],
  },
  bed: {
    label: '床',
    group: '坐和躺',
    w: 3,
    d: 5,
    uses: [
      { pose: 'lie', x: 1.5, z: 1.5, y: 0.3, from: [c(3, 2), c(-1, 2), c(3, 3), c(-1, 3)], for: ['sleep'] },
      sit(2.2, 3.9, 0.46, [c(3, 3), c(3, 4), c(2, 5), c(1, 5)], 'read'),
    ],
  },
  table: { label: '茶几', group: '桌子', w: 3, d: 2 },
  dining: {
    label: '餐桌',
    group: '桌子',
    w: 4,
    d: 4,
    uses: [
      sit(1.1, 0.5, 0.36, [c(0, 0), c(1, -1)], 'eat', 'tea', 'read'),
      sit(2.9, 0.5, 0.36, [c(3, 0), c(2, -1)], 'eat', 'tea', 'read'),
      sit(1.1, 3.5, 0.36, [c(1, 4), c(0, 4), c(-1, 3)], 'eat', 'tea', 'read'),
      sit(2.9, 3.5, 0.36, [c(2, 4), c(3, 4), c(4, 3)], 'eat', 'tea', 'read'),
    ],
  },
  bistro: {
    label: '小圆桌',
    group: '桌子',
    w: 4,
    d: 2,
    uses: [
      sit(0.6, 1, 0.36, [c(0, 2), c(-1, 1), c(-1, 0)], 'tea', 'eat', 'read'),
      sit(3.4, 1, 0.36, [c(3, 2), c(4, 1), c(4, 0)], 'tea', 'eat', 'read'),
    ],
  },
  chess: {
    label: '棋桌',
    group: '桌子',
    w: 4,
    d: 2,
    uses: [
      sit(0.6, 1, 0.36, [c(0, 2), c(-1, 1), c(-1, 0)], 'chess'),
      sit(3.4, 1, 0.36, [c(3, 2), c(4, 1), c(4, 0)], 'chess'),
    ],
  },
  desk: {
    label: '书桌',
    group: '桌子',
    w: 3,
    d: 2,
    uses: [sit(1.5, 0.6, 0.4, [c(-1, 0), c(3, 0)], 'desk', 'read')],
  },
  easel: { label: '画架', group: '桌子', w: 2, d: 1, uses: [stand(0.4, 1.5, [c(0, 1), c(1, 1)], 'paint')] },
  tv: { label: '电视柜', group: '柜子', w: 4, d: 1 },
  shelf: { label: '书架', group: '柜子', w: 3, d: 1, uses: [stand(1.5, 1.5, [c(1, 1)], 'read')] },
  dresser: { label: '斗柜', group: '柜子', w: 2, d: 1 },
  wardrobe: { label: '衣柜', group: '柜子', w: 3, d: 1 },
  nightstand: { label: '床头柜', group: '柜子', w: 1, d: 1 },
  counter: { label: '料理台', group: '厨房', w: 2, d: 1, uses: [stand(1, 1.5, [c(0, 1), c(1, 1)], 'eat', 'cook')] },
  sink: { label: '水槽', group: '厨房', w: 2, d: 1, uses: [stand(1, 1.5, [c(0, 1), c(1, 1)], 'tea', 'cook')] },
  stove: { label: '灶台', group: '厨房', w: 2, d: 1, uses: [stand(1, 1.5, [c(0, 1), c(1, 1)], 'cook')] },
  fridge: { label: '冰箱', group: '厨房', w: 2, d: 1 },
  lamp: { label: '落地灯', group: '灯和花草', w: 1, d: 1 },
  lantern: { label: '小灯笼', group: '灯和花草', w: 1, d: 1 },
  plant: { label: '绿植', group: '灯和花草', w: 1, d: 1, uses: [stand(0.5, 1.6, [c(0, 1), c(1, 0), c(-1, 0)], 'water')] },
  planter: { label: '花槽', group: '灯和花草', w: 3, d: 1, uses: [stand(1.5, 1.6, [c(1, 1), c(2, 1), c(0, 1)], 'water')] },
  rug: { label: '大地毯', group: '地毯', w: 7, d: 6, flat: true },
  mat: { label: '小地毯', group: '地毯', w: 4, d: 3, flat: true },
  catbed: {
    label: '大猫窝',
    group: '猫咪',
    w: 4,
    d: 4,
    uses: [
      { pose: 'lie', x: 2, z: 2.1, y: 0.22, from: [c(4, 2), c(-1, 2), c(2, 4), c(1, 4)], for: ['sleep'] },
      sit(2.6, 2.6, 0.22, [c(3, 4), c(4, 3), c(2, 4)], 'read', 'tv'),
    ],
  },
  cattree: {
    label: '猫爬架',
    group: '猫咪',
    w: 3,
    d: 2,
    uses: [sit(2.3, 1, 1.03, [c(2, 2), c(3, 1), c(1, 2)], 'read', 'tea')],
  },
  fishpillow: {
    label: '小鱼干抱枕',
    group: '猫咪',
    w: 2,
    d: 1,
    uses: [sit(1, 0.6, 0.2, [c(1, 1), c(0, 1), c(2, 0), c(-1, 0)], 'read', 'tv')],
  },
  canopy: {
    label: '帐幔床',
    group: '坐和躺',
    w: 3,
    d: 5,
    uses: [
      { pose: 'lie', x: 1.5, z: 1.5, y: 0.34, from: [c(3, 2), c(-1, 2), c(3, 3), c(-1, 3)], for: ['sleep'] },
      sit(2.2, 3.9, 0.5, [c(3, 3), c(3, 4), c(2, 5), c(1, 5)], 'read'),
    ],
  },
  chaise: {
    label: '贵妃榻',
    group: '坐和躺',
    w: 4,
    d: 2,
    uses: [
      sit(1.3, 1.2, 0.42, [c(1, 2), c(0, 2), c(-1, 1)], 'tea', 'read', 'tv'),
      sit(2.8, 1.2, 0.42, [c(3, 2), c(2, 2), c(4, 1)], 'tea', 'read', 'tv'),
    ],
  },
  teacart: { label: '茶车', group: '桌子', w: 2, d: 1, uses: [stand(1, 1.5, [c(0, 1), c(1, 1)], 'tea')] },
  vanity: { label: '梳妆台', group: '柜子', w: 2, d: 1 },
  hoard: { label: '宝藏堆', group: '摆件', w: 3, d: 2 },
  orb: { label: '龙珠台', group: '摆件', w: 1, d: 1 },
  blossom: {
    label: '海棠花瓶',
    group: '摆件',
    w: 1,
    d: 1,
    uses: [stand(0.5, 1.6, [c(0, 1), c(1, 0), c(-1, 0)], 'water')],
  },
} satisfies Record<string, Furniture>

export type Kind = keyof typeof FURNITURE

export const furnitureOf = (kind: Kind): Furniture => FURNITURE[kind]

/** How often it is turned by a quarter, against the clock as seen from above. */
export type Turn = 0 | 1 | 2 | 3

/** A piece in a room; x, z: its corner nearest the far corner of the room, as it stands. */
export type Piece = { kind: Kind; x: number; z: number; turn: Turn }

const isKind = (kind: unknown): kind is Kind => typeof kind === 'string' && Object.hasOwn(FURNITURE, kind)

/** The cells it takes along x and z as it stands. */
export function sizeOf(piece: Pick<Piece, 'kind' | 'turn'>): { w: number; d: number } {
  const { w, d } = furnitureOf(piece.kind)
  return piece.turn % 2 ? { w: d, d: w } : { w, d }
}

/** A point of the piece (not turned) as a point of the room. */
export function pointOf(piece: Piece, at: Cell): Cell {
  const { w, d } = furnitureOf(piece.kind)
  const turned = [at, c(at.z, w - at.x), c(w - at.x, d - at.z), c(d - at.z, at.x)][piece.turn]
  return c(piece.x + turned.x, piece.z + turned.z)
}

/** Where the model's own corner goes, for drawing it turned about that corner. */
export function cornerOf(piece: Piece): Cell {
  return pointOf(piece, c(0, 0))
}

function cellOf(piece: Piece, cell: Cell): Cell {
  const at = pointOf(piece, middle(cell))
  return c(Math.floor(at.x), Math.floor(at.z))
}

export function cellsOf(piece: Piece): Cell[] {
  const { w, d } = sizeOf(piece)
  const out: Cell[] = []
  for (let x = piece.x; x < piece.x + w; x++) for (let z = piece.z; z < piece.z + d; z++) out.push(c(x, z))
  return out
}

// -- rooms ---------------------------------------------------------------------------------------------------

export type RoomPlan = {
  name: string
  w: number
  d: number
  door: Cell
  /** A balcony: a railing for the left wall, the sky around it. */
  open?: boolean
  look: Look
  pieces: Piece[]
}

/** What the user changes of a room. */
export type Furnished = Pick<RoomPlan, 'look' | 'pieces'>

const p = (kind: Kind, x: number, z: number, turn: Turn = 0): Piece => ({ kind, x, z, turn })
const look = (wall: WallKind, floor: FloorKind, ...hung: Hung[]): Look => ({ wall, floor, hung })
const right = (kind: Hung['kind'], at: number, y: number): Hung => ({ kind, wall: 'right', at, y })

const PLANS: Record<string, RoomPlan> = {
  客厅: {
    name: '客厅',
    w: 14,
    d: 12,
    door: c(12, 0),
    look: look('cream', 'oak', right('hills', 9, 5), right('flower', 14, 6)),
    pieces: [
      p('rug', 3, 2),
      p('sofa', 4, 0),
      p('table', 5, 4),
      p('lamp', 2, 0),
      p('shelf', 9, 0),
      p('tv', 0, 5, 1),
      p('plant', 0, 1),
      p('beanbag', 4, 8),
      p('chess', 9, 8),
    ],
  },
  厨房: {
    name: '厨房',
    w: 12,
    d: 10,
    door: c(10, 0),
    look: look('mint', 'mint', right('shelf', 5, 6), right('calendar', 17, 5)),
    pieces: [
      p('fridge', 0, 0),
      p('sink', 2, 0),
      p('counter', 4, 0),
      p('stove', 6, 0),
      p('dresser', 0, 4, 1),
      p('dining', 4, 4),
      p('plant', 0, 8),
      p('lamp', 0, 6),
    ],
  },
  书房: {
    name: '书房',
    w: 12,
    d: 10,
    door: c(10, 0),
    look: look('lilac', 'oak', right('sea', 10, 7), right('clock', 15, 8)),
    pieces: [
      p('mat', 4, 4),
      p('shelf', 0, 0),
      p('lamp', 3, 0),
      p('desk', 5, 0),
      p('shelf', 0, 6, 1),
      p('easel', 0, 3, 1),
      p('armchair', 4, 4),
      p('beanbag', 8, 5),
      p('plant', 0, 9),
    ],
  },
  阳台: {
    name: '阳台',
    w: 14,
    d: 8,
    door: c(11, 0),
    open: true,
    look: look('sand', 'deck', right('lights', 0, 10), right('lights', 10, 10), right('wreath', 17, 6)),
    pieces: [
      p('planter', 0, 1, 1),
      p('planter', 0, 4, 1),
      p('bench', 3, 0),
      p('lantern', 2, 0),
      p('plant', 6, 0),
      p('lantern', 8, 0),
      p('bistro', 5, 3),
      p('mat', 5, 3),
    ],
  },
}

const BEDROOM: Omit<RoomPlan, 'name' | 'look'> = {
  w: 11,
  d: 10,
  door: c(9, 0),
  pieces: [
    p('mat', 5, 4),
    p('bed', 1, 0),
    p('lamp', 0, 0),
    p('nightstand', 4, 0),
    p('dresser', 5, 0),
    p('wardrobe', 0, 6, 1),
    p('beanbag', 6, 4),
    p('plant', 0, 5),
  ],
}

const BEDROOM_PAPERS: WallKind[] = ['pink', 'sky', 'lilac', 'mint', 'lemon']

export const SHARED_ROOMS = Object.keys(PLANS)
export const BEDROOM_KEY = '卧室:'

function hash(text: string): number {
  let n = 0
  for (const ch of text) n = (n * 31 + (ch.codePointAt(0) ?? 0)) >>> 0
  return n
}

/** A room as it is built; its walls, floor and furniture as they were, or as the user wants them (`saved`: what
 * Furnished is, or from before there were walls to choose only its pieces). `key`: a shared room's name, or
 * 「卧室:」 and whose it is. */
export function planOf(key: string, saved?: unknown): RoomPlan {
  const plan: RoomPlan = key.startsWith(BEDROOM_KEY)
    ? {
        ...BEDROOM,
        name: '卧室',
        look: look(BEDROOM_PAPERS[hash(key) % BEDROOM_PAPERS.length], 'oak', right('hills', 3, 6), right('flower', 11, 6)),
      }
    : (PLANS[key] ?? PLANS['客厅'])
  const { pieces, look: wanted } = (
    Array.isArray(saved) ? { pieces: saved } : saved && typeof saved === 'object' ? saved : {}
  ) as Record<string, unknown>
  return {
    ...plan,
    pieces: Array.isArray(pieces) ? cleanPieces(plan, pieces) : plan.pieces,
    look: wanted ? cleanLook(plan, wanted, plan.look) : plan.look,
  }
}

export const MAX_PIECES = 60

/** A saved room as far as it still makes sense: known furniture, inside, on free floor. */
export function cleanPieces(room: Omit<RoomPlan, 'pieces'>, saved: unknown[]): Piece[] {
  const out: Piece[] = []
  for (const item of saved.slice(0, MAX_PIECES)) {
    const { kind, x, z, turn } = (item ?? {}) as Record<string, unknown>
    if (!isKind(kind) || ![x, z, turn].every(Number.isInteger) || ![0, 1, 2, 3].includes(turn as number)) continue
    const piece: Piece = { kind, x: x as number, z: z as number, turn: turn as Turn }
    if (canStand({ ...room, pieces: out }, piece)) out.push(piece)
  }
  return out
}

/** Whether it may stand there: inside, not in another, not before the door. `but`: a piece not to mind (itself). */
export function canStand(room: RoomPlan, piece: Piece, but?: Piece): boolean {
  const cells = cellsOf(piece)
  if (!cells.every((cell) => cell.x >= 0 && cell.z >= 0 && cell.x < room.w && cell.z < room.d)) return false
  const { flat = false } = furnitureOf(piece.kind)
  if (!flat && cells.some((cell) => cell.x === room.door.x && cell.z <= room.door.z + 1)) return false
  const taken = new Set(
    room.pieces
      .filter((other) => other !== but && Boolean(furnitureOf(other.kind).flat) === flat)
      .flatMap((other) => cellsOf(other).map(key)),
  )
  return !cells.some((cell) => taken.has(key(cell)))
}

/** A free spot for a new piece, the nearest to the middle of the room; null: it does not fit. */
export function roomFor(room: RoomPlan, kind: Kind): Piece | null {
  let best: Piece | null = null
  let least = Infinity
  for (const turn of [0, 1] as Turn[]) {
    const { w, d } = sizeOf({ kind, turn })
    for (let x = 0; x <= room.w - w; x++) {
      for (let z = 0; z <= room.d - d; z++) {
        const far = Math.hypot(x + w / 2 - room.w / 2, z + d / 2 - room.d / 2) + turn * 0.1
        if (far >= least) continue
        const piece = { kind, x, z, turn }
        if (!canStand(room, piece)) continue
        best = piece
        least = far
      }
    }
  }
  return best
}

// -- being there ---------------------------------------------------------------------------------------------

/** Somewhere to be. `at`: where her feet (or her seat) are, in cells; `y`: how high, in metres; `from`: the cell
 * she walks to first; `for`: what one does there; `piece`: which piece it is of (its place in the room's list). */
export type Place = { id: string; pose: Pose; at: Cell; y: number; from: Cell; for: Activity[]; piece: number }

const STANDS = 8

const key = ({ x, z }: Cell) => `${x},${z}`

export const middle = ({ x, z }: Cell): Cell => c(x + 0.5, z + 0.5)

export function blockedCells(room: RoomPlan): Set<string> {
  return new Set(
    room.pieces.filter((piece) => !furnitureOf(piece.kind).flat).flatMap((piece) => cellsOf(piece).map(key)),
  )
}

/** The cells one gets to from the door. */
function reached(room: RoomPlan, blocked: Set<string>): Map<string, Cell> {
  const out = new Map<string, Cell>()
  const next = [room.door]
  while (next.length) {
    const cell = next.pop()!
    if (out.has(key(cell)) || blocked.has(key(cell))) continue
    if (cell.x < 0 || cell.z < 0 || cell.x >= room.w || cell.z >= room.d) continue
    out.set(key(cell), cell)
    next.push(c(cell.x + 1, cell.z), c(cell.x - 1, cell.z), c(cell.x, cell.z + 1), c(cell.x, cell.z - 1))
  }
  return out
}

/** Every place to be in the room that one gets to: what the furniture offers, and where to stand, well apart. */
export function placesOf(room: RoomPlan): Place[] {
  const blocked = blockedCells(room)
  const open = reached(room, blocked)
  const places: Place[] = []
  for (const [n, piece] of room.pieces.entries()) {
    for (const [m, use] of (furnitureOf(piece.kind).uses ?? []).entries()) {
      const from = use.from.map((cell) => cellOf(piece, cell)).find((cell) => open.has(key(cell)))
      if (!from) continue
      let at = pointOf(piece, use)
      // Standing at something: on free floor.
      if (use.pose === 'stand' && !open.has(key(c(Math.floor(at.x), Math.floor(at.z))))) at = middle(from)
      places.push({ id: `${piece.kind}${n}-${m}`, pose: use.pose, at, y: use.y, from, for: use.for, piece: n })
    }
  }
  const used = new Set(places.map((place) => key(place.from)))
  const spare = [...open.values()].filter(
    (cell) =>
      cell.x > 0 && cell.z > 0 && cell.x < room.w - 1 && cell.z < room.d - 1 && !used.has(key(cell)) &&
      Math.hypot(cell.x - room.door.x, cell.z - room.door.z) > 2,
  )
  const stands: Cell[] = []
  while (stands.length < STANDS) {
    let best: Cell | null = null
    let most = 1.9
    for (const cell of spare) {
      const near = stands.length
        ? Math.min(...stands.map((other) => Math.hypot(other.x - cell.x, other.z - cell.z)))
        : 99 - Math.hypot(cell.x - room.w * 0.55, cell.z - room.d * 0.6)
      if (near > most) {
        best = cell
        most = near
      }
    }
    if (!best) break
    stands.push(best)
  }
  return [
    ...places,
    ...stands.map((cell) => ({
      id: `stand${key(cell)}`,
      pose: 'stand' as const,
      at: middle(cell),
      y: 0,
      from: cell,
      for: [],
      piece: -1,
    })),
  ]
}

const ACTIVITIES: [Activity, RegExp][] = [
  ['cook', /做.{0,2}饭|做早餐|煮|炒|煎|烤|炖|蒸|切菜|下厨|揉面|洗碗|刷锅/],
  ['chess', /棋|打牌|桌游|扑克/],
  ['tv', /看.{0,2}剧|追剧|番剧|追番|看番|电影|电视|游戏|综艺|动画/],
  ['paint', /画/],
  ['water', /浇|盆栽|花草|植物|修枝/],
  ['desk', /写|复习|作业|电脑|代码|简历|笔记|学习|工作|整理|题|忙自己/],
  ['read', /书|读|小说|漫画|杂志/],
  ['tea', /茶|咖啡|牛奶|果汁/],
  ['eat', /吃|午饭|晚饭|早饭|早餐|点心|三明治|蛋糕/],
]

/** What she is at, from the words of her day: what comes first in them. */
export function activityOf(doing: string, pose: Pose): Activity {
  if (pose === 'lie') return 'sleep'
  let found: Activity = ''
  let first = Infinity
  for (const [activity, words] of ACTIVITIES) {
    const at = doing.search(words)
    if (at >= 0 && at < first) {
      found = activity
      first = at
    }
  }
  return found
}

/** The way from one cell to another around the furniture, both ends included; [] when there is none. Diagonally
 * only where both cells beside are free (nobody cuts a corner of a table). */
export function path(room: RoomPlan, from: Cell, to: Cell, blocked = blockedCells(room)): Cell[] {
  const free = (cell: Cell) =>
    cell.x >= 0 && cell.z >= 0 && cell.x < room.w && cell.z < room.d && !blocked.has(key(cell))
  if (!free(from) || !free(to)) return []
  const guess = (cell: Cell) => Math.hypot(cell.x - to.x, cell.z - to.z)
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
        const next = c(cell.x + dx, cell.z + dz)
        if (!free(next)) continue
        if (dx && dz && !(free(c(cell.x + dx, cell.z)) && free(c(cell.x, cell.z + dz)))) continue
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

export type Step = { at: Cell; y: number }

/** The points she goes through from where she is (`from`: a place, or the door) to another place: off the seat,
 * around the furniture, onto the seat. */
export function route(room: RoomPlan, from: { from: Cell }, to: Place): Step[] {
  const way = path(room, from.from, to.from).map((cell) => ({ at: middle(cell), y: 0 }))
  return [...way, { at: to.at, y: to.y }]
}
