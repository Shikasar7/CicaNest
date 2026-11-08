'use strict'

// nest desktop shell: single instance, launch sequence in a splash, the console in a Mica window,
// a tray icon that keeps the bots one click away. Configuration: see config.js.

const fs = require('node:fs')
const path = require('node:path')
const { app, Menu, Notification, dialog, ipcMain, nativeImage, session, shell } = require('electron')

const { loadConfig } = require('./config')
const log = require('./log')
const { Nest, detailOf } = require('./nest')
const { Launch } = require('./launcher')
const { Splash } = require('./splash')
const { MainWindow } = require('./mainWindow')
const { TrayController } = require('./tray')

const config = loadConfig()
const ICONS = path.join(__dirname, '..', 'assets', 'icons')
const APP_ICON = path.join(ICONS, 'nest.ico')
const COOKIE_NAME = 'nest_session'
const HIDDEN = process.argv.includes('--hidden') // started at login: stay in the tray
// A nest other than the usual one (NEST_HOME elsewhere) must never be mistaken for the real bots.
const TEST_MARK = config.isTestCopy ? '测试副本' : ''
const APP_TITLE = TEST_MARK ? `nest · ${TEST_MARK}` : 'nest'

if (config.profileDir) app.setPath('userData', config.profileDir)
app.setAppUserModelId('nest')

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  run()
}

function run() {
  log.init(config.logsDir, config.debugDir)
  log.info(`nest desktop ${app.getVersion()} (electron ${process.versions.electron}) home=${config.home} port=${config.port}`)

  const nest = new Nest(config)
  const prefsFile = path.join(app.getPath('userData'), 'prefs.json')
  let quitting = false
  let launch = null
  let cookieToken = null
  let mainWin = null
  let splash = null
  let tray = null

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

  function notify(title, body) {
    if (TEST_MARK) title = `${title}（${TEST_MARK}）`
    log.info(`notify: ${title} ${body}`)
    if (toastsWork()) {
      const toast = new Notification({ title, body, icon: path.join(ICONS, 'nest-64.png'), silent: true })
      toast.on('click', () => openNest())
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
  }

  // ---- launch / restart --------------------------------------------------------------------------

  function startLaunch({ restart = false, visible = true } = {}) {
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
    if (launch && !launch.finished) {
      splash.show()
      return
    }
    mainWin.show()
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
      if (event.sender !== mainWin.webContents || !mainWin.isTrustedPage(frameUrl)) {
        log.warn(`refused ${channel} from ${frameUrl}`)
        throw new Error('not allowed')
      }
      return fn(...args)
    })
  }

  handle('nest:restart', () => {
    startLaunch({ restart: true, visible: true })
    return true
  })
  handle('nest:open-logs', () => openFolder(config.logsDir))
  handle('nest:open-data', () => openFolder(config.dataDir))
  handle('nest:get-autostart', () => getAutostart())
  handle('nest:set-autostart', (enabled) => setAutostart(Boolean(enabled)))
  handle('nest:quit', (options) => {
    // Answer first; quitting tears the page down.
    setTimeout(() => quitApp({ stopNest: Boolean(options?.stopNest) }), 50)
    return true
  })

  // ---- app lifecycle -------------------------------------------------------------------------

  app.on('second-instance', (_event, argv) => {
    log.info('second instance: focusing')
    if (argv.includes('--hidden')) return
    openNest()
  })

  app.on('before-quit', () => {
    quitting = true
  })

  // The tray keeps the app alive; closing windows never quits.
  app.on('window-all-closed', () => {})

  app.on('web-contents-created', (_event, contents) => {
    contents.on('will-attach-webview', (event) => event.preventDefault())
  })

  app.whenReady().then(() => {
    Menu.setApplicationMenu(null)
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
      callback(permission === 'clipboard-sanitized-write')
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
      onStatus: async (status) => {
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
    startLaunch({ restart: false, visible: !HIDDEN })

    if (config.debugDir) {
      require('./debug').install({
        config,
        get: () => ({ mainWin, splash, tray, launch, nest }),
        confirmQueue,
        cancelDialog: () => dialogAbort?.abort(),
        startLaunch,
        quitApp,
      })
    }
  })

  app.on('will-quit', () => {
    tray?.destroy()
  })
}

