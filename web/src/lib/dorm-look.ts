/** 3D 小窝: what a room's walls and floor are like and what hangs there, as data (lib/dorm-paint.ts draws it). What
 * hangs is placed on a grid of quarter metres: `at` along its wall from the far corner, `y` up from the floor. */

export type Pattern =
  | { kind: 'plain'; base: string }
  | { kind: 'stripes' | 'dots' | 'stars' | 'checks' | 'leaves' | 'flowers' | 'bricks' | 'waves'; base: string; on: string; more?: string }

export type Wallpaper = {
  label: string
  pattern: Pattern
  /** Boards up to the hip, over the paper. */
  boards?: string
}

export const WALLPAPERS = {
  cream: { label: '奶油条纹', pattern: { kind: 'stripes', base: '#f9ecdd', on: '#f5e4d2' } },
  mint: { label: '薄荷条纹', pattern: { kind: 'stripes', base: '#edf4ea', on: '#dfebdc' } },
  lilac: { label: '淡紫条纹', pattern: { kind: 'stripes', base: '#f0edf7', on: '#e3deee' } },
  sand: { label: '沙色木板', pattern: { kind: 'stripes', base: '#f3e9dd', on: '#e8dac8' } },
  pink: { label: '粉条纹', pattern: { kind: 'stripes', base: '#faebed', on: '#f2dbe0' } },
  sky: { label: '天蓝条纹', pattern: { kind: 'stripes', base: '#ecf2f9', on: '#dbe5f2' } },
  lemon: { label: '柠檬格子', pattern: { kind: 'checks', base: '#fcf4e0', on: '#f2e2b8' } },
  rosy: { label: '樱花波点', pattern: { kind: 'dots', base: '#fbeef0', on: '#eeb9c2' } },
  bubbles: { label: '泡泡波点', pattern: { kind: 'dots', base: '#e9f3f6', on: '#b9d6e2' } },
  floral: { label: '碎花', pattern: { kind: 'flowers', base: '#fbf5ea', on: '#e6a39b', more: '#9dbb93' } },
  ivy: { label: '叶子', pattern: { kind: 'leaves', base: '#eef3e8', on: '#a9c3a0' } },
  waves: { label: '海浪', pattern: { kind: 'waves', base: '#e6f0f4', on: '#b2cfdd' } },
  starry: { label: '星夜', pattern: { kind: 'stars', base: '#4b5283', on: '#f3e3ae' } },
  stardust: { label: '星星淡紫', pattern: { kind: 'stars', base: '#f1ebfa', on: '#e8c871' } },
  maid: { label: '黑白墙裙', pattern: { kind: 'stripes', base: '#f7f4f8', on: '#ece7f0' }, boards: '#4a4152' },
  tide: { label: '淡紫海浪', pattern: { kind: 'waves', base: '#f1eef9', on: '#cfc4ec' } },
  haitang: { label: '海棠碎花', pattern: { kind: 'flowers', base: '#f8ede9', on: '#d97f8c', more: '#8fa886' } },
  bricks: { label: '白砖', pattern: { kind: 'bricks', base: '#f4f0ea', on: '#dcd4c8' } },
  clay: { label: '红砖', pattern: { kind: 'bricks', base: '#d9a58e', on: '#eed9cc' } },
  white: { label: '纯白', pattern: { kind: 'plain', base: '#faf7f2' } },
  cabin: { label: '木墙裙', pattern: { kind: 'plain', base: '#f7efe2' }, boards: '#d7b28c' },
  garden: { label: '碎花墙裙', pattern: { kind: 'flowers', base: '#f3f6ee', on: '#e2b1b6', more: '#a5c09b' }, boards: '#fffaf2' },
} satisfies Record<string, Wallpaper>

export type WallKind = keyof typeof WALLPAPERS
export const wallpaperOf = (kind: WallKind): Wallpaper => WALLPAPERS[kind]

