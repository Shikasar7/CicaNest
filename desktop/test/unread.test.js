'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')

const { Unread } = require('../src/unread')

test('her words in the chat app count while you are not looking, once each', () => {
  let looking = false
  const seen = []
  const unread = new Unread({ watching: () => looking, onChange: (n) => seen.push(n) })
  unread.heard({ from_self: true, external_id: 'app:abc' })
  unread.heard({ from_self: true, external_id: 'app:abc' }) // a 桌面群 copy of the same message
  unread.heard({ from_self: false, external_id: 'app:mine' }) // what you said
  unread.heard({ from_self: true, external_id: '12345' }) // said on QQ, not in the app
  unread.heard({ from_self: true, external_id: 'app:o:def' }) // 主动找你
  assert.equal(unread.count, 2)
  assert.deepEqual(seen, [1, 2])

  looking = true
  unread.heard({ from_self: true, external_id: 'app:ghi' })
  assert.equal(unread.count, 2)
  unread.clear()
  unread.clear()
  assert.deepEqual(seen, [1, 2, 0])
})
