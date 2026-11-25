'use strict'

// nest desktop shell: single instance, launch sequence in a splash, the console in a Mica window,
// a tray icon that keeps the bots one click away. Configuration: see config.js.
//
// Remote mode (nest runs on another machine): no launch and no splash; the chat window and the console
// open the nest there, which asks for the remote-access password once. A companion's reply that arrives
// while the chat window is not in front becomes a Windows notification.

const fs = require('node:fs')
const path = require('node:path')
const { app, BrowserWindow, Menu, Notification, dialog, ipcMain, nativeImage, powerMonitor, session, shell } = require('electron')

const { bundledBackend, loadConfig, readConnection, remoteOrigin } = require('./config')
const { prepareHome } = require('./local-setup')
const log = require('./log')
const { Nest, detailOf } = require('./nest')
const { Launch } = require('./launcher')
const { Splash } = require('./splash')
const { MainWindow } = require('./mainWindow')
const { TrayController } = require('./tray')
const { OpenBot } = require('./open-bot')
const { Unread } = require('./unread')
const { RemoteNest, pickOrigin, probe, whyNot } = require('./remote')
const { useDoor } = require('./local-name')
const { Pets } = require('./pets')
const { movedAway } = require('./login-item')
const { fixElectronLink } = require('./electron-link')
const { Updater } = require('./update')
const { UpdateApp } = require('./update-app')
const { Executor } = require('./executor')
const { ExecutorStore } = require('./executor/store')
const { Confirmations } = require('./executor/confirm')
const { Theme } = require('./theme')
const { debugAllowed } = require('./debug-gate')

// The profile folder first: the saved connection (local or remote) lives in it.
const startup = loadConfig()
if (startup.profileDir) app.setPath('userData', startup.profileDir)
const CONNECTION_FILE = path.join(app.getPath('userData'), 'connection.json')
// A copy of its own: which of two addresses answers is found out once the app is ready (origin).
const INSTALLED = app.isPackaged ? app.getPath('userData') : null
const BUNDLED = INSTALLED ? bundledBackend(process.resourcesPath) : null // the nest that came with it
const config = { ...loadConfig(process.env, readConnection(CONNECTION_FILE), INSTALLED, BUNDLED), version: app.getVersion() }
const EXECUTOR_FILE = path.join(app.getPath('userData'), 'executor.json')
// The debug harness only for a launch made for a test (debug-gate.js).
if (INSTALLED) config.debugDir = null // not even in the installed app's files
const DEBUG_REFUSED = config.debugDir ? debugAllowed(config.debugDir, EXECUTOR_FILE).why : ''
if (DEBUG_REFUSED) config.debugDir = null
const ICONS = path.join(__dirname, '..', 'assets', 'icons')
const APP_ICON = path.join(ICONS, 'nest.ico')
const COOKIE_NAME = 'nest_session'
const HIDDEN = process.argv.includes('--hidden') // started at login: stay in the tray
// A nest other than the usual one (NEST_HOME elsewhere) must never be mistaken for the real bots.
const TEST_MARK = config.isTestCopy ? '测试副本' : ''
const APP_TITLE = TEST_MARK ? `nest · ${TEST_MARK}` : 'nest'

const APP_ID = 'nest'
app.setAppUserModelId(APP_ID)
// 语音输入 in the chat window: a nest on another machine is reached over plain http on the home network,
// where Chromium offers no microphone; this one address is treated as a secure page (nothing else is).
const PLAIN = config.origins.filter((origin) => origin.startsWith('http://'))
if (PLAIN.length) app.commandLine.appendSwitch('unsafely-treat-insecure-origin-as-secure', PLAIN.join(','))

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  run()
}