export type FloorPattern = 'planks' | 'herringbone' | 'tiles' | 'stone' | 'deck' | 'carpet' | 'tatami'
export type Flooring = { label: string; pattern: FloorPattern; base: string; on: string; edge: string }

export const FLOORS = {
  oak: { label: '浅木地板', pattern: 'planks', base: '#dcb38c', on: '#a87650', edge: '#b98a66' },
  walnut: { label: '深木地板', pattern: 'planks', base: '#a8795a', on: '#6b4733', edge: '#7d573f' },
  ash: { label: '灰白木', pattern: 'planks', base: '#ddd3c6', on: '#a79b8c', edge: '#b7ab9c' },
  herringbone: { label: '人字拼', pattern: 'herringbone', base: '#d6ab82', on: '#96683f', edge: '#b28560' },
  mint: { label: '薄荷格砖', pattern: 'tiles', base: '#f5f3ec', on: '#dfe9e4', edge: '#c9d4ce' },
  rose: { label: '粉格砖', pattern: 'tiles', base: '#f8f1ee', on: '#f0d5d5', edge: '#d9bfc0' },
  checker: { label: '黑白格砖', pattern: 'tiles', base: '#f2efe9', on: '#8b8791', edge: '#7a7681' },
  stone: { label: '石板', pattern: 'stone', base: '#cfcac1', on: '#a7a197', edge: '#a39c92' },
  deck: { label: '户外木板', pattern: 'deck', base: '#bba58c', on: '#6f5b48', edge: '#8f7a64' },
  carpet: { label: '米色地毯', pattern: 'carpet', base: '#e9dccb', on: '#d8c7b1', edge: '#cdbba4' },
  cloud: { label: '雾蓝地毯', pattern: 'carpet', base: '#d3dcea', on: '#bfcbdd', edge: '#aebbd0' },
  tatami: { label: '榻榻米', pattern: 'tatami', base: '#d9d3a4', on: '#7f8a5a', edge: '#b1aa7d' },
} satisfies Record<string, Flooring>

export type FloorKind = keyof typeof FLOORS
export const flooringOf = (kind: FloorKind): Flooring => FLOORS[kind]

// -- what hangs there ----------------------------------------------------------------------------------------

type Hanging = { label: string; group: '画' | '挂件'; w: number; h: number }

export const HANGINGS = {
  hills: { label: '山丘', group: '画', w: 4, h: 3 },
  sea: { label: '海边', group: '画', w: 4, h: 3 },
  flower: { label: '一朵花', group: '画', w: 2, h: 3 },
  cat: { label: '小猫', group: '画', w: 3, h: 3 },
  moon: { label: '月亮', group: '画', w: 2, h: 3 },
  fruit: { label: '水果', group: '画', w: 3, h: 2 },
  town: { label: '小镇', group: '画', w: 5, h: 3 },
  photos: { label: '照片墙', group: '画', w: 5, h: 3 },
  clock: { label: '挂钟', group: '挂件', w: 2, h: 2 },
  mirror: { label: '镜子', group: '挂件', w: 2, h: 4 },
  shelf: { label: '置物架', group: '挂件', w: 6, h: 2 },
  hangplant: { label: '吊兰', group: '挂件', w: 2, h: 3 },
  flags: { label: '彩旗', group: '挂件', w: 8, h: 2 },
  lights: { label: '小灯串', group: '挂件', w: 10, h: 1 },
  wreath: { label: '花环', group: '挂件', w: 2, h: 2 },
  calendar: { label: '日历', group: '挂件', w: 2, h: 3 },
  stars: { label: '星星挂饰', group: '挂件', w: 6, h: 4 },
  apron: { label: '女仆裙', group: '挂件', w: 2, h: 4 },
  fan: { label: '折扇', group: '挂件', w: 4, h: 2 },
  haitang: { label: '海棠', group: '画', w: 3, h: 3 },
} satisfies Record<string, Hanging>

export type HangKind = keyof typeof HANGINGS
export const hangingOf = (kind: HangKind): Hanging => HANGINGS[kind]

