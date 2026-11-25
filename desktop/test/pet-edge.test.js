'use strict'

// 桌宠: 贴边藏起 (where she hides and peeks out), the overlay over several screens, her right-click menu.
// node --test test/

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const edge = require('../src/pet/edge')
const { overlayArea, petMenu, loadPacks } = require('../src/pets')

// One 1920×1040 work area; a pet canvas of 200 with her figure in the middle of it.
const ONE = [{ x: 0, y: 0, width: 1920, height: 1040 }]
const SIZE = 200
const BOX = { left: 50, top: 40, right: 150, bottom: 190 } // 100 wide, 150 tall
const at = (x, y) => ({ left: x + BOX.left, top: y + BOX.top, right: x + BOX.right, bottom: y + BOX.bottom })

test('put down against the left, right or top edge she hides there; elsewhere she does not', () => {
  assert.equal(edge.dockSide(at(-60, 500), ONE), 'left') // figure past the left edge
  assert.equal(edge.dockSide(at(-40, 500), ONE), 'left') // within SNAP of it
  assert.equal(edge.dockSide(at(1920 - 150 + 20, 500), ONE), 'right')
  assert.equal(edge.dockSide(at(800, -40), ONE), 'top')
  assert.equal(edge.dockSide(at(800, 500), ONE), null)
  assert.equal(edge.dockSide(at(-20, 500), ONE), null) // 30 px from the edge: not yet
  // The bottom is the ground she stands on.
  assert.equal(edge.dockSide(at(800, 1040 - 190), ONE), null)
})

test('in a corner the edge she is pushed furthest against wins', () => {
  assert.equal(edge.dockSide(at(-70, -45), ONE), 'left') // 34 past the left, 19 past the top
  assert.equal(edge.dockSide(at(-45, -70), ONE), 'top')
})

test('two screens: an edge with another screen behind it is not one to hide at', () => {
  const two = [
    { x: 0, y: 0, width: 1920, height: 1040 },
    { x: 1920, y: 0, width: 1920, height: 1040 },
  ]
  // At the right edge of the left screen she would show up on the right one.
  assert.equal(edge.dockSide(at(1920 - 150 + 20, 500), two), null)
  // The outer edges still work, on whichever screen she is.
  assert.equal(edge.dockSide(at(3840 - 150 + 20, 500), two), 'right')
  assert.equal(edge.dockSide(at(-60, 500), two), 'left')
  assert.equal(edge.dockSide(at(2500, -40), two), 'top')
  // A screen stacked above: its bottom meets the lower screen's top only where they overlap.
  const stacked = [
    { x: 0, y: 1080, width: 1920, height: 1040 },
    { x: 0, y: 0, width: 960, height: 1080 },
  ]
  assert.equal(edge.dockSide(at(400, 1080 - 40), stacked), null) // under the upper screen
  assert.equal(edge.dockSide(at(1400, 1080 - 40), stacked), 'top') // no screen above there
})

test('a screen with a different origin: its own edges count, not the page edges', () => {
  const screens = [
    { x: 0, y: 200, width: 1920, height: 1040 },
    { x: 1920, y: 0, width: 1280, height: 1240 },
  ]
  // 50 px below the first screen's top (which starts at 200 on the page): not yet; at it: yes.
  assert.equal(edge.dockSide(at(400, 210), screens), null)
  assert.equal(edge.dockSide(at(400, 170), screens), 'top')
  // The taller screen beside it reaches higher: its own top is the page top.
  assert.equal(edge.dockSide(at(2400, 170), screens), null)
  assert.equal(edge.dockSide(at(2400, -30), screens), 'top')
})

test('screenAt: the screen she is on, else the nearest', () => {
  const two = [
    { x: 0, y: 0, width: 100, height: 100 },
    { x: 100, y: 0, width: 100, height: 100 },
  ]
  assert.equal(edge.screenAt(two, { x: 150, y: 50 }), two[1])
  assert.equal(edge.screenAt(two, { x: -30, y: 50 }), two[0])
  assert.equal(edge.screenAt(two, { x: 260, y: 300 }), two[1])
})

test('hidden she is all behind the edge; peeking only part of her shows', () => {
  const s = ONE[0]
  // Left: hidden, her figure's right side is past the edge.
  const hidden = edge.dockPlace('left', s, 500, BOX, SIZE, 0)
  assert.ok(hidden.x + BOX.right <= s.x)
  assert.equal(hidden.y, 500)
  const peek = edge.dockPlace('left', s, 500, BOX, SIZE, 1)
  const shown = peek.x + BOX.right - s.x
  assert.ok(shown > 40 && shown < 100, `shows ${shown}px of 100`)
  // Halfway out is between the two.
  const half = edge.dockPlace('left', s, 500, BOX, SIZE, 0.5)
  assert.ok(half.x > hidden.x && half.x < peek.x)

  // Right: mirrored.
  const rHidden = edge.dockPlace('right', s, 500, BOX, SIZE, 0)
  assert.ok(rHidden.x + BOX.left >= s.x + s.width)
  const rPeek = edge.dockPlace('right', s, 500, BOX, SIZE, 1)
  const rShown = s.x + s.width - (rPeek.x + BOX.left)
  assert.ok(rShown > 40 && rShown < 100)

  // Top: upside down, so her figure box turns about the canvas middle.
  const flipped = edge.figureFor('top', BOX, SIZE)
  assert.deepEqual(flipped, { left: 50, right: 150, top: 10, bottom: 160 })
  const tHidden = edge.dockPlace('top', s, 800, BOX, SIZE, 0)
  assert.ok(tHidden.y + flipped.bottom <= s.y)
  assert.equal(tHidden.x, 800)
  const tPeek = edge.dockPlace('top', s, 800, BOX, SIZE, 1)
  const tShown = tPeek.y + flipped.bottom - s.y
  assert.ok(tShown > 50 && tShown < 150)
})

