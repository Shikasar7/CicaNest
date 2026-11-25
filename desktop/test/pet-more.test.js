'use strict'

// One overlay per screen with mixed scales, two pets greeting each other, the two addresses of a nest.

const test = require('node:test')
const assert = require('node:assert')
const Module = require('node:module')
const os = require('node:os')
const path = require('node:path')

// Without Electron: the few things these modules take from it at load.
const load = Module._load
Module._load = (name, ...rest) =>
  name === 'electron'
    ? { BrowserWindow: class {}, Menu: {}, ipcMain: {}, powerMonitor: {}, screen: {}, net: {}, session: {} }
    : load(name, ...rest)
const { overlayAreas, petMenu } = require('../src/pets')
const { pickOrigin } = require('../src/remote')
const { loadConfig } = require('../src/config')
Module._load = load
const social = require('../src/pet/social')

const display = (id, x, scaleFactor, label = '') => ({
  id,
  label,
  scaleFactor,
  workArea: { x, y: 0, width: 1920, height: 1040 },
})

test('one overlay for screens of one scale, one per screen with mixed scales', () => {
  const primary = display(1, 0, 1.25)
  const same = overlayAreas([display(2, 1920, 1.25), primary], primary)
  assert.deepEqual(
    same.map((a) => [a.key, a.screens.length]),
    [['all', 2]],
  )
  const mixed = overlayAreas([display(2, 1920, 1, 'DELL'), primary], primary)
  assert.deepEqual(
    mixed.map((a) => [a.key, a.label, a.bounds.x, a.screens]),
    [
      ['1', '', 0, [{ x: 0, y: 0, width: 1920, height: 1040 }]],
      ['2', 'DELL', 1920, [{ x: 0, y: 0, width: 1920, height: 1040 }]],
    ],
  )
})

test('her menu offers the other screens', () => {
  const moved = []
  const menu = petMenu({
    name: '阿鲸',
    docked: false,
    onTop: true,
    skins: [],
    accessories: [],
    screens: [{ key: '2', label: 'DELL' }],
    do: { moveTo: (key) => moved.push(key) },
  })
  menu.find((item) => item.label === '搬到另一块屏').submenu[0].click()
  assert.deepEqual(moved, ['2'])
})

const pet = (botId, x, more = {}) => ({
  botId,
  x,
  y: 100,
  mode: 'idle',
  dock: null,
  pack: { size: 100, baseline: 90, scale: 1 },
  ...more,
})

test('two pets side by side with nothing to do may greet each other', () => {
  social.reset()
  const [a, b, c] = [pet('a', 0), pet('b', 110), pet('c', 600)]
  assert.deepEqual(
    social.together([a, b, c], 0).map((p) => p.botId),
    ['a', 'b'],
  )
  assert.equal(social.together([a, c], 0), null) // too far apart
  for (const busy of [{ mode: 'walk' }, { working: 1 }, { asking: 1 }, { open: true }, { dock: {} }]) {
    assert.equal(social.together([a, pet('b', 110, busy)], 0), null)
  }
  assert.equal(social.together([a, pet('b', 110, { y: 400 })], 0), null) // not on the same ground
})

test('of two addresses the first that answers is used', async () => {
  const config = loadConfig({}, { remote: 'nest.ts.net:18800', other: 'http://mac.local:18800' })
  assert.deepEqual(config.origins, ['http://nest.ts.net:18800', 'http://mac.local:18800'])
  const up =
    (...hosts) =>
    async (origin) =>
      hosts.some((h) => origin.includes(h)) ? { app: 'nest' } : null
  assert.equal(await pickOrigin(config.origins, up('ts.net', 'mac')), 'http://nest.ts.net:18800')
  assert.equal(await pickOrigin(config.origins, up('mac')), 'http://mac.local:18800')
  assert.equal(await pickOrigin(config.origins, up()), 'http://nest.ts.net:18800')
  assert.deepEqual(loadConfig({}, { other: 'mac.local' }).origins, []) // a spare without a first is nothing
})

test('installed: its own folder, and nothing is chosen yet; from the sources nothing changes', () => {
  const profile = path.join(os.tmpdir(), 'nest-profile')
  const fresh = loadConfig({}, {}, profile)
  assert.equal(fresh.home, path.join(profile, 'home'))
  assert.equal(fresh.undecided, true)
  assert.equal(fresh.isTestCopy, false)
  assert.equal(loadConfig({}, { remote: 'mac.local' }, profile).undecided, false)
  assert.equal(loadConfig({ NEST_HOME: profile }, {}, profile).undecided, false)
  assert.equal(loadConfig({}, {}).undecided, false)
})
