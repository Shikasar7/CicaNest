'use strict'

// 外观: the app's own windows (starting, offline, connection settings, 电脑上做事, 桌宠) and the title bar
// buttons look like the console. The console tells us (window.nestDesktop.setTheme) light, dark or the
// system's, and the colours of the theme picked in nest; we keep them in <profile>\theme.json so the next
// start looks right before nest answers.
//
// Light or dark goes to nativeTheme.themeSource, which every window (the console's too) and the title bar
// buttons follow. The colours are laid over base.css in our own pages (file: URLs) with insertCSS.

const fs = require('node:fs')
const path = require('node:path')

const HEX = /^#[0-9a-f]{6}$/
const MODES = ['system', 'light', 'dark']
// The colours of a look we use (nest.core.themes names them).
const SLOTS = ['accent', 'accent_text', 'background', 'surface', 'card', 'text', 'muted', 'border']

function goodColours(colours) {
  return Boolean(colours) && typeof colours === 'object' && SLOTS.every((slot) => HEX.test(String(colours[slot] ?? '')))
}

/** What the console sent (or theme.json holds), checked: { mode, colors: { light, dark } | null }. */
function normalize(input) {
  const mode = MODES.includes(input?.mode) ? input.mode : 'system'
  const given = input?.colors
  const colors =
    given && goodColours(given.light) && goodColours(given.dark)
      ? {
          light: Object.fromEntries(SLOTS.map((s) => [s, given.light[s]])),
          dark: Object.fromEntries(SLOTS.map((s) => [s, given.dark[s]])),
        }
      : null
  return { mode, colors }
}

/** base.css's variables for both looks; '' for nest's own look (base.css as it is). */
function cssFor(colors) {
  if (!colors) return ''
  const block = (c, dark) =>
    [
      `--bg:${c.background}`,
      `--surface:${c.card}`,
      `--text:${c.text}`,
      `--muted:${c.muted}`,
      `--faint:color-mix(in srgb,${c.muted} 60%,${c.background})`,
      `--line:color-mix(in srgb,${c.text} 8%,transparent)`,
      `--line-strong:color-mix(in srgb,${c.text} 16%,transparent)`,
      `--hover:color-mix(in srgb,${c.text} ${dark ? 6 : 5}%,transparent)`,
      `--edge:color-mix(in srgb,${c.text} ${dark ? 8 : 7}%,transparent)`,
      `--accent:${c.accent}`,
      `--accent-text:${c.accent_text}`,
    ].join(';')
  // :root:root outranks base.css's :root whichever sheet comes last.
  return `:root:root{${block(colors.light, false)}}@media (prefers-color-scheme: dark){:root:root{${block(colors.dark, true)}}}`
}

function isOwnPage(url) {
  return String(url || '').startsWith('file:')
}

class Theme {
  constructor({ file, nativeTheme = null, log = console }) {
    this.file = file
    this.nativeTheme = nativeTheme || require('electron').nativeTheme
    this.log = log
    this.current = normalize(null)
    this.inserted = new WeakMap() // webContents -> the key of the CSS we inserted
  }

  /** Read theme.json and apply light/dark at once (before any window is drawn). */
  load() {
    try {
      this.current = normalize(JSON.parse(fs.readFileSync(this.file, 'utf8')))
    } catch {
      this.current = normalize(null)
    }
    this.nativeTheme.themeSource = this.current.mode
    return this.current
  }

  /** From the console. Returns whether anything changed. */
  set(input) {
    const next = normalize(input)
    if (JSON.stringify(next) === JSON.stringify(this.current)) return false
    this.current = next
    this.save()
    if (this.nativeTheme.themeSource !== next.mode) this.nativeTheme.themeSource = next.mode
    this.refreshAll()
    return true
  }

  save() {
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true })
      const temp = `${this.file}.tmp`
      fs.writeFileSync(temp, JSON.stringify(this.current, null, 2), 'utf8')
      fs.renameSync(temp, this.file)
    } catch (err) {
      this.log.warn('could not save theme.json:', err.message)
    }
  }

  /** Every page loaded in `contents` gets the colours (app.on('web-contents-created')). */
  attach(contents) {
    contents.on('dom-ready', () => {
      this.inserted.delete(contents) // a new page: what was inserted went with the old one
      this.refresh(contents)
    })
  }

  async refresh(contents) {
    if (contents.isDestroyed() || !isOwnPage(contents.getURL())) return
    const old = this.inserted.get(contents)
    this.inserted.delete(contents)
    try {
      if (old) await contents.removeInsertedCSS(old)
      const css = cssFor(this.current.colors)
      if (css && !contents.isDestroyed()) this.inserted.set(contents, await contents.insertCSS(css))
    } catch (err) {
      this.log.warn('could not apply the theme:', err.message)
    }
  }

  refreshAll() {
    const { webContents } = require('electron')
    for (const contents of webContents.getAllWebContents()) void this.refresh(contents)
  }
}

module.exports = { Theme, normalize, cssFor, isOwnPage }
