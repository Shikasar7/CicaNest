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

// How the 电脑上做事 item reads in the menu (executor/index.js states).
const EXECUTOR_STATE = { off: '关着', connecting: '连接中', online: '已连上', another: '另一台电脑连着', login: '没登录' }

class TrayController {
  /**
   * @param {{ nest: import('./nest').Nest, actions: Record<string, Function>, onStatus?: Function }} options
   */
  constructor({ nest, name = 'nest', actions, onStatus, remote = false }) {
    this.nest = nest
    this.name = name
    // A nest elsewhere: this app neither starts nor stops it, and opens the chat on a click.
    this.remote = remote
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
    this.unread = 0 // her words in the chat app you have not looked at (unread.js)
  }

  setUnread(count) {
    if (count === this.unread) return
    this.unread = count
    this.render()
  }

  start() {
    this.icons = Object.fromEntries(
      KINDS.map((kind) => [kind, nativeImage.createFromPath(path.join(ICON_DIR, `tray-${kind}.ico`))]),
    )
    this.tray = new Tray(this.icons.starting)
    this.tray.on('click', () => this.actions.openChat())
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
      let signedOut = false
      try {
        const res = await this.nest.api('GET', '/api/accounts')
        if (res.status === 200 && Array.isArray(res.json)) accounts = res.json.filter((a) => a.enabled !== false)
      } catch (err) {
        accounts = null
        signedOut = err?.code === 'login'
      }
      if (signedOut) status = { kind: 'attention', summary: '还没登录' }
      else status = accounts ? summarize(accounts) : { kind: 'attention', summary: '读不到账号状态' }
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
    const { kind: state, summary } = this.view()
    // Unread words show as the attention icon unless something is more wrong.
    const kind = this.unread > 0 && state === 'ok' ? 'attention' : state
    if (kind !== this.shownKind) {
      this.tray.setImage(this.icons[kind])
      this.shownKind = kind
    }
    const tip = `${this.name}：${summary}${this.unread > 0 ? ` · ${this.unread} 条未读` : ''}`
    if (tip !== this.shownTip) {
      this.tray.setToolTip(tip)
      this.shownTip = tip
    }
    const autostart = this.actions.getAutostart()
    const down = kind === 'down'
    const busy = Boolean(this.busy)
    const pets = this.actions.pets?.() ?? { items: [], hidden: false, onTop: true }
    const executor = this.actions.executorState?.() ?? 'off'
    const key = JSON.stringify([summary, down, busy, autostart, pets, executor, this.unread])
    if (!force && key === this.menuKey) return
    this.menuKey = key
    const local = !this.remote
    this.menu = Menu.buildFromTemplate([
      { label: `${this.name} · ${summary}`, enabled: false },
      { type: 'separator' },
      {
        label: this.unread > 0 ? `和伙伴聊天（${this.unread} 条未读）` : '和伙伴聊天',
        click: () => this.actions.openChat(),
      },
      { label: '打开小窝', click: () => this.actions.openHome() },
      { label: '打开控制台', click: () => this.actions.open() },
      {
        label: '桌宠',
        submenu: [
          ...(pets.items.length
            ? pets.items.map((item) => ({
                label: item.placeholder ? `${item.name}（还没有她的图，用小人代替）` : item.name,
                submenu: [
                  {
                    label: '在桌面上',
                    type: 'checkbox',
                    checked: item.on,
                    enabled: !pets.hidden,
                    click: (menuItem) => this.actions.setPet(item.botId, menuItem.checked),
                  },
                  ...(item.skins.length ? [{ type: 'separator' }, { label: '皮肤', enabled: false }] : []),
                  ...item.skins.map((skin) => ({
                    label: skin.label,
                    type: 'radio',
                    checked: skin.chosen,
                    click: () => this.actions.setSkin(item.botId, skin.id),
                  })),
                  ...(item.accessories.length
                    ? [
                        { type: 'separator' },
                        { label: '配饰', enabled: false },
                        ...item.accessories.map((acc) => ({
                          label: acc.fits ? acc.label : `${acc.label}（这个皮肤的画风戴不了）`,
                          type: 'checkbox',
                          checked: acc.on,
                          enabled: acc.fits,
                          click: (menuItem) => this.actions.setAccessory(item.botId, acc.id, menuItem.checked),
                        })),
                      ]
                    : []),
                ],
              }))
            : [{ label: '还没有能放到桌面上的伙伴', enabled: false }]),
          { type: 'separator' },
          {
            label: '总在最前（浮在其他窗口上面）',
            type: 'checkbox',
            checked: pets.onTop !== false,
            click: (menuItem) => this.actions.setPetsOnTop(menuItem.checked),
          },
          {
            label: '看全屏（游戏、视频）时自动让开',
            type: 'checkbox',
            checked: pets.stepAside !== false,
            click: (menuItem) => this.actions.setPetsStepAside(menuItem.checked),
          },
          {
            label: '全部收起来',
            type: 'checkbox',
            checked: pets.hidden,
            click: (menuItem) => this.actions.hidePets(menuItem.checked),
          },
          {
            label: pets.quietUntil ? `安静到 ${pets.quietUntil}（提醒照常）` : '安静一会儿（她们不主动说话）',
            submenu: [
              { label: '一小时', click: () => this.actions.quietPets(60 * 60_000) },
              { label: '三小时', click: () => this.actions.quietPets(3 * 60 * 60_000) },
              { label: '到明天早上', click: () => this.actions.quietPets('tomorrow') },
              { type: 'separator' },
              { label: '不用安静了', enabled: Boolean(pets.quietUntil), click: () => this.actions.quietPets(0) },
            ],
          },
          { label: '重新读取素材', click: () => this.actions.reloadPets() },
          { label: '打开素材文件夹', click: () => this.actions.openPets() },
        ],
      },
      { type: 'separator' },
      ...(local
        ? [
            { label: '全部暂停…', enabled: !down && !busy, click: () => this.actions.pauseAll() },
            down
              ? { label: '启动 nest', enabled: !busy, click: () => this.actions.start() }
              : { label: '重启 nest', enabled: !busy, click: () => this.actions.restart() },
            { type: 'separator' },
          ]
        : []),
      {
        label: '开机时自动启动',
        type: 'checkbox',
        checked: autostart,
        click: (item) => this.actions.setAutostart(item.checked),
      },
      {
        label: `电脑上做事${EXECUTOR_STATE[executor] ? `（${EXECUTOR_STATE[executor]}）` : ''}…`,
        click: () => this.actions.openExecutor?.(),
      },
      { label: '连接设置…', click: () => this.actions.openConnection() },
      { label: '打开日志文件夹', click: () => this.actions.openLogs() },
      { type: 'separator' },
      ...(local
        ? [
            { label: '退出（她们继续在线）', click: () => this.actions.quit() },
            { label: '退出并停止 nest…', enabled: !down && !busy, click: () => this.actions.quitAndStop() },
          ]
        : [{ label: '退出', click: () => this.actions.quit() }]),
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
