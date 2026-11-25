'use strict'

// The confirm window for a step on this PC: a window of the main process, on
// top, showing what the step really does (the action and the exact path), never a page from nest. Answers:
// once / for the rest of this answer or task (same tool, same folder) / no. Ten minutes without an answer,
// or the window closed, is no.
//
// One window at a time: the others wait their turn, so a
// window never opens on top of the one the user is about to click; its buttons wake up a moment after it
// shows. More than QUEUE_MAX waiting: the newest is answered 'busy' without a window.

const path = require('node:path')
const { BrowserWindow, ipcMain } = require('electron')
const log = require('../log')

const WAIT_MS = 10 * 60_000
const QUEUE_MAX = 5
const ANSWERS = new Set(['once', 'kind', 'deny'])

class Confirmations {
  /** `debug`: the debug harness is on (it can answer these windows): each window says so. */
  constructor({ icon, debug = false }) {
    this.icon = icon
    this.debug = debug
    this.open = new Map() // webContents id -> { data, settle, win } (one at most)
    this.queue = [] // [{ data, resolve }] waiting for their window
    ipcMain.handle('confirm:data', (event) => {
      const entry = this.open.get(event.sender.id)
      if (!entry) throw new Error('not allowed')
      return entry.data
    })
    ipcMain.handle('confirm:answer', (event, answer) => {
      const entry = this.open.get(event.sender.id)
      if (!entry || !ANSWERS.has(answer)) throw new Error('not allowed')
      entry.settle(answer)
    })
  }

  /**
   * data: { botName, action, target, detail, detailKind, why, kindLabel }. Resolves 'once' | 'kind' |
   * 'deny' | 'busy'.
   */
  ask(data) {
    if (this.queue.length >= QUEUE_MAX) return Promise.resolve('busy')
    return new Promise((resolve) => {
      this.queue.push({ data, resolve })
      this.next()
    })
  }

  /** Every window and every one waiting: answered no (电脑上做事 switched off). */
  closeAll() {
    for (const waiting of this.queue.splice(0)) waiting.resolve('deny')
    for (const entry of [...this.open.values()]) entry.settle('deny')
  }

  next() {
    if (this.open.size || !this.queue.length) return
    const { data, resolve } = this.queue.shift()
    this.show(data, this.queue.length).then((answer) => {
      resolve(answer)
      this.next()
    })
  }

  show(data, waiting) {
    return new Promise((resolve) => {
      const big = Boolean(data.detail)
      const win = new BrowserWindow({
        width: big ? 760 : 520,
        height: big ? 640 : 400,
        resizable: big,
        maximizable: false,
        minimizable: false,
        alwaysOnTop: true,
        skipTaskbar: false,
        show: false,
        title: `${data.botName} 想帮你做一件事`,
        icon: this.icon,
        autoHideMenuBar: true,
        backgroundColor: '#f2f4f5',
        webPreferences: {
          preload: path.join(__dirname, 'confirm-preload.js'),
          contextIsolation: true,
          sandbox: true,
          nodeIntegration: false,
          spellcheck: false,
        },
      })
      const id = win.webContents.id
      let done = false
      const settle = (answer) => {
        if (done) return
        done = true
        clearTimeout(timer)
        this.open.delete(id)
        log.info(`executor: ${data.action} answered ${answer}`)
        if (!win.isDestroyed()) win.destroy()
        resolve(answer)
      }
      const timer = setTimeout(() => settle('deny'), WAIT_MS)
      this.open.set(id, {
        data: { ...data, waitMinutes: WAIT_MS / 60_000, waiting, debug: this.debug },
        settle,
        win,
      })
      win.setMenu(null)
      win.webContents.on('will-navigate', (event) => event.preventDefault())
      win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
      win.on('closed', () => settle('deny'))
      win.once('ready-to-show', () => {
        win.show()
        win.flashFrame(true)
      })
      win.loadFile(path.join(__dirname, '..', 'ui', 'confirm.html'))
    })
  }
}

/** The debug harness's view (debug.js only): the window waiting now, and answering it. */
Confirmations.prototype.oldest = function oldest() {
  return this.open.values().next().value ?? null
}

module.exports = { Confirmations, WAIT_MS, QUEUE_MAX }
