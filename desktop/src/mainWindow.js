'use strict'

// The console window: hidden title bar with the system caption buttons drawn over the page,
// Mica behind it, size/position remembered, navigation kept on the nest origin.

const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const { BrowserWindow, nativeTheme, screen, shell } = require('electron')
const log = require('./log')

const TITLE_BAR_HEIGHT = 40
const DEFAULT_SIZE = { width: 1240, height: 820 }
const MIN_SIZE = { width: 960, height: 640 }
const OFFLINE_PAGE = path.join(__dirname, 'ui', 'offline.html')
const OFFLINE_URL = pathToFileURL(OFFLINE_PAGE).href

function overlayOptions() {
  return {
    color: '#00000000',
    symbolColor: nativeTheme.shouldUseDarkColors ? '#E7EBEE' : '#17202A',
    height: TITLE_BAR_HEIGHT,
  }
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

function writeJson(file, value) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true })
    const temp = `${file}.tmp`
    fs.writeFileSync(temp, JSON.stringify(value, null, 2), 'utf8')
    fs.renameSync(temp, file)
  } catch (err) {
    log.warn(`could not save ${path.basename(file)}:`, err.message)
  }
}

// Saved bounds are used only if they still land on a connected screen.
function restoredBounds(saved) {
  if (!saved || !Number.isFinite(saved.width) || !Number.isFinite(saved.height)) return null
  const width = Math.max(MIN_SIZE.width, Math.round(saved.width))
  const height = Math.max(MIN_SIZE.height, Math.round(saved.height))
  if (!Number.isFinite(saved.x) || !Number.isFinite(saved.y)) return { width, height }
  const bounds = { x: Math.round(saved.x), y: Math.round(saved.y), width, height }
  const area = screen.getDisplayMatching(bounds).workArea
  const visibleX = Math.min(bounds.x + width, area.x + area.width) - Math.max(bounds.x, area.x)
  const visibleY = Math.min(bounds.y + height, area.y + area.height) - Math.max(bounds.y, area.y)
  return visibleX >= 160 && visibleY >= 80 ? bounds : { width, height }
}

class MainWindow {
  constructor({ config, title = 'nest', icon, version, stateFile, isQuitting, onHiddenToTray }) {
    this.config = config
    this.title = title
    this.icon = icon
    this.version = version
    this.stateFile = stateFile
    this.isQuitting = isQuitting
    this.onHiddenToTray = onHiddenToTray
    this.win = null
    this.ready = false
    this.offline = false
    this.saveTimer = null
    nativeTheme.on('updated', () => {
      if (this.alive()) this.win.setTitleBarOverlay(overlayOptions())
    })
  }

  alive() {
    return Boolean(this.win && !this.win.isDestroyed())
  }

  isNestUrl(url) {
    try {
      return new URL(url).origin === this.config.origin
    } catch {
      return false
    }
  }

  // The console, or our own offline page: the only pages allowed to call window.nestDesktop.
  isTrustedPage(url) {
    return this.isNestUrl(url) || String(url).split('#')[0] === OFFLINE_URL
  }

  // Create (hidden) and start loading the console, if not already.
  ensure() {
    if (this.alive()) return this.win
    const saved = readJson(this.stateFile) || {}
    const bounds = restoredBounds(saved) || DEFAULT_SIZE
    this.savedBounds = saved.width ? { x: saved.x, y: saved.y, width: saved.width, height: saved.height } : null
    this.ready = false
    const win = new BrowserWindow({
      ...DEFAULT_SIZE,
      ...bounds,
      center: bounds.x === undefined,
      minWidth: MIN_SIZE.width,
      minHeight: MIN_SIZE.height,
      show: false,
      title: this.title,
      icon: this.icon,
      titleBarStyle: 'hidden',
      titleBarOverlay: overlayOptions(),
      backgroundMaterial: 'mica',
      backgroundColor: '#00000000',
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        spellcheck: false,
        additionalArguments: [
          `--nest-desktop-version=${this.version}`,
          `--nest-test-copy=${this.config.isTestCopy ? '1' : '0'}`,
        ],
      },
    })
    this.win = win
    // maximize() would also show the window; do it when it is shown.
    this.pendingMaximize = Boolean(saved.maximized)
    win.setMenu(null)

