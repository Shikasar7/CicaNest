import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  activityOf,
  blockedCells,
  canStand,
  cellsOf,
  cleanPieces,
  FURNITURE,
  path,
  placesOf,
  planOf,
  pointOf,
  roomFor,
  route,
  SHARED_ROOMS,
  sizeOf,
  type Cell,
  type Kind,
  type Piece,
  type Turn,
} from '../src/lib/dorm.ts'
import { seatOn, wanderOn } from '../src/lib/home-stage.ts'

const ROOMS = [...SHARED_ROOMS, '卧室:bot'].map((key) => planOf(key))
const LIVING = planOf('客厅')
const key = (c: Cell) => `${c.x},${c.z}`
const TURNS: Turn[] = [0, 1, 2, 3]

test('every room as it comes: its furniture stands, nothing in another, every use can be reached', () => {
  for (const room of ROOMS) {
    assert.equal(cleanPieces(room, room.pieces).length, room.pieces.length, room.name)
    const places = placesOf(room)
    const uses = room.pieces.flatMap((piece) => (FURNITURE[piece.kind] as { uses?: unknown[] }).uses ?? [])
    assert.equal(places.filter((place) => place.piece >= 0).length, uses.length, room.name)
    assert.ok(places.filter((place) => place.piece < 0).length >= 4, room.name)
    assert.equal(new Set(places.map((place) => place.id)).size, places.length)
    const blocked = blockedCells(room)
    for (const place of places) {
      const way = path(room, room.door, place.from)
      assert.ok(way.length, `${room.name} ${place.id}`)
      for (const cell of way) assert.ok(!blocked.has(key(cell)))
      for (let n = 1; n < way.length; n++) {
        const [a, b] = [way[n - 1], way[n]]
        // No corner of a table cut.
        assert.ok(!blocked.has(key({ x: a.x, z: b.z })) && !blocked.has(key({ x: b.x, z: a.z })))
      }
    }
  }
})

test('a piece turned takes the cells its points are in', () => {
  for (const kind of Object.keys(FURNITURE) as Kind[]) {
    for (const turn of TURNS) {
      const piece: Piece = { kind, x: 3, z: 4, turn }
      const { w, d } = sizeOf(piece)
      const cells = new Set(cellsOf(piece).map(key))
      assert.equal(cells.size, w * d)
      const own = FURNITURE[kind]
      for (let x = 0; x < own.w; x++) {
        for (let z = 0; z < own.d; z++) {
          const at = pointOf(piece, { x: x + 0.5, z: z + 0.5 })
          assert.ok(cells.has(key({ x: Math.floor(at.x), z: Math.floor(at.z) })), `${kind} ${turn}`)
        }
      }
    }
  }
})

test('a sofa turned is sat on from its front', () => {
  const at = (turn: Turn) => placesOf({ ...LIVING, pieces: [{ kind: 'sofa', x: 4, z: 4, turn }] })[0]
  assert.ok(at(0).from.z === 6 && at(1).from.x === 6 && at(2).from.z === 3 && at(3).from.x === 3)
})

test('where furniture may stand', () => {
  const sofa = LIVING.pieces.find((piece) => piece.kind === 'sofa')!
  assert.ok(canStand(LIVING, sofa, sofa))
  assert.ok(!canStand(LIVING, { ...sofa }))
  assert.ok(!canStand(LIVING, { kind: 'plant', x: -1, z: 3, turn: 0 }))
  assert.ok(!canStand(LIVING, { kind: 'plant', x: 14, z: 3, turn: 0 }))
  assert.ok(!canStand(LIVING, { kind: 'plant', x: LIVING.door.x, z: 1, turn: 0 })) // before the door
  assert.ok(canStand(LIVING, { kind: 'plant', x: 12, z: 4, turn: 0 }))
  assert.ok(canStand(LIVING, { kind: 'mat', x: 10, z: 8, turn: 0 })) // under the chess table
  assert.ok(!canStand(LIVING, { kind: 'mat', x: 4, z: 3, turn: 0 })) // not on another rug
})

test('a new piece comes where there is room, or not at all', () => {
  for (const room of ROOMS) {
    const piece = roomFor(room, 'plant')
    assert.ok(piece && canStand(room, piece), room.name)
  }
  let room = { ...planOf('阳台'), pieces: [] as Piece[] }
  for (let n = 0; n < 40; n++) {
    const piece = roomFor(room, 'dining')
    if (!piece) break
    room = { ...room, pieces: [...room.pieces, piece] }
  }
  assert.ok(room.pieces.length > 2 && room.pieces.length < 8)
})

