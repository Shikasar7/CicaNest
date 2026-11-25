'use strict'

// 外观 (src/theme.js): what the console sends is checked, kept, and laid over the app's own pages.
// node --test test/

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { Theme, normalize, cssFor, isOwnPage } = require('../src/theme')

const LOOK = {
  accent: '#2a6cc0',
  accent_text: '#ffffff',
  background: '#f2f4f5',
  surface: '#f8f9fa',
  card: '#ffffff',
  text: '#17202a',
  muted: '#5e6b76',
  border: '#e1e6ea',
}
const DARK = { ...LOOK, accent: '#6aaef2', accent_text: '#11161a', background: '#121619', text: '#e8edf1' }

function fakeNativeTheme() {
  return { themeSource: 'system' }
}

test('only light, dark or system, and only whole palettes of #rrggbb colours', () => {
  assert.deepEqual(normalize(null), { mode: 'system', colors: null })
  assert.deepEqual(normalize({ mode: 'sepia' }), { mode: 'system', colors: null })
  assert.equal(normalize({ mode: 'dark', colors: { light: LOOK, dark: DARK } }).colors.dark.accent, '#6aaef2')
  // Anything that is not a plain colour is dropped: the palette is not used at all.
  const sneaky = { ...LOOK, text: 'red;} body{display:none' }
  assert.equal(normalize({ mode: 'light', colors: { light: sneaky, dark: DARK } }).colors, null)
  assert.equal(normalize({ mode: 'light', colors: { light: LOOK } }).colors, null)
  // Colours it does not use are not kept.
  const extra = normalize({ colors: { light: { ...LOOK, fill: '#000000' }, dark: DARK } })
  assert.equal(extra.colors.light.fill, undefined)
})

test('the colours become base.css variables for both looks', () => {
  assert.equal(cssFor(null), '')
  const css = cssFor({ light: LOOK, dark: DARK })
  assert.match(css, /^:root:root\{--bg:#f2f4f5;/)
  assert.match(css, /--accent:#2a6cc0;--accent-text:#ffffff\}/)
  assert.match(css, /@media \(prefers-color-scheme: dark\)\{:root:root\{--bg:#121619;.*--accent:#6aaef2/)
})

test('only our own pages get the CSS (the console has its own)', () => {
  assert.ok(isOwnPage('file:///D:/nest/desktop/src/ui/splash.html'))
  assert.ok(!isOwnPage('http://127.0.0.1:18800/'))
  assert.ok(!isOwnPage(''))
})

test('kept for the next start, and light/dark goes to the whole app', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-theme-'))
  const file = path.join(dir, 'theme.json')
  const native = fakeNativeTheme()
  const theme = new Theme({ file, nativeTheme: native, log: { warn() {} } })
  theme.refreshAll = () => {} // no windows here
  assert.deepEqual(theme.load(), { mode: 'system', colors: null })

  assert.equal(theme.set({ mode: 'dark', colors: { light: LOOK, dark: DARK } }), true)
  assert.equal(native.themeSource, 'dark')
  assert.equal(theme.set({ mode: 'dark', colors: { light: LOOK, dark: DARK } }), false) // nothing new

  const again = new Theme({ file, nativeTheme: fakeNativeTheme(), log: { warn() {} } })
  assert.equal(again.load().colors.light.accent, '#2a6cc0')
  assert.equal(again.nativeTheme.themeSource, 'dark')

  fs.writeFileSync(file, 'not json')
  assert.deepEqual(new Theme({ file, nativeTheme: fakeNativeTheme() }).load(), { mode: 'system', colors: null })
  fs.rmSync(dir, { recursive: true, force: true })
})

test('a page gets the CSS when it loads, and new colours replace the old', async () => {
  const theme = new Theme({ file: path.join(os.tmpdir(), 'unused.json'), nativeTheme: fakeNativeTheme() })
  theme.current = normalize({ mode: 'light', colors: { light: LOOK, dark: DARK } })
  const calls = []
  let ready
  const page = {
    url: 'file:///ui/offline.html',
    on: (event, fn) => event === 'dom-ready' && (ready = fn),
    isDestroyed: () => false,
    getURL: () => page.url,
    insertCSS: async (css) => (calls.push(['insert', css]), `key${calls.length}`),
    removeInsertedCSS: async (key) => calls.push(['remove', key]),
  }
  theme.attach(page)
  ready()
  await new Promise((r) => setImmediate(r))
  assert.equal(calls.length, 1)
  assert.match(calls[0][1], /--accent:#2a6cc0/)

  theme.current = normalize({ mode: 'light', colors: null }) // back to nest's own look
  await theme.refresh(page)
  assert.deepEqual(calls[1], ['remove', 'key1'])
  assert.equal(calls.length, 2)

  page.url = 'http://127.0.0.1:18800/'
  theme.current = normalize({ mode: 'light', colors: { light: LOOK, dark: DARK } })
  await theme.refresh(page)
  assert.equal(calls.length, 2) // the console draws its own colours
})
