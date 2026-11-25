import assert from 'node:assert/strict'
import { test } from 'node:test'

import { planOf, SHARED_ROOMS } from '../src/lib/dorm.ts'
import { canHang, cleanLook, HANGINGS, spotFor, type HangKind, type Hung, type Look } from '../src/lib/dorm-look.ts'

const ROOMS = [...SHARED_ROOMS, '卧室:bot'].map((key) => planOf(key))
const LIVING = planOf('客厅')
const at = (kind: HangKind, wall: Hung['wall'], x: number, y: number): Hung => ({ kind, wall, at: x, y })

test('every room as it comes: what hangs there may hang there', () => {
  for (const room of ROOMS) {
    assert.ok(room.look.hung.length > 0, room.name)
    assert.deepEqual(cleanLook(room, room.look, room.look), room.look, room.name)
  }
})

test('where things may hang', () => {
  assert.ok(canHang(LIVING, [], at('clock', 'right', 4, 8)))
  assert.ok(!canHang(LIVING, [], at('clock', 'right', -1, 8)))
  assert.ok(!canHang(LIVING, [], at('clock', 'right', 27, 8))) // over the end of the wall
  assert.ok(!canHang(LIVING, [], at('clock', 'right', 4, 10))) // too high
  assert.ok(!canHang(LIVING, [], at('clock', 'right', 24, 5))) // on the door
  assert.ok(canHang(LIVING, [], at('clock', 'right', 24, 9))) // over it
  assert.ok(!canHang(LIVING, [], at('clock', 'left', 5, 6))) // on the window
  assert.ok(canHang(LIVING, [], at('clock', 'left', 14, 6)))
  const there = at('hills', 'right', 4, 5)
  assert.ok(!canHang(LIVING, [there], at('clock', 'right', 6, 6)))
  assert.ok(canHang(LIVING, [there], at('hills', 'right', 5, 5), there)) // moved over itself
  assert.ok(canHang(LIVING, [there], at('clock', 'left', 14, 6)))
  // A balcony has no left wall.
  assert.ok(!canHang(planOf('阳台'), [], at('clock', 'left', 14, 6)))
})

test('something new hangs where there is room, or not at all', () => {
  for (const room of ROOMS) {
    const all: Hung[] = []
    for (let n = 0; n < 200; n++) {
      const hung = spotFor(room, all, 'clock')
      if (!hung) break
      assert.ok(canHang(room, all, hung))
      all.push(hung)
    }
    assert.ok(all.length > 8 && all.length < 200, `${room.name} ${all.length}`)
  }
  for (const kind of Object.keys(HANGINGS) as HangKind[]) assert.ok(spotFor(LIVING, [], kind), kind)
})

test('a saved look is read as far as it makes sense', () => {
  const usual: Look = { wall: 'cream', floor: 'oak', hung: [at('clock', 'right', 4, 8)] }
  assert.deepEqual(cleanLook(LIVING, { wall: 'toString', floor: 'lava' }, usual), usual)
  assert.deepEqual(cleanLook(LIVING, null, usual), usual)
  const read = cleanLook(
    LIVING,
    {
      wall: 'starry',
      floor: 'tatami',
      hung: [
        at('moon', 'right', 2, 5),
        at('cat', 'right', 3, 6), // on the moon
        at('cat', 'left', 5, 5), // on the window
        { kind: 'gun', wall: 'right', at: 9, y: 5 },
        { kind: 'cat', wall: 'ceiling', at: 9, y: 5 },
        { kind: 'cat', wall: 'right', at: 9.5, y: 5 },
        null,
      ],
    },
    usual,
  )
  assert.deepEqual(read, { wall: 'starry', floor: 'tatami', hung: [at('moon', 'right', 2, 5)] })
  assert.deepEqual(cleanLook(LIVING, { hung: [] }, usual).hung, [])
})

test('rooms saved before there were walls to choose keep their furniture', () => {
  const sofa = { kind: 'sofa', x: 4, z: 0, turn: 0 }
  const old = planOf('客厅', [sofa])
  assert.deepEqual(old.pieces, [sofa])
  assert.deepEqual(old.look, LIVING.look)
  const now = planOf('客厅', { pieces: [sofa], look: { wall: 'clay', floor: 'stone', hung: [] } })
  assert.deepEqual([now.pieces, now.look], [[sofa], { wall: 'clay', floor: 'stone', hung: [] }])
  assert.deepEqual(planOf('客厅', { pieces: [], look: null }).look, LIVING.look)
})