test('a saved room is read as far as it makes sense', () => {
  const kept = cleanPieces(LIVING, [
    { kind: 'sofa', x: 4, z: 0, turn: 0 },
    { kind: 'sofa', x: 5, z: 0, turn: 0 }, // in the first
    { kind: 'throne', x: 1, z: 1, turn: 0 },
    { kind: 'toString', x: 1, z: 1, turn: 0 },
    { kind: 'plant', x: 1.5, z: 3, turn: 0 },
    { kind: 'plant', x: 0, z: 3, turn: 7 },
    { kind: 'plant', x: 99, z: 3, turn: 0 },
    null,
    'x',
    { kind: 'plant', x: 0, z: 3, turn: 2, more: 1 },
  ])
  assert.deepEqual(kept, [
    { kind: 'sofa', x: 4, z: 0, turn: 0 },
    { kind: 'plant', x: 0, z: 3, turn: 2 },
  ])
  assert.deepEqual(planOf('客厅', []).pieces, [])
  assert.ok(planOf('客厅', 'nonsense').pieces.length > 3 && planOf('客厅').pieces.length > 3)
})

test('walled in, a seat is no place', () => {
  const wall: Piece[] = [0, 1, 2, 3].map((n) => ({ kind: 'plant', x: 2, z: n, turn: 0 }))
  const floor: Piece[] = [0, 1].map((x) => ({ kind: 'plant', x, z: 3, turn: 0 }))
  const room = { ...LIVING, pieces: [{ kind: 'beanbag', x: 0, z: 0, turn: 0 } as Piece, ...wall, ...floor] }
  assert.ok(!placesOf(room).some((place) => place.id.startsWith('beanbag')))
})

test('onto a seat and off it again', () => {
  const [a, b] = placesOf(LIVING).filter((place) => place.id.startsWith('sofa'))
  const way = route(LIVING, a, b)
  assert.deepEqual(way.at(-1), { at: b.at, y: b.y })
  assert.ok(way.slice(0, -1).every((step) => step.y === 0))
})

test('what she is at', () => {
  const said: [string, string][] = [
    ['喝茶看杂志', 'tea'],
    ['看书喝茶', 'read'],
    ['和阿马下棋', 'chess'],
    ['追番', 'tv'],
    ['煮粥，边搅边吃', 'cook'],
    ['做早饭、吃早饭', 'cook'],
    ['吃午饭', 'eat'],
    ['复习', 'desk'],
    ['画窗外的树', 'paint'],
    ['给阳台的花浇水', 'water'],
    ['发呆', ''],
  ]
  for (const [doing, activity] of said) assert.equal(activityOf(doing, 'sit'), activity, doing)
  assert.equal(activityOf('睡着了', 'lie'), 'sleep')
})

test('she goes where one does what she does, together with who does it too', () => {
  const spots = placesOf(LIVING)
  const places = seatOn(spots, [
    { id: 'a', pose: 'sit', for: 'chess' },
    { id: 'b', pose: 'sit', for: 'chess' },
    { id: 'c', pose: 'sit', for: 'tv' },
    { id: 'd', pose: 'stand', for: '' },
    { id: 'e', pose: 'sit', for: 'cook' }, // no stove here: any seat
  ])
  assert.ok(places.a.id.startsWith('chess') && places.b.id.startsWith('chess'))
  assert.equal(places.a.piece, places.b.piece)
  assert.ok(places.c.for.includes('tv') && places.d.pose === 'stand' && places.e.pose === 'sit')
  // She takes up something else: she goes there, and whoever stays at it stays.
  const later = seatOn(spots, [{ id: 'a', pose: 'sit', for: 'tv' }, { id: 'b', pose: 'sit', for: 'chess' }], places)
  assert.ok(later.a.for.includes('tv'))
  assert.equal(later.b, places.b)
  const kitchen = placesOf(planOf('厨房'))
  const cooks = seatOn(kitchen, [
    { id: 'a', pose: 'stand', for: 'cook' },
    { id: 'b', pose: 'stand', for: 'cook' },
  ])
  assert.ok(cooks.a.id.startsWith('stove') && cooks.b.for.includes('cook') && cooks.b !== cooks.a)
  const study = placesOf(planOf('书房'))
  assert.ok(seatOn(study, [{ id: 'a', pose: 'sit', for: 'read' }]).a.id.startsWith('armchair'))
})

test('who only stands there wanders; who is at something stays at it', () => {
  const spots = placesOf(LIVING)
  const free = spots.filter((spot) => spot.piece < 0)
  const places = seatOn(spots, [
    { id: 'a', pose: 'sit' },
    { id: 'b', pose: 'stand' },
  ])
  const to = wanderOn(free, places, 'b', () => 0.5)
  assert.ok(to && to !== places.b && to.piece < 0)
  assert.equal(wanderOn(free, places, 'a', () => 0.5), null)
})

test('no way into a wall or the sofa', () => {
  assert.deepEqual(path(LIVING, LIVING.door, { x: 5, z: 0 }), [])
  assert.deepEqual(path(LIVING, LIVING.door, { x: 99, z: 0 }), [])
  assert.deepEqual(path(LIVING, LIVING.door, LIVING.door), [LIVING.door])
})
