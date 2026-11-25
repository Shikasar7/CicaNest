/** 小窝: who is where in a room and how. Only places and poses in the room's own units, no
 * picture: whatever draws the room (the line drawing now) puts them on its own floor. */

export type Pose = 'stand' | 'sit' | 'lie'

/** Where she can be in a room: her feet, in units of a 320×180 room (y goes down). */
export type Spot = { x: number; y: number; pose: Pose }

export type Occupant = { id: string; pose: Pose }

export const ROOM_W = 320
export const ROOM_H = 180
/** How tall she stands, in the same units. */
export const FIGURE = 72
/** At most so many are drawn in one room; the others are only counted. */
export const MAX_SHOWN = 4
/** Where she comes in. */
export const DOOR: Spot = { x: -24, y: 170, pose: 'stand' }

const WALK_SPEED = 34 // units a second

const at = (pose: Pose, ...spots: [number, number][]): Spot[] => spots.map(([x, y]) => ({ x, y, pose }))

/** Every room has at least MAX_SHOWN places to stand, apart from each other and away from the sides (which a
 * narrow room cuts off). */
const STAGES: Record<string, Spot[]> = {
  客厅: [
    ...at('sit', [156, 141], [202, 141]),
    ...at('stand', [58, 168], [110, 174], [166, 177], [218, 174], [268, 169]),
  ],
  厨房: [...at('sit', [112, 152], [190, 152]), ...at('stand', [58, 172], [150, 176], [212, 174], [272, 171])],
  书房: [...at('sit', [206, 156], [62, 172]), ...at('stand', [112, 171], [160, 175], [210, 177], [264, 172])],
  阳台: [...at('sit', [222, 150], [262, 150]), ...at('stand', [58, 168], [106, 173], [154, 169], [200, 176])],
  卧室: [
    ...at('lie', [112, 130]),
    ...at('sit', [176, 130]),
    ...at('stand', [58, 172], [112, 176], [184, 175], [252, 170]),
  ],
}

const INSTEAD: Record<Pose, Pose[]> = { lie: ['lie', 'sit', 'stand'], sit: ['sit', 'stand'], stand: ['stand'] }

export function spotsOf(room: string): Spot[] {
  return STAGES[room.endsWith('卧室') ? '卧室' : room] ?? STAGES['客厅']
}

function turn(id: string): number {
  let n = 0
  for (const c of id) n = (n * 31 + (c.codePointAt(0) ?? 0)) >>> 0
  return n
}

/** A place for each of `people`: where she was (`before`) if that still suits what she does, else a free one for
 * her pose, else a free one to stand. Never two on one. `spots`: those of one room, the same objects each time. */
export function seatOn<S extends { pose: Pose }>(
  spots: S[],
  people: Occupant[],
  before: Record<string, S> = {},
): Record<string, S> {
  const taken = new Set<S>()
  const places: Record<string, S> = {}
  const put = (id: string, spot: S) => {
    places[id] = spot
    taken.add(spot)
  }
  for (const { id, pose } of people) {
    const was = before[id]
    if (was && was.pose === pose && spots.includes(was) && !taken.has(was)) put(id, was)
  }
  for (const { id, pose } of people) {
    if (places[id]) continue
    for (const kind of INSTEAD[pose]) {
      const free = spots.filter((spot) => spot.pose === kind && !taken.has(spot))
      if (!free.length) continue
      put(id, free.includes(before[id]) ? before[id] : free[turn(id) % free.length])
      break
    }
  }
  return places
}

/** The same in a drawn room, for the first MAX_SHOWN. */
export function seat(room: string, people: Occupant[], before: Record<string, Spot> = {}): Record<string, Spot> {
  return seatOn(spotsOf(room), people.slice(0, MAX_SHOWN), before)
}

/** Another free place to stand for someone who is standing; null: she stays. */
export function wanderOn<S extends { pose: Pose }>(
  spots: S[],
  places: Record<string, S>,
  id: string,
  random: () => number,
): S | null {
  if (places[id]?.pose !== 'stand') return null
  const taken = new Set(Object.values(places))
  const free = spots.filter((spot) => spot.pose === 'stand' && !taken.has(spot))
  return free.length ? free[Math.min(free.length - 1, Math.floor(random() * free.length))] : null
}

export function wander(room: string, places: Record<string, Spot>, id: string, random: () => number): Spot | null {
  return wanderOn(spotsOf(room), places, id, random)
}

export function walkMs(from: Spot, to: Spot): number {
  return Math.round((Math.hypot(to.x - from.x, to.y - from.y) / WALK_SPEED) * 1000)
}

/** How a room is drawn in a box of that size: it fills the box, the bottom kept, the sides cut evenly
 * (preserveAspectRatio xMidYMax slice). */
export function fit(width: number, height: number) {
  const scale = Math.max(width / ROOM_W, height / ROOM_H)
  const margin = (FIGURE / 2) * scale * 0.7
  const inside = (px: number) =>
    width > margin * 2 ? Math.min(width - margin, Math.max(margin, px)) : width / 2
  return {
    scale,
    /** Where something of the drawing is. */
    x: (x: number) => (width - ROOM_W * scale) / 2 + x * scale,
    /** Where she is: nobody half behind the wall; only who comes in is outside. */
    left: (x: number) => (x < 0 ? x * scale : inside((width - ROOM_W * scale) / 2 + x * scale)),
    top: (y: number) => height - (ROOM_H - y) * scale,
  }
}

export type Daylight = 'morning' | 'day' | 'evening' | 'night'

export function daylightOf(hour: number): { light: Daylight; label: string } {
  if (hour >= 5 && hour < 10) return { light: 'morning', label: '早上' }
  if (hour >= 10 && hour < 17) return { light: 'day', label: '白天' }
  if (hour >= 17 && hour < 20) return { light: 'evening', label: '傍晚' }
  return { light: 'night', label: '夜里' }
}
