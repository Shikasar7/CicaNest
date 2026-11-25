'use strict'

// What the splash shows on the way: QQ only where there is some, the companions asleep until they are there.

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const Module = require('node:module')

const load = Module._load
Module._load = function (request, ...rest) {
  return request === 'electron' ? { app: { getPath: () => '.' } } : load.call(this, request, ...rest)
}
const { Launch, glyphsFor } = require('../src/launcher')
const { FACES, faceOf } = require('../src/ui/face')

function launch({ accounts = [], snowluma = { configured: false } } = {}) {
  const nest = {
    start: async () => ({ already: true }),
    readRuntime: () => ({ token: 't' }),
    api: async (_method, where) =>
      where === '/api/accounts' ? { status: 200, json: accounts } : { status: 200, json: { snowluma } },
  }
  const seen = []
  const run = new Launch(nest, { onLogin: async () => {} })
  run.on('change', (state) => seen.push(state))
  return new Promise((resolve) => {
    run.on('finish', () => resolve(seen))
    run.start()
  })
}

test('with companions only on this computer there is no QQ step, and everyone wakes up', async () => {
  const seen = await launch()
  assert.deepEqual(seen[0].steps.map((s) => s.label), ['启动 nest 后台', '登录控制台', '等伙伴上线'])
  assert.ok(seen[0].glyphs.length === 3 && seen[0].glyphs.every((g) => !g.online))
  const last = seen.at(-1)
  assert.equal(last.phase, 'done')
  assert.ok(last.steps.every((s) => s.state === 'done'))
  assert.ok(last.glyphs.every((g) => g.online))
})

test('where QQ is set up its step shows, and the faces are the accounts', async () => {
  const accounts = [{ id: 'qq:900000001', name: '阿鲸', enabled: true, status: { state: 'connected' } }]
  const last = (await launch({ accounts, snowluma: { configured: true, running: true } })).at(-1)
  assert.deepEqual(last.steps.map((s) => s.label), ['启动 nest 后台', '登录控制台', '启动 QQ 连接', '等伙伴上线'])
  assert.deepEqual(last.glyphs.map((g) => [g.name, g.online]), [['阿鲸', true]])
})

test('an account has the same face as in the console', () => {
  // Kept as one rule in two places: web/src/lib/persona.ts says which face, in this order.
  const persona = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'src', 'lib', 'persona.ts'), 'utf8')
  const order = /FACES[^=]*= \[([^\]]+)\]/.exec(persona)[1].split(',').map((f) => f.trim().replaceAll("'", ''))
  assert.deepEqual(FACES, order)
  assert.match(persona, /FACES\[\(h >>> 7\) % FACES\.length\]/)
  const [glyph] = glyphsFor([{ id: 'qq:900000001', name: '阿鲸' }])
  assert.equal(glyph.face, faceOf('qq:900000001'))
  assert.ok(FACES.includes(glyph.face))
})