export type Wall = 'left' | 'right'
export type Hung = { kind: HangKind; wall: Wall; at: number; y: number }
export type Look = { wall: WallKind; floor: FloorKind; hung: Hung[] }

/** What of a room the walls need to know. */
export type Walls = { w: number; d: number; door: { x: number }; open?: boolean }

export const MAX_HUNG = 30
/** How high things may hang, in quarter metres (the walls are a little higher). */
const TOP = 11
const PER_CELL = 2

type Rect = { at: number; y: number; w: number; h: number }

/** What of a wall is taken already: the door in the right one, the window and its curtains in the left one. */
function fixed(room: Walls, wall: Wall): Rect[] {
  return wall === 'right'
    ? [{ at: room.door.x * PER_CELL - 1, y: 0, w: 4, h: 9 }]
    : [{ at: 2, y: 2, w: 10, h: 9 }]
}

const meet = (a: Rect, b: Rect) => a.at < b.at + b.w && b.at < a.at + a.w && a.y < b.y + b.h && b.y < a.y + a.h

const rectOf = (hung: Hung): Rect => ({ at: hung.at, y: hung.y, w: hangingOf(hung.kind).w, h: hangingOf(hung.kind).h })

/** How long a wall is, in quarter metres. */
export const lengthOf = (room: Walls, wall: Wall): number => (wall === 'right' ? room.w : room.d) * PER_CELL

/** Whether it may hang there: on a wall there is, not on the window, the door or another. `but`: one not to mind. */
export function canHang(room: Walls, all: Hung[], hung: Hung, but?: Hung): boolean {
  if (hung.wall === 'left' && room.open) return false
  const r = rectOf(hung)
  if (r.at < 0 || r.y < 0 || r.at + r.w > lengthOf(room, hung.wall) || r.y + r.h > TOP) return false
  const others = all.filter((other) => other !== but && other.wall === hung.wall).map(rectOf)
  return ![...fixed(room, hung.wall), ...others].some((other) => meet(r, other))
}

/** A free spot for a new one, about as high as one looks; null: there is none. */
export function spotFor(room: Walls, all: Hung[], kind: HangKind): Hung | null {
  const { w, h } = hangingOf(kind)
  let best: Hung | null = null
  let least = Infinity
  for (const wall of ['right', 'left'] as Wall[]) {
    for (let at = 0; at <= lengthOf(room, wall) - w; at++) {
      for (let y = 2; y <= TOP - h; y++) {
        const far = Math.abs(y + h / 2 - 7.5) * 2 + Math.abs(at + w / 2 - lengthOf(room, wall) * 0.4) * 0.5 + (wall === 'left' ? 3 : 0)
        if (far >= least) continue
        const hung: Hung = { kind, wall, at, y }
        if (!canHang(room, all, hung)) continue
        best = hung
        least = far
      }
    }
  }
  return best
}

const has = <T extends object>(all: T, kind: unknown): kind is keyof T => typeof kind === 'string' && Object.hasOwn(all, kind)

/** A saved look as far as it still makes sense; what is not known is as it was (`usual`). */
export function cleanLook(room: Walls, saved: unknown, usual: Look): Look {
  const { wall, floor, hung } = (saved && typeof saved === 'object' ? saved : {}) as Record<string, unknown>
  const out: Hung[] = []
  for (const item of Array.isArray(hung) ? hung.slice(0, MAX_HUNG) : usual.hung) {
    const { kind, wall: on, at, y } = (item ?? {}) as Record<string, unknown>
    if (!has(HANGINGS, kind) || (on !== 'left' && on !== 'right') || !Number.isInteger(at) || !Number.isInteger(y)) continue
    const one: Hung = { kind, wall: on, at: at as number, y: y as number }
    if (canHang(room, out, one)) out.push(one)
  }
  return {
    wall: has(WALLPAPERS, wall) ? wall : usual.wall,
    floor: has(FLOORS, floor) ? floor : usual.floor,
    hung: out,
  }
}
