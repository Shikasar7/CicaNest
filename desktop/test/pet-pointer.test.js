'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')

const { Pointer, ASK_MS, REST_MS } = require('../src/pet/pointer')

test('a mouse not heard of for a while is asked for, once a second and only while it is here', () => {
  const pointer = new Pointer()
  assert.equal(pointer.shouldAsk(99_999), false) // not over the overlay
  pointer.at(10, 20, 100_000)
  assert.equal(pointer.shouldAsk(100_000 + ASK_MS - 1), false)
  assert.equal(pointer.shouldAsk(100_000 + ASK_MS), true)
  assert.equal(pointer.shouldAsk(100_000 + ASK_MS + 1), false)
  assert.equal(pointer.shouldAsk(100_000 + ASK_MS * 2), true)
  pointer.at(-1, -1, 103_000)
  assert.equal(pointer.shouldAsk(200_000), false)
})

test('a mouse that does not move rests; the same place told again is no move', () => {
  const pointer = new Pointer()
  assert.equal(pointer.at(10, 20, 1000), true)
  assert.equal(pointer.at(10, 21, 5000), false)
  assert.equal(pointer.resting(1000 + REST_MS), false)
  assert.equal(pointer.resting(1001 + REST_MS), true)
  assert.equal(pointer.at(30, 20, 30_000), true)
  assert.equal(pointer.resting(30_001), false)
})