    win.once('ready-to-show', () => {
      this.ready = true
      win.emit('nest-ready')
    })

    // The taskbar title stays ours (it carries 「测试副本」 for a test copy).
    win.on('page-title-updated', (event) => event.preventDefault())

    const contents = win.webContents
    contents.on('will-navigate', (event, url) => {
      if (this.isNestUrl(url)) return
      event.preventDefault()
      this.openExternal(url)
    })
    contents.on('will-redirect', (event, url) => {
      if (!this.isNestUrl(url)) event.preventDefault()
    })
    contents.setWindowOpenHandler(({ url }) => {
      if (this.isNestUrl(url)) {
        contents.loadURL(url)
      } else {
        this.openExternal(url)
      }
      return { action: 'deny' }
    })
    contents.on('will-attach-webview', (event) => event.preventDefault())
    contents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
      // -3 is ERR_ABORTED: a navigation replaced by another one, not a failure.
      if (!isMainFrame || code === -3 || !this.isNestUrl(url)) return
      log.warn(`console failed to load (${code} ${description})`)
      this.showOffline()
    })
    contents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return
      const key = input.key.toLowerCase()
      if (key === 'f5' || (input.control && key === 'r')) {
        event.preventDefault()
        this.loadConsole()
      } else if (key === 'f12' || (input.control && input.shift && key === 'i')) {
        event.preventDefault()
        contents.toggleDevTools()
      }
    })

    // 'resized'/'moved' fire once a user drag ends; programmatic sizing never changes what is saved.
    for (const name of ['resized', 'moved']) {
      win.on(name, () => {
        if (win.isMaximized() || win.isMinimized() || win.isFullScreen()) return
        this.savedBounds = win.getNormalBounds()
        this.scheduleSave()
      })
    }
    for (const name of ['maximize', 'unmaximize']) win.on(name, () => this.scheduleSave())
    win.on('close', (event) => {
      this.save()
      if (this.isQuitting()) return
      event.preventDefault()
      win.hide()
      this.onHiddenToTray()
    })
    win.on('closed', () => {
      this.win = null
      this.ready = false
    })

    this.loadConsole()
    return win
  }

  loadConsole() {
    if (!this.alive()) return
    this.offline = false
    this.win.loadURL(`${this.config.origin}/`).catch(() => {
      // did-fail-load shows the offline page
    })
  }

  showOffline() {
    if (!this.alive()) return
    this.offline = true
    this.win.loadFile(OFFLINE_PAGE).catch(() => {})
  }

  openExternal(url) {
    try {
      const { protocol } = new URL(url)
      if (protocol === 'http:' || protocol === 'https:' || protocol === 'mailto:') shell.openExternal(url)
    } catch {
      // not a URL
    }
  }

  async show() {
    const win = this.ensure()
    if (!this.ready) {
      // Wait for the first paint (no white flash), but never hang on it.
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, 4000)
        win.once('nest-ready', () => {
          clearTimeout(timer)
          resolve()
        })
      })
    }
    if (win.isDestroyed()) return
    if (win.isMinimized()) win.restore()
    if (this.pendingMaximize) {
      this.pendingMaximize = false
      win.maximize()
    }
    win.show()
    win.focus()
  }

  hide() {
    if (this.alive()) this.win.hide()
  }

  isVisible() {
    return this.alive() && this.win.isVisible()
  }

  reload() {
    if (this.alive()) this.loadConsole()
  }

  scheduleSave() {
    clearTimeout(this.saveTimer)
    this.saveTimer = setTimeout(() => this.save(), 600)
  }

  save() {
    clearTimeout(this.saveTimer)
    if (!this.alive()) return
    const win = this.win
    writeJson(this.stateFile, { ...(this.savedBounds || {}), maximized: win.isMaximized() })
  }

  get webContents() {
    return this.alive() ? this.win.webContents : null
  }
}

module.exports = { MainWindow, TITLE_BAR_HEIGHT }
