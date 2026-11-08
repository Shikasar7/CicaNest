'use strict'

// The tray icon: status at a glance (ok / starting / attention / down), polled every 10 s,
// and the menu that keeps working while the window is closed.

const path = require('node:path')
const { Tray, Menu, nativeImage } = require('electron')

const POLL_MS = 10_000
const ICON_DIR = path.join(__dirname, '..', 'assets', 'icons')
const KINDS = ['ok', 'starting', 'attention', 'down']

const isOnline = (account) => account?.status?.state === 'connected'

function summarize(accounts) {
  const total = accounts.length
  const online = accounts.filter(isOnline).length
  if (total === 0) return { kind: 'attention', summary: '还没有添加账号' }
  if (online === total) return { kind: 'ok', summary: `${total} 个号在线` }
  if (online === 0) return { kind: 'attention', summary: total === 1 ? '号没上线' : `${total} 个号都没上线` }
  return { kind: 'attention', summary: `${online} 个号在线，${total - online} 个没上线` }
}

class TrayController {
  /**
   * @param {{ nest: import('./nest').Nest, actions: Record<string, Function>, onStatus?: Function }} options
   */
  constructor({ nest, name = 'nest', actions, onStatus }) {
    this.nest = nest
    this.name = name
    this.actions = actions
    this.onStatus = onStatus
    this.tray = null
    this.menu = null
    this.busy = null // e.g. '正在启动…' while a launch or restart runs
    this.status = null
    this.shownKind = null
    this.shownTip = null
    this.menuKey = null
    this.timer = null
    this.refreshing = null
  }

  start() {
    this.icons = Object.fromEntries(
      KINDS.map((kind) => [kind, nativeImage.createFromPath(path.join(ICON_DIR, `tray-${kind}.ico`))]),
    )
    this.tray = new Tray(this.icons.starting)
    this.tray.on('click', () => this.actions.open())
    this.render()
    this.timer = setInterval(() => this.refresh(), POLL_MS)
  }

  setBusy(text) {
    if ((text || null) === this.busy) return
    this.busy = text || null
    this.render()
    if (!text) this.refresh()
  }

  // One poll at a time; callers may await the current one.
  refresh() {
    if (!this.refreshing) {
      this.refreshing = this.poll().finally(() => {
        this.refreshing = null
        this.render()
      })
    }
    return this.refreshing
  }

  async poll() {
    const health = await this.nest.health()
    let status
    if (!health) {
      status = { kind: 'down', summary: '后台没有运行' }
    } else {
      let accounts = null
      try {
        const res = await this.nest.api('GET', '/api/accounts')
        if (res.status === 200 && Array.isArray(res.json)) accounts = res.json.filter((a) => a.enabled !== false)
      } catch {
        accounts = null
      }
      status = accounts ? summarize(accounts) : { kind: 'attention', summary: '读不到账号状态' }
    }
    this.status = status
    this.onStatus?.(status)
    return status
  }

  view() {
    if (this.busy) return { kind: 'starting', summary: this.busy }
    return this.status || { kind: 'starting', summary: '正在启动…' }
  }

  render(force = false) {
    if (!this.tray || this.tray.isDestroyed()) return
    const { kind, summary } = this.view()
    if (kind !== this.shownKind) {
      this.tray.setImage(this.icons[kind])
      this.shownKind = kind
    }
    const tip = `${this.name}：${summary}`
    if (tip !== this.shownTip) {
      this.tray.setToolTip(tip)
      this.shownTip = tip
    }
    const autostart = this.actions.getAutostart()
    const down = kind === 'down'
    const busy = Boolean(this.busy)
    const key = JSON.stringify([summary, down, busy, autostart])
    if (!force && key === this.menuKey) return
    this.menuKey = key
    this.menu = Menu.buildFromTemplate([
      { label: `${this.name} · ${summary}`, enabled: false },
      { type: 'separator' },
      { label: '打开 nest', click: () => this.actions.open() },
      { type: 'separator' },
      { label: '全部暂停…', enabled: !down && !busy, click: () => this.actions.pauseAll() },
      down
        ? { label: '启动 nest', enabled: !busy, click: () => this.actions.start() }
        : { label: '重启 nest', enabled: !busy, click: () => this.actions.restart() },
      { type: 'separator' },
      {
        label: '开机时自动启动',
        type: 'checkbox',
        checked: autostart,
        click: (item) => this.actions.setAutostart(item.checked),
      },
      { label: '打开日志文件夹', click: () => this.actions.openLogs() },
      { type: 'separator' },
      { label: '退出（bot 继续在后台运行）', click: () => this.actions.quit() },
      { label: '退出并停止 nest…', enabled: !down && !busy, click: () => this.actions.quitAndStop() },
    ])
    this.tray.setContextMenu(this.menu)
  }

  // For the debug harness: press a menu item by the start of its label.
  clickItem(prefix) {
    const item = this.menu?.items.find((i) => i.label && i.label.startsWith(prefix))
    if (!item) return false
    if (!item.enabled) return 'disabled'
    item.click()
    return true
  }

  balloon(options) {
    if (this.tray && !this.tray.isDestroyed()) this.tray.displayBalloon(options)
  }

  destroy() {
    clearInterval(this.timer)
    if (this.tray && !this.tray.isDestroyed()) this.tray.destroy()
    this.tray = null
  }
}

module.exports = { TrayController, summarize }
