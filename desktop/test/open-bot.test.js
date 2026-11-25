'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const { OpenBot } = require('../src/open-bot')

const PRELOAD = fs.readFileSync(path.join(__dirname, '..', 'src', 'preload.js'), 'utf8')

/** preload.js of a window (`view`) against `wanted`, as main.js answers it. */
function page(wanted, view) {
  const heard = new Set()
  let bridge
  const ipcRenderer = {
    on: (channel, fn) => channel === 'nest:open-bot' && heard.add(fn),
    removeListener: (_channel, fn) => heard.delete(fn),
    invoke: async (channel, arg) => {
      if (channel === 'nest:wanted-bot') return wanted.id
      if (channel === 'nest:bot-shown') return wanted.shown(arg)
    },
  }
  const electron = { contextBridge: { exposeInMainWorld: (_name, api) => (bridge = api) }, ipcRenderer }
  new Function('require', 'process', PRELOAD)(() => electron, { argv: [`--nest-view=${view}`] })
  return { bridge, send: (id) => heard.forEach((fn) => fn({}, id)) }
}

const settled = () => new Promise((resolve) => setImmediate(resolve))

test('a bot asked for while the page is loading is shown once it is ready, and only once', async () => {
  const wanted = new OpenBot()
  const chat = page(wanted, 'chat')
  wanted.ask('desk:jing')
  chat.send('desk:jing') // nobody is listening yet
  const shown = []
  const stop = chat.bridge.onOpenBot((id) => shown.push(id))
  await settled()
  assert.deepEqual(shown, ['desk:jing'])
  stop()
  chat.bridge.onOpenBot((id) => shown.push(id)) // the page loads again: not hers again
  await settled()
  assert.deepEqual(shown, ['desk:jing'])
})

test('a page that is there hears it at once; the console and a plain open take nothing', async () => {
  const wanted = new OpenBot()
  const chat = page(wanted, 'chat')
  const shown = []
  chat.bridge.onOpenBot((id) => shown.push(id))
  await settled()
  wanted.ask('desk:ma')
  chat.send('desk:ma')
  assert.deepEqual(shown, ['desk:ma'])
  await settled()
  assert.equal(wanted.id, '')

  wanted.ask('desk:long')
  page(wanted, 'console').bridge.onOpenBot((id) => shown.push(id))
  await settled()
  assert.deepEqual(shown, ['desk:ma'])
  wanted.shown('desk:ma') // an answer about an earlier one
  assert.equal(wanted.id, 'desk:long')
  wanted.ask('')
  page(wanted, 'chat').bridge.onOpenBot((id) => shown.push(id))
  await settled()
  assert.deepEqual(shown, ['desk:ma'])
})
