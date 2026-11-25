import assert from 'node:assert/strict'
import { test } from 'node:test'

import { fit, MAX_SHOWN, seat, spotsOf, wander, walkMs, type Occupant, type Spot } from '../src/lib/home-stage.ts'

const ROOMS = ['客厅', '厨房', '书房', '阳台', '阿鲸的卧室']
const who = (n: number, pose: Occupant['pose']): Occupant[] =>
  Array.from({ length: n }, (_, i) => ({ id: `bot${i}`, pose }))

test('every room has room for everyone drawn, each on a place of her own', () => {
  for (const room of ROOMS) {
    assert.ok(spotsOf(room).filter((spot) => spot.pose === 'stand').length >= MAX_SHOWN, room)
    for (const pose of ['stand', 'sit', 'lie'] as const) {
      const places = Object.values(seat(room, who(6, pose)))
      assert.equal(places.length, MAX_SHOWN)
      assert.equal(new Set(places).size, MAX_SHOWN)
    }
  }
})

test('nobody stands on another, not even in a narrow room', () => {
  for (const room of ROOMS) {
    const { left, scale } = fit(230, 160) // the sides are cut
    const xs = spotsOf(room)
      .filter((spot) => spot.pose === 'stand')
      .map((spot) => left(spot.x) / scale)
      .sort((a, b) => a - b)
    for (let n = 1; n < xs.length; n++) assert.ok(xs[n] - xs[n - 1] >= 38, `${room} ${xs}`)
  }
})

test('she sits when she sits, lies when she lies, and stands when there is no such place left', () => {
  assert.equal(seat('客厅', who(1, 'sit')).bot0.pose, 'sit')
  assert.equal(seat('阿鲸的卧室', who(1, 'lie')).bot0.pose, 'lie')
  assert.equal(seat('客厅', who(1, 'lie')).bot0.pose, 'sit')
  const poses = Object.values(seat('客厅', who(4, 'sit'))).map((spot) => spot.pose)
  assert.deepEqual(poses.sort(), ['sit', 'sit', 'stand', 'stand'])
})

test('who was there stays where she was; who does something else moves', () => {
  const [a, b] = who(2, 'sit')
  const first = seat('客厅', [a])
  assert.equal(seat('客厅', [b, a], first).bot0, first.bot0)
  assert.equal(seat('客厅', [{ ...a, pose: 'stand' }], first).bot0.pose, 'stand')
  // Standing because the seats were taken: she stays there as long as they are.
  const full = seat('客厅', who(4, 'sit'))
  assert.deepEqual(seat('客厅', who(4, 'sit'), full), full)
  // A place of another room is none here.
  assert.ok(spotsOf('书房').includes(seat('书房', [a], first).bot0))
})

test('only who stands wanders, to a free place to stand', () => {
  const places = seat('客厅', [
    { id: 'a', pose: 'stand' },
    { id: 'b', pose: 'sit' },
  ])
  assert.equal(wander('客厅', places, 'b', () => 0), null)
  for (const n of [0, 0.5, 0.999, 1]) {
    const to = wander('客厅', places, 'a', () => n) as Spot
    assert.ok(to.pose === 'stand' && to !== places.a)
  }
  assert.equal(wander('客厅', places, 'nobody', () => 0), null)
})

test('walking takes as long as it is far', () => {
  const spot = (x: number): Spot => ({ x, y: 170, pose: 'stand' })
  assert.equal(walkMs(spot(10), spot(10)), 0)
  assert.ok(walkMs(spot(0), spot(200)) > walkMs(spot(0), spot(100)))
})

test('on the floor of the drawing however the room is cut', () => {
  const wide = fit(640, 180) // twice as wide: the top is cut
  assert.equal(wide.scale, 2)
  assert.equal(wide.top(180), 180)
  assert.equal(wide.left(160), 320)
  const narrow = fit(160, 180) // half as wide: the sides are cut
  assert.equal(narrow.left(160), 80)
  assert.ok(narrow.left(10) > 0 && narrow.left(310) < 160) // brought inside
  assert.ok(narrow.left(-24) < 0) // the door
})
