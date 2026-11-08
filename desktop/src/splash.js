'use strict'

// The splash window: frameless, transparent, a 460-wide card with a soft shadow, drawn by ui/splash.*.
// It mirrors a Launch (launcher.js); it can be hidden while the launch keeps going in the background.

const path = require('node:path')
const { BrowserWindow, ipcMain, screen } = require('electron')
const log = require('./log')

const CARD_WIDTH = 460
const CARD_HEIGHT_4_STEPS = 336
const STEP_HEIGHT = 30
const SHADOW_MARGIN = 28
const LEAVE_MS = 220

class Splash {
  constructor({ icon, title = 'nest', footnote = '', footnoteTitle = '', testCopy = false, onAction }) {
    this.icon = icon
    this.title = title
    this.footnote = footnote
    this.footnoteTitle = footnoteTitle
    this.testCopy = testCopy
    this.onAction = onAction
    this.win = null
    this.launch = null
    this.state = null
    this.rendererReady = false
    this.wantVisible = false
    this.entered = false
    this.unsubscribe = null

    ipcMain.on('splash:ready', (event) => {
      if (!this.owns(event)) return
      this.rendererReady = true
      this.push()
      if (this.wantVisible) this.reveal()
    })
    ipcMain.on('splash:action', (event, id) => {
      if (!this.owns(event) || typeof id !== 'string') return
      this.onAction(id, this.launch)
    })
  }

  owns(event) {
    return Boolean(this.win && !this.win.isDestroyed() && event.sender === this.win.webContents)
  }

  attach(launch, { visible }) {
    this.unsubscribe?.()
    this.launch = launch
    this.state = launch.snapshot()
    const onChange = (state) => {
      this.state = state
      this.push()
    }
    launch.on('change', onChange)
    this.unsubscribe = () => launch.off('change', onChange)
    this.create(launch.steps.length)
    if (visible) this.show()
  }

  create(stepCount) {
    if (this.win && !this.win.isDestroyed()) {
      this.push()
      return
    }
    const cardHeight = CARD_HEIGHT_4_STEPS + Math.max(0, stepCount - 4) * STEP_HEIGHT
    const width = CARD_WIDTH + SHADOW_MARGIN * 2
    const height = cardHeight + SHADOW_MARGIN * 2
    const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
    this.rendererReady = false
    this.entered = false
    this.win = new BrowserWindow({
      width,
      height,
      x: Math.round(area.x + (area.width - width) / 2),
      y: Math.round(area.y + (area.height - height) / 2 - 24),
      frame: false,
      transparent: true,
      backgroundColor: '#00000000',
      hasShadow: false,
      resizable: false,
      maximizable: false,
      minimizable: true,
      fullscreenable: false,
      alwaysOnTop: false,
      skipTaskbar: false,
      show: false,
      title: this.title,
      icon: this.icon,
      webPreferences: {
        preload: path.join(__dirname, 'splash-preload.js'),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        spellcheck: false,
      },
    })
    this.win.setMenu(null)
    this.win.on('page-title-updated', (event) => event.preventDefault())
    this.win.webContents.on('will-navigate', (event) => event.preventDefault())
    this.win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    this.win.on('closed', () => {
      this.win = null
      this.rendererReady = false
      this.entered = false
    })
    this.win.loadFile(path.join(__dirname, 'ui', 'splash.html'))
  }

  push() {
    if (!this.rendererReady || !this.win || this.win.isDestroyed() || !this.state) return
    this.win.webContents.send('splash:state', {
      ...this.state,
      footnote: this.footnote,
      footnoteTitle: this.footnoteTitle,
      testCopy: this.testCopy,
    })
  }

  reveal() {
    if (!this.win || this.win.isDestroyed()) return
    if (!this.win.isVisible()) this.win.show()
    this.win.focus()
    if (!this.entered) {
      this.entered = true
      this.win.webContents.send('splash:enter')
    }
  }

  show() {
    this.wantVisible = true
    if (!this.win || this.win.isDestroyed()) {
      if (this.launch && !this.launch.finished) this.create(this.launch.steps.length)
      else return
    }
    if (this.win.isMinimized()) this.win.restore()
    if (this.rendererReady) this.reveal()
  }

  hide() {
    this.wantVisible = false
    if (this.win && !this.win.isDestroyed()) this.win.hide()
  }

  isVisible() {
    return Boolean(this.win && !this.win.isDestroyed() && this.win.isVisible())
  }

  // Fade the card out, then close the window.
  async leave() {
    const win = this.win
    this.unsubscribe?.()
    this.unsubscribe = null
    this.wantVisible = false
    if (!win || win.isDestroyed()) return
    if (win.isVisible() && this.rendererReady) {
      win.webContents.send('splash:leave')
      await new Promise((resolve) => setTimeout(resolve, LEAVE_MS + 30))
    }
    if (!win.isDestroyed()) win.destroy()
    log.info('splash closed')
  }

  get webContents() {
    return this.win && !this.win.isDestroyed() ? this.win.webContents : null
  }
}

module.exports = { Splash }