function run() {
  log.init(config.logsDir, config.debugDir)
  if (DEBUG_REFUSED) log.warn(`debug harness not started: ${DEBUG_REFUSED}`)
  log.info(
    `nest desktop ${app.getVersion()} (electron ${process.versions.electron}) ` +
      (config.remote ? `remote=${config.origin}` : `home=${config.home} port=${config.port}${config.bundled ? ' bundled' : ''}${config.undecided ? ' undecided' : ''}`),
  )

  const nest = config.remote ? new RemoteNest(config) : new Nest(config)
  const prefsFile = path.join(app.getPath('userData'), 'prefs.json')
  // Light/dark and the theme's colours from the console (loaded when ready, before the first window).
  const theme = new Theme({ file: path.join(app.getPath('userData'), 'theme.json'), log })
  let quitting = false
  let launch = null
  let cookieToken = null
  let mainWin = null
  let chatWin = null
  // Her words in the chat app while you were not looking at the chat window: the tray shows how many.
  const unread = new Unread({
    watching: () => Boolean(chatWin?.isWatched()),
    onChange: (count) => tray?.setUnread(count),
  })
  const wanted = new OpenBot()
  let connectWin = null
  let splash = null
  let tray = null
  let pets = null
  let executor = null
  let presentNow = () => {} // tells nest at once that the user is here (and asked for quiet or not)
  let updates = null // an installed app only
  let executorStore = null
  let executorWin = null
  let confirmations = null

  // ---- small helpers ---------------------------------------------------------------------------

  function readPrefs() {
    try {
      return JSON.parse(fs.readFileSync(prefsFile, 'utf8')) || {}
    } catch {
      return {}
    }
  }

  function writePrefs(patch) {
    try {
      fs.mkdirSync(path.dirname(prefsFile), { recursive: true })
      fs.writeFileSync(prefsFile, JSON.stringify({ ...readPrefs(), ...patch }, null, 2), 'utf8')
    } catch (err) {
      log.warn('could not save prefs:', err.message)
    }
  }

  // Toasts need a Start Menu shortcut carrying our AppUserModelID (install-shortcuts.js makes one).
  // Without it Windows drops them silently, so fall back to a tray balloon.
  function toastsWork() {
    const link = path.join(app.getPath('appData'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'nest.lnk')
    try {
      return Notification.isSupported() && shell.readShortcutLink(link).appUserModelId === 'nest'
    } catch {
      return false
    }
  }

  function notify(title, body, onClick = openNest, { quiet = false } = {}) {
    if (TEST_MARK) title = `${title}（${TEST_MARK}）`
    // A companion's words are private: the log says that one came, not what it said.
    log.info(quiet ? `notify: ${title}` : `notify: ${title} ${body}`)
    if (toastsWork()) {
      const toast = new Notification({ title, body, icon: path.join(ICONS, 'nest-64.png'), silent: true })
      toast.on('click', () => onClick())
      toast.show()
      return
    }
    tray?.balloon({
      title,
      content: body,
      iconType: 'custom',
      icon: nativeImage.createFromPath(path.join(ICONS, 'nest-64.png')),
      noSound: true,
    })
  }

  // In development the login item is electron.exe plus this folder; --hidden keeps it in the tray.
  function loginItem() {
    return app.isPackaged
      ? { path: process.execPath, args: ['--hidden'] }
      : { path: process.execPath, args: [app.getAppPath(), '--hidden'] }
  }

  function getAutostart() {
    try {
      return app.getLoginItemSettings(loginItem()).openAtLogin
    } catch {
      return false
    }
  }

  function setAutostart(enabled) {
    app.setLoginItemSettings({ openAtLogin: Boolean(enabled), ...loginItem() })
    log.info(`autostart ${enabled ? 'on' : 'off'}`)
    tray?.render(true)
    return getAutostart()
  }

  const confirmQueue = [] // the debug harness can pre-answer the next confirmation
  let dialogAbort = null

  async function confirm({ message, detail, ok }) {
    if (confirmQueue.length) {
      const answer = confirmQueue.shift()
      log.info(`confirm "${message}" answered by debug: ${answer}`)
      return answer
    }
    const parent = mainWin?.isVisible() ? mainWin.win : undefined
    const options = {
      type: 'question',
      title: APP_TITLE,
      message,
      detail,
      buttons: [ok, '取消'],
      defaultId: 1,
      cancelId: 1,
      noLink: true,
      icon: nativeImage.createFromPath(path.join(ICONS, 'nest-64.png')),
    }
    dialogAbort = new AbortController()
    options.signal = dialogAbort.signal
    try {
      const result = parent ? await dialog.showMessageBox(parent, options) : await dialog.showMessageBox(options)
      return result.response === 0
    } finally {
      dialogAbort = null
    }
  }

  async function login(token) {
    await session.defaultSession.cookies.set({
      url: config.origin,
      name: COOKIE_NAME,
      value: token,
      path: '/',
      httpOnly: true,
      sameSite: 'strict',
    })
    const changed = cookieToken !== null && cookieToken !== token
    cookieToken = token
    // Load the console now, hidden, so it is painted by the time the splash is done.
    if (!mainWin.alive()) mainWin.ensure()
    else if (changed || mainWin.offline) mainWin.reload()
    if (chatWin?.alive() && (changed || chatWin.offline)) chatWin.reload()
  }

  // ---- launch / restart --------------------------------------------------------------------------

  function startLaunch({ restart = false, visible = true } = {}) {
    if (config.undecided) return void openConnection() // nothing to start yet
    if (launch && !launch.finished) {
      if (visible) splash.show()
      return launch
    }
    const mainWasVisible = mainWin.isVisible()
    if (restart && mainWasVisible) mainWin.hide()
    const current = new Launch(nest, { restart, onLogin: login })
    launch = current
    tray.setBusy(restart ? '正在重启…' : '正在启动…')
    log.info(`${restart ? 'restart' : 'launch'} begins (visible=${visible})`)

    current.on('failed', (text) => {
      tray.setBusy(null)
      if (!splash.isVisible()) notify('nest 没有启动完成', text)
    })
    current.on('change', (state) => {
      if (state.phase === 'running') tray.setBusy(restart ? '正在重启…' : '正在启动…')
      else if (state.phase === 'waiting') tray.setBusy(null)
    })
    current.on('finish', async (result) => {
      log.info(`launch finished: ${result}`)
      tray.setBusy(null)
      const watching = splash.isVisible()
      await splash.leave()
      if (result === 'cancelled') return
      // A restart from the tray with the window closed ends in the tray too.
      if (watching && (!restart || mainWasVisible || result === 'console')) await mainWin.show()
    })

    splash.attach(current, { visible })
    current.start()
    return current
  }

  function onSplashAction(id, current) {
    if (!current || current !== launch) return
    if (id === 'retry') current.retry()
    else if (id === 'enter-console') current.enterConsole()
    else if (id === 'open-qq') current.openQQ()
    else if (id === 'open-logs') openFolder(config.logsDir)
    else if (id === 'dismiss') {
      // Esc: carry on in the background. Waiting for QQ is optional, so that just ends quietly.
      splash.hide()
      if (current.phase === 'waiting' || current.qqWaiting) current.enterConsole()
    }
  }

  function openNest() {
    if (config.undecided) return openConnection()
    if (launch && !launch.finished) {
      splash.show()
      return
    }
    mainWin.show()
  }

  function openChat(botId = '') {
    if (config.undecided) return openConnection()
    if (launch && !launch.finished) {
      splash.show()
      return
    }
    wanted.ask(botId)
    chatWin.show().then(() => {
      if (botId && chatWin.alive()) chatWin.webContents.send('nest:open-bot', botId)
    })
  }

  // ---- 连接设置 ------------------------------------------------------------------------------------

  // ---- 电脑上做事 (the Windows executor) ---------------------------------------------------------

  function openExecutor() {
    if (executorWin && !executorWin.isDestroyed()) {
      executorWin.show()
      executorWin.focus()
      return
    }
    executorWin = new BrowserWindow({
      width: 600,
      height: 520,
      resizable: true,
      maximizable: false,
      minimizable: false,
      title: '电脑上做事',
      icon: APP_ICON,
      autoHideMenuBar: true,
      backgroundColor: '#f2f4f5',
      webPreferences: {
        preload: path.join(__dirname, 'executor', 'settings-preload.js'),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        spellcheck: false,
      },
    })
    executorWin.setMenu(null)
    executorWin.webContents.on('will-navigate', (event) => event.preventDefault())
    executorWin.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    executorWin.loadFile(path.join(__dirname, 'ui', 'executor.html'))
  }

  function fromExecutor(event) {
    return executorWin && !executorWin.isDestroyed() && event.sender === executorWin.webContents
  }

  function executorChanged() {
    executor?.changed()
    tray?.render(true)
  }

  ipcMain.handle('executor:state', (event) => {
    if (!fromExecutor(event)) throw new Error('not allowed')
    const { key, ...shown } = executorStore.get()
    return { ...shown, status: executor?.state ?? 'off' }
  })

  ipcMain.handle('executor:enabled', (event, on) => {
    if (!fromExecutor(event)) throw new Error('not allowed')
    executorStore.setEnabled(on)
    executorChanged()
  })

  ipcMain.handle('executor:add', async (event) => {
    if (!fromExecutor(event)) throw new Error('not allowed')
    const picked = await dialog.showOpenDialog(executorWin, {
      title: '授权一个文件夹',
      properties: ['openDirectory'],
    })
    if (picked.canceled || !picked.filePaths[0]) return
    executorStore.add(picked.filePaths[0])
    executorChanged()
  })

  ipcMain.handle('executor:remove', (event, folder) => {
    if (!fromExecutor(event)) throw new Error('not allowed')
    executorStore.remove(folder)
    executorChanged()
  })

  ipcMain.handle('executor:read-only', (event, folder, on) => {
    if (!fromExecutor(event)) throw new Error('not allowed')
    executorStore.setReadOnly(folder, on)
    executorChanged()
  })

  // Loosening is asked once more in a window of this app (not the page's own), 取消 by default.
  async function sure(message, detail) {
    const { response } = await dialog.showMessageBox(executorWin, {
      type: 'warning',
      title: '电脑上做事',
      message,
      detail,
      buttons: ['取消', '打开'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    })
    return response === 1
  }

  ipcMain.handle('executor:allow-all', async (event, on) => {
    if (!fromExecutor(event)) throw new Error('not allowed')
    if (
      on &&
      !(await sure(
        '打开「完全允许」？',
        '伙伴在这台电脑上看、写、改、删、移动文件、跑命令、打开文件都不再弹窗问你，授权的文件夹以外也直接做。' +
          '命令能做的事很多，禁区只挡得住文件操作，挡不住命令。' +
          '程序和脚本仍然不能用「打开」来运行；定时任务不算在内（那时没人在，照样只做不用点头的事）。' +
          '她会读群聊、网页和插件给的东西，里面有人写了指挥她的话，她也可能照做。',
      ))
    ) {
      return
    }
    executorStore.setAllowAll(on)
    log.info(`executor: allow all ${on ? 'on' : 'off'}`)
    executorChanged()
  })

  ipcMain.handle('executor:open-forbidden', async (event, on) => {
    if (!fromExecutor(event)) throw new Error('not allowed')
    if (
      on &&
      !(await sure(
        '连禁区也放开？',
        '密钥、凭据、.env、浏览器和密码管理器的数据、系统文件夹、QQ 的数据、nest 自己的数据，她都能碰了。' +
          '关掉「完全允许」或者重启桌面端时，禁区会自己恢复。',
      ))
    ) {
      return
    }
    executorStore.setOpenForbidden(on)
    log.info(`executor: never-list ${on ? 'open' : 'closed'}`)
    executorChanged()
  })

  function openConnection() {
    if (connectWin && !connectWin.isDestroyed()) {
      connectWin.show()
      connectWin.focus()
      return
    }
    connectWin = new BrowserWindow({
      width: 560,
      height: 680,
      useContentSize: true,
      maximizable: false,
      minimizable: false,
      title: '连接设置',
      icon: APP_ICON,
      autoHideMenuBar: true,
      backgroundColor: '#f2f4f5',
      webPreferences: {
        preload: path.join(__dirname, 'connect-preload.js'),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        spellcheck: false,
      },
    })
    connectWin.setMenu(null)
    connectWin.webContents.on('will-navigate', (event) => event.preventDefault())
    connectWin.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    connectWin.loadFile(path.join(__dirname, 'ui', 'connect.html'))
  }

  function fromConnect(event) {
    return connectWin && !connectWin.isDestroyed() && event.sender === connectWin.webContents
  }

  ipcMain.handle('connect:current', (event) => {
    if (!fromConnect(event)) throw new Error('not allowed')
    const saved = readConnection(CONNECTION_FILE)
    return {
      remote: config.remote,
      origin: config.origin,
      saved: saved.remote || '',
      other: saved.other || '',
      fromEnv: Boolean(remoteOrigin(process.env.NEST_REMOTE_URL)),
      undecided: config.undecided,
      canRunHere: config.canRunHere,
      home: config.home,
    }
  })

  /** Why an address does not work, in a line or two. */
  async function said(origin) {
    const why = await whyNot(origin)
    if (!why) return `${origin} 刚才没有回应，再试一次。`
    return [`${why.title}。${why.text}`, ...why.steps.map((step, n) => `${n + 1}. ${step}`)].join('\n')
  }

  ipcMain.handle('connect:test', async (event, address) => {
    if (!fromConnect(event)) throw new Error('not allowed')
    const origin = remoteOrigin(address)
    if (!origin) return { ok: false, error: '这不像一个地址：写成 名字:端口 或 http://IP:端口' }
    const health = await probe(origin)
    if (!health) return { ok: false, error: await said(origin) }
    return { ok: true, origin, version: health.version || '' }
  })

  ipcMain.handle('connect:save', async (event, choice) => {
    if (!fromConnect(event)) throw new Error('not allowed')
    const local = choice?.mode === 'local'
    const before = readConnection(CONNECTION_FILE)
    const saved = { mode: local ? 'local' : 'remote' }
    if (local) {
      if (!config.canRunHere) return { ok: false, error: '这个安装包里没有 nest 后台，只能连到另一台。' }
      if (before.remote) saved.remote = before.remote // to come back to
      if (before.other) saved.other = before.other
    } else {
      const origin = remoteOrigin(choice?.address)
      if (!origin) return { ok: false, error: '先填 nest 的地址：写成 名字:端口 或 http://IP:端口' }
      const otherText = String(choice?.other || '').trim()
      const other = otherText ? remoteOrigin(otherText) : null
      if (otherText && !other) return { ok: false, error: '备用地址不像一个地址：写成 名字:端口 或 http://IP:端口' }
      saved.remote = origin
      if (other) saved.other = other
      // Not saved unseen: an address nothing answers at would leave the app with nothing to show.
      if (!choice?.anyway && !(await probe(origin)) && !(other && (await probe(other)))) {
        return { ok: false, unreachable: true, error: await said(origin) }
      }
      if (!config.remote && !(await leaveLocal())) return { ok: false, error: '' }
    }
    return switchTo(saved)
  })

  /** Keep which nest this app is for, and open again with it. */
  async function switchTo(saved) {
    const local = saved.mode === 'local'
    try {
      if (local && BUNDLED) await prepareHome(config.home)
      fs.mkdirSync(path.dirname(CONNECTION_FILE), { recursive: true })
      fs.writeFileSync(CONNECTION_FILE, JSON.stringify(saved, null, 2), 'utf8')
    } catch (err) {
      return { ok: false, error: `保存失败：${err.message}` }
    }
    log.info(`connection saved: ${local ? 'local' : saved.remote}; relaunching`)
    setTimeout(() => {
      quitting = true
      app.relaunch({ args: process.argv.slice(1).filter((arg) => arg !== '--hidden') })
      app.exit(0)
    }, 100)
    return { ok: true }
  }

  // Going over to a nest elsewhere while the one on this computer runs: its bots stay online unless it is
  // stopped, and from then on this app does not manage it. False: not going after all.
  async function leaveLocal() {
    if (config.undecided || !(await nest.health()) || !(await nest.isOurs())) return true
    const { response } = await dialog.showMessageBox(connectWin, {
      type: 'question',
      title: APP_TITLE,
      message: '这台电脑上的 nest 还在运行，要停掉吗？',
      detail: '停掉的话，它的 bot 会下线；数据都还在，以后换回来就接着用。不停的话，它们继续在线。',
      buttons: ['停掉', '让它继续运行', '取消'],
      defaultId: 0,
      cancelId: 2,
      noLink: true,
    })
    if (response === 2) return false
    if (response === 1) return true
    try {
      await nest.stop()
      return true
    } catch (err) {
      dialog.showErrorBox('nest 没能停止', err.message)
      return false
    }
  }

  async function openFolder(folder) {
    fs.mkdirSync(folder, { recursive: true })
    const error = await shell.openPath(folder)
    if (error) log.warn(`open ${folder}: ${error}`)
    return !error
  }

  async function pauseAll() {
    const yes = await confirm({
      message: '暂停全部 bot？',
      detail: '暂停后，所有 bot 不再调用模型，也不会在群里发任何消息，直到你在控制台里把它们改回来。',
      ok: '全部暂停',
    })
    if (!yes) return false
    try {
      const res = await nest.api('POST', '/api/bots/pause-all')
      if (res.status !== 200) throw new Error(detailOf(res))
      const count = res.json?.paused ?? 0
      notify('已全部暂停', `${count} 个 bot 已暂停。在控制台里可以逐个恢复。`)
      return true
    } catch (err) {
      notify('暂停失败', err.message || String(err))
      return false
    } finally {
      tray.refresh()
    }
  }

  async function quitApp({ stopNest = false } = {}) {
    if (stopNest) {
      tray.setBusy('正在停止…')
      try {
        await nest.stop()
      } catch (err) {
        tray.setBusy(null)
        log.error('stop before quit failed:', err)
        dialog.showErrorBox('nest 没能停止', `${err.message}\n\n应用不会退出；可以稍后再试，或在任务管理器里结束 python.exe。`)
        return false
      }
    }
    quitting = true
    log.info(`quit (stopNest=${stopNest})`)
    app.quit()
    return true
  }

  async function quitAndStop() {
    const yes = await confirm({
      message: '退出并停止 nest？',
      detail: '所有 bot 会一起下线，直到下次打开 nest。只想关掉窗口的话，选「退出」就行，bot 会继续在后台运行。',
      ok: '退出并停止',
    })
    if (yes) await quitApp({ stopNest: true })
  }

  // ---- IPC for the console (window.nestDesktop) --------------------------------------------------

  function handle(channel, fn) {
    ipcMain.handle(channel, async (event, ...args) => {
      const frameUrl = event.senderFrame?.url || ''
      const win = [mainWin, chatWin].find((w) => w && event.sender === w.webContents)
      if (!win || !win.isTrustedPage(frameUrl)) {
        log.warn(`refused ${channel} from ${frameUrl}`)
        throw new Error('not allowed')
      }
      return fn(...args)
    })
  }

  // The offline page. Nest on this computer is offered only where it came with the app: run from the sources,
  // this folder's nest may be one the user has retired.
  const canRunHere = Boolean(config.remote && BUNDLED && !remoteOrigin(process.env.NEST_REMOTE_URL))
  handle('nest:why-offline', async () => {
    const why = (await whyNot(config.origin)) || { title: '正在重新连接', text: 'nest 有回应了。', steps: [] }
    return { ...why, canRunHere }
  })
  handle('nest:retry', () => {
    for (const win of [mainWin, chatWin]) if (win?.offline) win.reload()
  })
  handle('nest:open-connection', () => openConnection())
  handle('nest:use-this-computer', () => {
    if (!canRunHere) return { ok: false, error: '这个应用里没有带 nest，只能连到另一台。' }
    const before = readConnection(CONNECTION_FILE)
    // The address stays, to come back to.
    return switchTo({
      mode: 'local',
      ...(before.remote ? { remote: before.remote } : {}),
      ...(before.other ? { other: before.other } : {}),
    })
  })
  handle('nest:restart', () => {
    if (config.remote) return false // the nest elsewhere is restarted there
    startLaunch({ restart: true, visible: true })
    return true
  })
  handle('nest:notify', ({ title, body, botId }) => {
    const words = String(body || '').replace(/\s+/g, ' ').slice(0, 200)
    notify(String(title || 'nest').slice(0, 40), words, () => openChat(String(botId || '')), { quiet: true })
    return true
  })
  handle('nest:pet-sprite', (botId) => pets?.spriteFor(String(botId || '')) ?? null)
  handle('nest:open-chat', () => {
    openChat()
    return true
  })
  handle('nest:wanted-bot', () => wanted.id)
  handle('nest:bot-shown', (botId) => wanted.shown(String(botId || '')))
  // The chat window's 「控制台」 button.
  handle('nest:open-console', () => {
    openNest()
    return true
  })
  handle('nest:set-theme', (value) => theme.set(value))
  handle('nest:open-logs', () => openFolder(config.logsDir))
  handle('nest:open-data', () => openFolder(config.dataDir))
  handle('nest:get-autostart', () => getAutostart())
  handle('nest:set-autostart', (enabled) => setAutostart(Boolean(enabled)))
  // Updates (null: this app cannot be updated). Installing asks in a window of this app, whoever calls.
  handle('nest:get-update', () => updates?.state() ?? null)
  handle('nest:check-update', () => updates?.check() ?? null)
  handle('nest:download-update', () => updates?.download() ?? null)
  handle('nest:install-update', async () => {
    await updates?.install()
    return updates?.state() ?? null
  })
  handle('nest:update-page', () => updates?.openPage())
  handle('nest:set-update-auto', (on) => {
    updates?.setAuto(Boolean(on))
    return updates?.state() ?? null
  })
  handle('nest:quit', (options) => {
    // Answer first; quitting tears the page down.
    setTimeout(() => quitApp({ stopNest: !config.remote && Boolean(options?.stopNest) }), 50)
    return true
  })

  // ---- 更新 ----------------------------------------------------------------------------------------

  function makeUpdates() {
    const { updates: source } = JSON.parse(fs.readFileSync(path.join(app.getAppPath(), 'package.json'), 'utf8'))
    return new UpdateApp({
      updater: new Updater({
        version: app.getVersion(),
        source,
        dir: path.join(INSTALLED, 'updates'),
        get: require('./update-net').get,
        log,
      }),
      remote: config.remote,
      icon: nativeImage.createFromPath(path.join(ICONS, 'nest-64.png')),
      title: APP_TITLE,
      prefs: readPrefs,
      savePrefs: writePrefs,
      notify,
      parent: () => [mainWin, chatWin].find((w) => w?.isVisible())?.win,
      healthVersion: async () => (await nest.health())?.version || '',
      stopNest: async () => {
        if (config.remote || config.undecided || !(await nest.health()) || !(await nest.isOurs())) return false
        tray?.setBusy('正在更新…')
        try {
          await nest.stop()
          return true
        } finally {
          tray?.setBusy(null)
        }
      },
      startNest: () => startLaunch({ restart: false, visible: false }),
      quit: () => quitApp({ stopNest: false }),
      onChange: () => {
        tray?.render()
        for (const win of [mainWin, chatWin]) if (win?.alive()) win.webContents.send('nest:update')
      },
      log,
    })
  }

  // ---- app lifecycle -------------------------------------------------------------------------

  app.on('second-instance', (_event, argv) => {
    log.info('second instance: focusing')
    if (argv.includes('--hidden')) return
    if (config.remote) openChat()
    else openNest()
  })

  app.on('before-quit', () => {
    quitting = true
  })

  // The tray keeps the app alive; closing windows never quits.
  app.on('window-all-closed', () => {})

  app.on('web-contents-created', (_event, contents) => {
    contents.on('will-attach-webview', (event) => event.preventDefault())
    theme.attach(contents)
  })

  app.whenReady().then(async () => {
    if (INSTALLED && BUNDLED) {
      updates = makeUpdates()
      if (!(await updates.begin())) return app.exit(0)
    }
    try {
      // Before the first request: a .local name takes Windows about 11 seconds to look up.
      await useDoor(session.defaultSession)
    } catch (err) {
      log.warn('local name: left to Windows:', err.message)
    }
    if (config.origins.length > 1) {
      config.origin = await pickOrigin(config.origins)
      log.info(`using ${config.origin}`)
    }
    if (!app.isPackaged) {
      const link = path.join(app.getPath('appData'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Electron.lnk')
      const options = { appId: APP_ID, execPath: process.execPath, icon: APP_ICON, appPath: app.getAppPath() }
      if (fixElectronLink(link, shell, options)) log.info('gave Electron.lnk the nest icon')
    }
    theme.load()
    Menu.setApplicationMenu(null)
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback, details) => {
      if (permission === 'clipboard-sanitized-write') return callback(true)
      // The microphone, for 语音输入: only nest's own pages, and only sound (never the camera or the screen).
      const types = details?.mediaTypes ?? []
      let origin = ''
      try {
        origin = new URL(details?.requestingUrl || '').origin
      } catch {
        origin = ''
      }
      callback(permission === 'media' && origin === config.origin && types.length > 0 && types.every((t) => t === 'audio'))
    })

    mainWin = new MainWindow({
      config,
      title: APP_TITLE,
      icon: APP_ICON,
      version: app.getVersion(),
      stateFile: path.join(app.getPath('userData'), 'window.json'),
      isQuitting: () => quitting,
      onHiddenToTray: () => {
        if (readPrefs().closeHintShown) return
        writePrefs({ closeHintShown: true })
        notify('nest 还在后台运行', 'nest 还在后台运行，bot 不受影响。点任务栏右下角的图标可以再打开。')
      },
    })
    chatWin = new MainWindow({
      config,
      title: TEST_MARK ? `nest 聊天 · ${TEST_MARK}` : 'nest 聊天',
      icon: APP_ICON,
      version: app.getVersion(),
      stateFile: path.join(app.getPath('userData'), 'chat-window.json'),
      isQuitting: () => quitting,
      onHiddenToTray: () => {},
      page: '/?view=chat#/chat',
      view: 'chat',
      onFocus: () => unread.clear(),
      defaultSize: { width: 920, height: 720 },
      minSize: { width: 420, height: 480 },
    })
    // Bottom-left of the splash: which nest this is (hover shows the folder).
    splash = new Splash({
      icon: APP_ICON,
      title: APP_TITLE,
      footnote: `127.0.0.1:${config.port}`,
      footnoteTitle: config.home,
      testCopy: config.isTestCopy,
      onAction: onSplashAction,
    })
    tray = new TrayController({
      nest,
      name: TEST_MARK ? `nest ${TEST_MARK}` : 'nest',
      remote: config.remote,
      onStatus: async (status) => {
        if (status.kind !== 'down') pets?.refreshIfEmpty()
        if (config.remote) {
          // Back after nest's machine (or the network) was away: reload what showed the offline page.
          if (status.kind === 'down') return
          for (const win of [mainWin, chatWin]) if (win.offline) win.reload()
          return
        }
        // nest restarted by someone else (启动.bat, the console): pick up the new token.
        if (status.kind === 'down' || (launch && !launch.finished)) return
        const runtime = nest.readRuntime()
        if (runtime && cookieToken && runtime.token !== cookieToken) {
          log.info('nest has a new token; refreshing the console login')
          await login(runtime.token)
        } else if (mainWin.offline) {
          mainWin.reload()
        }
      },
      actions: {
        open: () => openNest(),
        openChat: () => openChat(),
        openHome: () => openChat('@home'),
        openConnection: () => openConnection(),
        openExecutor: () => openExecutor(),
        updateItem: () => updates?.menuItem() ?? null,
        update: () => updates?.ask(),
        executorState: () => executor?.state ?? 'off',
        pets: () => {
          const until = pets?.quiet() ? new Date(pets.saved.quietUntil) : null
          const quietUntil = until
            ? `${until.getMonth() + 1}/${until.getDate()} ${String(until.getHours()).padStart(2, '0')}:${String(until.getMinutes()).padStart(2, '0')}`
            : ''
          return {
            items: pets?.menuItems() ?? [],
            hidden: Boolean(pets?.hidden),
            onTop: pets?.saved.onTop !== false,
            stepAside: pets?.saved.stepAside !== false,
            quietUntil,
          }
        },
        quietPets: (ms) => {
          if (ms === 'tomorrow') {
            const morning = new Date()
            morning.setDate(morning.getDate() + 1)
            morning.setHours(8, 0, 0, 0)
            pets?.quietFor(morning.getTime() - Date.now())
          } else pets?.quietFor(ms)
          presentNow() // nest hears of it now, not at the next minute
        },
        setPet: (botId, on) => pets?.setOn(botId, on),
        setSkin: (botId, skinId) => pets?.setSkin(botId, skinId),
        setAccessory: (botId, id, on) => pets?.setAccessory(botId, id, on),
        hidePets: (hidden) => pets?.setHidden(hidden),
        setPetsOnTop: (on) => pets?.setOnTop(on),
        setPetsStepAside: (on) => pets?.setStepAside(on),
        reloadPets: () => pets?.reloadPacks(),
        openPets: () => openFolder(pets.petsDir),
        pauseAll: () => pauseAll(),
        restart: () => startLaunch({ restart: true, visible: true }),
        start: () => startLaunch({ restart: false, visible: true }),
        getAutostart: () => getAutostart(),
        setAutostart: (enabled) => setAutostart(enabled),
        openLogs: () => openFolder(config.logsDir),
        quit: () => quitApp({ stopNest: false }),
        quitAndStop: () => quitAndStop(),
      },
    })
    tray.start()
    if (INSTALLED && movedAway(app.getLoginItemSettings().launchItems, APP_ID, process.execPath, fs.existsSync)) {
      setAutostart(true) // installed into another folder since
    }
    pets = new Pets({
      config,
      nest,
      petsDir: config.petsDir || path.join(app.getPath('userData'), 'pets'),
      stateFile: path.join(app.getPath('userData'), 'pets.json'),
      openChat: (botId) => openChat(botId),
      onChange: () => tray?.render(),
      onMessage: (message) => unread.heard(message),
    })
    pets.start()
    executorStore = new ExecutorStore(EXECUTOR_FILE)
    confirmations = new Confirmations({ icon: APP_ICON, debug: Boolean(config.debugDir) })
    executor = new Executor({
      api: (method, pathname, body, options) => nest.api(method, pathname, body, options),
      ask: (data) => confirmations.ask(data),
      settings: () => executorStore.get(),
      // Deleting goes to the Recycle Bin; opening uses the default program (a page: the default browser).
      system: {
        trash: (target) => shell.trashItem(target),
        open: async ({ path: target, url }) => {
          if (url) {
            await shell.openExternal(url)
            return ''
          }
          return shell.openPath(target)
        },
      },
      // This app's own data, a nest running on this PC (its .env, its database) and the nest it brings are never
      // touched.
      extraForbidden: [app.getPath('userData'), config.remote ? null : config.home, BUNDLED?.folder].filter(Boolean),
      // The steps taken, so one nest hands over again is not done twice (also after a restart of this app).
      stepsFile: path.join(app.getPath('userData'), 'executor-steps.json'),
      // Switched off: the windows still open are a no.
      onStop: () => confirmations?.closeAll(),
      onStatus: (state) => {
        log.info(`executor: ${state}`)
        tray?.render(true)
        if (executorWin && !executorWin.isDestroyed()) executorWin.webContents.send('executor:changed')
      },
      log,
    })
    setTimeout(() => executor.start(), 3000)
    // nest sends reminders here rather than to QQ while it hears from this app (about every minute).
    // How long the user has been idle too: nest only comes to them (主动找你) while they are at the computer.
    // And how long 安静一会儿 still holds: then nobody comes to them, not even with a message.
    const present = () => {
      const quiet = pets?.quiet() ? Math.max(0, Math.round((pets.saved.quietUntil - Date.now()) / 1000)) : 0
      nest.api('POST', '/api/desk/presence', { idle: powerMonitor.getSystemIdleTime(), quiet }).catch(() => {})
    }
    presentNow = present
    setTimeout(present, 5000)
    setInterval(present, 60_000)
    if (config.undecided) {
      if (!HIDDEN) openConnection()
    } else if (config.remote) {
      // Loaded now even when started hidden: the chat window is what hears a companion speak.
      chatWin.ensure()
      if (!HIDDEN) openChat()
    } else {
      startLaunch({ restart: false, visible: !HIDDEN })
    }

    updates?.start()

    if (config.debugDir) {
      require('./debug').install({
        config,
        get: () => ({ mainWin, chatWin, splash, tray, launch, nest, pets, confirmations }),
        confirmQueue,
        cancelDialog: () => dialogAbort?.abort(),
        startLaunch,
        quitApp,
      })
    }
  })

  app.on('will-quit', () => {
    executor?.stop()
    pets?.stop()
    tray?.destroy()
  })
}