test('along the edge she stays beside her screen', () => {
  const s = ONE[0]
  assert.equal(edge.dockPlace('left', s, -500, BOX, SIZE, 0).y, s.y - BOX.top)
  assert.equal(edge.dockPlace('left', s, 5000, BOX, SIZE, 0).y, s.y + s.height - BOX.bottom)
  assert.equal(edge.dockPlace('top', s, 9000, BOX, SIZE, 0).x, s.x + s.width - BOX.right)
})

test('her hands hold the edge she hides at, about where her shoulders are, and stay on the screen', () => {
  const s = ONE[0]
  const left = edge.handsPlace('left', s, 500, BOX, SIZE, 60, 16)
  assert.equal(left.x, 0)
  assert.equal(left.width, 16)
  assert.equal(left.height, 60)
  assert.ok(left.y > 500 + BOX.top && left.y + left.height < 500 + BOX.bottom)
  const right = edge.handsPlace('right', s, 500, BOX, SIZE, 60, 16)
  assert.equal(right.x + right.width, s.x + s.width)
  const top = edge.handsPlace('top', s, 800, BOX, SIZE, 60, 16)
  assert.equal(top.y, 0)
  assert.equal(top.x + top.width / 2, 800 + (BOX.left + BOX.right) / 2)
  // At the very bottom of the screen the hands still fit on it.
  const low = edge.handsPlace('left', s, 5000, BOX, SIZE, 60, 16)
  assert.ok(low.y + low.height <= s.y + s.height)
})

test('coming out she stands just inside the edge', () => {
  const s = ONE[0]
  const ground = s.height - 190
  const left = edge.comeOut('left', s, 500, BOX, SIZE, ground)
  assert.ok(left.x + BOX.left >= s.x && left.x + BOX.left <= s.x + 20)
  assert.equal(left.y, 500)
  const right = edge.comeOut('right', s, 500, BOX, SIZE, ground)
  assert.ok(right.x + BOX.right <= s.x + s.width && right.x + BOX.right >= s.x + s.width - 20)
  const top = edge.comeOut('top', s, 800, BOX, SIZE, ground)
  assert.ok(top.y + BOX.top >= s.y && top.y + BOX.top <= s.y + 20)
  assert.equal(top.x, 800)
})

test('藏到屏幕边上: the nearer side edge without a screen behind it', () => {
  assert.equal(edge.nearestSide(at(300, 500), ONE), 'left')
  assert.equal(edge.nearestSide(at(1500, 500), ONE), 'right')
  const two = [
    { x: 0, y: 0, width: 1920, height: 1040 },
    { x: 1920, y: 0, width: 1920, height: 1040 },
  ]
  // Near the right of the left screen, but the right screen is there: the left edge instead.
  assert.equal(edge.nearestSide(at(1700, 500), two), 'left')
})

test('the overlay spans screens of one scale; mixed scales keep to the primary', () => {
  const primary = { id: 1, scaleFactor: 1.25, workArea: { x: 0, y: 0, width: 2048, height: 1104 } }
  const other = { id: 2, scaleFactor: 1.25, workArea: { x: -1920, y: 100, width: 1920, height: 1040 } }
  const both = overlayArea([other, primary], primary)
  assert.deepEqual(both.bounds, { x: -1920, y: 0, width: 3968, height: 1140 })
  // Page coordinates, the primary first.
  assert.deepEqual(both.screens, [
    { x: 1920, y: 0, width: 2048, height: 1104 },
    { x: 0, y: 100, width: 1920, height: 1040 },
  ])
  const mixed = overlayArea([other, { ...primary }].map((d) => d), { ...primary, scaleFactor: 1.5 })
  assert.deepEqual(mixed.bounds, { x: 0, y: 0, width: 2048, height: 1104 })
  assert.equal(mixed.screens.length, 1)
  const alone = overlayArea([primary], primary)
  assert.deepEqual(alone.screens, [{ x: 0, y: 0, width: 2048, height: 1104 }])
})

