import assert from 'node:assert/strict'
import { test } from 'node:test'

import { blockedCells, FURNITURE, LIVING_ROOM as ROOM, path, placesOf, route, type Cell } from '../src/lib/dorm.ts'
import { seatOn, wanderOn } from '../src/lib/home-stage.ts'

const key = (c: Cell) => `${c.x},${c.z}`
const blocked = blockedCells(ROOM)

test('the furniture stands in the room, none in another', () => {
  let cells = 0
  for (const piece of ROOM.pieces) {
    const { w, d, flat } = FURNITURE[piece.kind]
    assert.ok(piece.x >= 0 && piece.z >= 0 && piece.x + w <= ROOM.w && piece.z + d <= ROOM.d, piece.kind)
    if (!flat) cells += w * d
  }
  assert.equal(blocked.size, cells)
})

test('every place can be reached from the door, around the furniture', () => {
  const places = placesOf(ROOM)
  assert.ok(places.filter((place) => place.pose === 'stand').length >= 6)
  assert.ok(places.filter((place) => place.pose === 'sit').length >= 3)
  assert.equal(new Set(places.map((place) => place.id)).size, places.length)
  for (const place of places) {
    const way = path(ROOM, ROOM.door, place.from)
    assert.ok(way.length, place.id)
    for (const cell of way) assert.ok(!blocked.has(key(cell)), place.id)
    for (let n = 1; n < way.length; n++) {
      const [a, b] = [way[n - 1], way[n]]
      assert.ok(Math.abs(a.x - b.x) <= 1 && Math.abs(a.z - b.z) <= 1)
      // No corner of a table cut.
      assert.ok(!blocked.has(key({ x: a.x, z: b.z })) && !blocked.has(key({ x: b.x, z: a.z })))
    }
  }
})

test('onto a seat and off it again', () => {
  const [a, b] = placesOf(ROOM).filter((place) => place.pose === 'sit' && place.id.startsWith('sofa'))
  const way = route(ROOM, a, b)
  assert.deepEqual(way.at(-1), { at: b.at, y: b.y })
  assert.ok(way.slice(0, -1).every((step) => step.y === 0))
})

test('no way into a wall or the sofa', () => {
  assert.deepEqual(path(ROOM, ROOM.door, { x: 5, z: 0 }), [])
  assert.deepEqual(path(ROOM, ROOM.door, { x: 99, z: 0 }), [])
  assert.deepEqual(path(ROOM, ROOM.door, ROOM.door), [ROOM.door])
})

test('who sits gets a seat, who stands wanders', () => {
  const spots = placesOf(ROOM)
  const places = seatOn(spots, [
    { id: 'a', pose: 'sit' },
    { id: 'b', pose: 'stand' },
  ])
  assert.ok(places.a.pose === 'sit' && places.b.pose === 'stand')
  const to = wanderOn(spots, places, 'b', () => 0.5)
  assert.ok(to && to !== places.b && to.pose === 'stand')
  assert.equal(wanderOn(spots, places, 'a', () => 0.5), null)
})