test('her right-click menu: put her away, 总在最前, open the chat; out from the edge when hidden', () => {
  const calls = []
  const act = new Proxy({}, { get: (_t, name) => (...args) => calls.push([name, ...args]) })
  const base = {
    name: '阿鲸',
    onTop: true,
    skins: [
      { id: 'default', label: '原版', chosen: true },
      { id: 'pixel', label: '像素', chosen: false },
    ],
    accessories: [{ id: '王冠', label: '王冠', on: false }],
    do: act,
  }
  const out = petMenu({ ...base, docked: false })
  const labels = out.map((i) => i.label).filter(Boolean)
  assert.ok(labels.includes('打开和她的聊天'))
  assert.ok(labels.includes('戳她一下'))
  assert.ok(!labels.includes('出来吧'))
  const onTop = out.find((i) => i.label === '总在最前')
  assert.equal(onTop.type, 'checkbox')
  assert.equal(onTop.checked, true)
  onTop.click({ checked: false })
  out.find((i) => i.label?.startsWith('收起阿鲸')).click()
  out.find((i) => i.label === '打开和她的聊天').click()
  out.find((i) => i.label === '皮肤').submenu[1].click()
  out.find((i) => i.label === '配饰').submenu[0].click({ checked: true })
  assert.deepEqual(calls, [
    ['setOnTop', false],
    ['putAway'],
    ['openChat'],
    ['setSkin', 'pixel'],
    ['setAccessory', '王冠', true],
  ])
  assert.ok(out.find((i) => i.label?.startsWith('收起阿鲸')).label.includes('托盘'))

  const hidden = petMenu({ ...base, docked: true, skins: [base.skins[0]], accessories: [] })
  const hiddenLabels = hidden.map((i) => i.label).filter(Boolean)
  assert.equal(hiddenLabels[1], '出来吧')
  assert.ok(!hiddenLabels.includes('戳她一下'))
  assert.ok(!hiddenLabels.includes('皮肤')) // one skin: nothing to choose
  assert.ok(!hiddenLabels.includes('配饰'))
})

test('her hands go with her: a skin of her own style without hands borrows hers; another style gets none', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-pets-'))
  const pack = (dir, rendering, hands) => {
    fs.mkdirSync(dir, { recursive: true })
    const meta = { name: path.basename(dir) === '阿星' ? '阿星' : '阿鲸', rendering, frame: 96, actions: { idle: { file: 'idle.png', frames: 1 } } }
    fs.writeFileSync(path.join(dir, 'pet.json'), JSON.stringify(meta))
    fs.writeFileSync(path.join(dir, 'idle.png'), 'idle')
    if (hands) fs.writeFileSync(path.join(dir, 'hands.png'), hands)
  }
  const her = path.join(root, '阿鲸')
  pack(her, 'smooth', 'her-hands')
  pack(path.join(her, 'skins', 'summer'), 'smooth', null) // same style, none of its own
  pack(path.join(her, 'skins', 'mitts'), 'smooth', 'mitts') // its own
  pack(path.join(her, 'skins', 'pixel'), 'pixel', null) // another style
  pack(path.join(root, '阿星'), 'smooth', null) // nobody has drawn hers
  const packs = loadPacks(root)
  const hands = (name, id) => {
    const skin = packs.get(name).skins.find((s) => s.id === id)
    return skin.hands && Buffer.from(skin.hands.split(',')[1], 'base64').toString()
  }
  assert.equal(hands('阿鲸', 'default'), 'her-hands')
  assert.equal(hands('阿鲸', 'summer'), 'her-hands')
  assert.equal(hands('阿鲸', 'mitts'), 'mitts')
  assert.equal(hands('阿鲸', 'pixel'), null)
  assert.equal(hands('阿星', 'default'), null)
  fs.rmSync(root, { recursive: true, force: true })
})

test('an accessory can bring its own hands (cat ears with cat paws); one without brings none', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-pets-'))
  const her = path.join(root, '阿鲸')
  fs.mkdirSync(her)
  const meta = { name: '阿鲸', rendering: 'smooth', frame: 96, actions: { idle: { file: 'idle.png', frames: 1 } } }
  fs.writeFileSync(path.join(her, 'pet.json'), JSON.stringify(meta))
  fs.writeFileSync(path.join(her, 'idle.png'), 'idle')
  const accessory = (id, label, withHands) => {
    const dir = path.join(her, 'accessories', id)
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, 'acc.json'), JSON.stringify({ label, rendering: 'smooth' }))
    fs.writeFileSync(path.join(dir, 'item.png'), 'item')
    if (withHands) fs.writeFileSync(path.join(dir, 'hands.png'), 'paws')
  }
  accessory('catears', '猫耳', true)
  accessory('crown', '王冠', false)
  const { accessories } = loadPacks(root).get('阿鲸')
  const decode = (url) => url && Buffer.from(url.split(',')[1], 'base64').toString()
  assert.equal(decode(accessories.find((a) => a.label === '猫耳').hands), 'paws')
  assert.equal(accessories.find((a) => a.label === '王冠').hands, null)
  fs.rmSync(root, { recursive: true, force: true })
})
