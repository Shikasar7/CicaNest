'use strict'

// Debug harness, loaded only when NEST_DESKTOP_DEBUG_DIR is set. Lines appended to
// <dir>\commands.txt are run in order; screenshots and a log land in <dir>.
//
//   capture splash|main <name>             page pixels (webContents.capturePage)
//   shot splash|main <name>                the window itself via Windows capture (caption buttons; not the Mica backdrop)
//   burst splash|main <name> <n> <ms>      n page captures, ms apart (motion)
//   tray <label prefix>                    press a tray menu item
//   confirm yes|no                         pre-answer the next confirmation dialog
//   cancel-dialog                          dismiss an open confirmation dialog
//   click-splash <action id>               press a splash button
//   exec splash|main <js>                  run JS in the page, log the result
//   splash-state <json>                    draw an arbitrary state in the splash (visual checks)
//   theme light|dark|system                force the colour scheme
//   offline | console                      show the offline page / the console in the main window
//   close-main | open | restart | refresh | status | quit | quit-stop

const fs = require('node:fs')
const path = require('node:path')
const { desktopCapturer, nativeTheme, screen } = require('electron')
const log = require('./log')

function install({ config, get, confirmQueue, cancelDialog, startLaunch, quitApp }) {
  const dir = config.debugDir
  fs.mkdirSync(dir, { recursive: true })
  const commandsFile = path.join(dir, 'commands.txt')
  fs.writeFileSync(commandsFile, '')
  let offset = 0
  let seq = 0
  let queue = Promise.resolve()

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

  function save(image, name) {
    seq += 1
    const file = path.join(dir, `${String(seq).padStart(3, '0')}-${name}.png`)
    fs.writeFileSync(file, image.toPNG())
    log.info(`debug: saved ${file}`)
  }

  function target(which) {
    const { mainWin, splash } = get()
    return which === 'splash' ? splash?.win : mainWin?.win
  }

  async function capture(which, name) {
    const win = target(which)
    if (!win || win.isDestroyed()) return log.info(`debug: no ${which} window`)
    save(await win.webContents.capturePage(), `${which}-${name}`)
  }

  // The window as Windows captures it (with the caption buttons), taken from the window itself,
  // so nothing else on the screen ends up in the picture even if another window covers it.
  async function shot(which, name) {
    const win = target(which)
    if (!win || win.isDestroyed() || !win.isVisible()) return log.info(`debug: ${which} is not visible`)
    const id = win.getMediaSourceId()
    const { width, height } = win.getBounds()
    const scale = screen.getDisplayMatching(win.getBounds()).scaleFactor
    const sources = await desktopCapturer.getSources({
      types: ['window'],
      thumbnailSize: { width: Math.round(width * scale), height: Math.round(height * scale) },
      fetchWindowIcons: false,
    })
    const source = sources.find((s) => s.id === id)
    if (!source) return log.info(`debug: window source ${id} not found`)
    save(source.thumbnail, `${which}-window-${name}`)
  }

  function status() {
    const { mainWin, splash, tray, launch } = get()
    return {
      tray: tray?.view(),
      trayIcon: tray?.shownKind,
      tooltip: tray?.shownTip,
      menu: tray?.menu?.items.map((i) => (i.type === 'separator' ? '---' : `${i.label}${i.enabled ? '' : ' (disabled)'}${i.type === 'checkbox' ? ` [${i.checked ? 'x' : ' '}]` : ''}`)),
      main: mainWin?.alive()
        ? { visible: mainWin.win.isVisible(), title: mainWin.win.getTitle(), url: mainWin.win.webContents.getURL(), bounds: mainWin.win.getBounds(), offline: mainWin.offline }
        : null,
      splash: splash?.win && !splash.win.isDestroyed() ? { visible: splash.win.isVisible(), bounds: splash.win.getBounds() } : null,
      launch: launch ? { phase: launch.phase, finished: launch.finished, steps: launch.steps.map((s) => `${s.id}:${s.state}:${s.detail}`) } : null,
    }
  }

  async function execute(line) {
    const [command, ...rest] = line.trim().split(/\s+/)
    const arg = line.trim().slice(command.length).trim()
    const { mainWin, splash, tray } = get()
    log.info(`debug> ${line.trim()}`)
    switch (command) {
      case 'capture':
        return capture(rest[0], rest[1] || 'page')
      case 'shot':
        return shot(rest[0], rest[1] || 'window')
      case 'offline':
        return mainWin?.showOffline()
      case 'console':
        return mainWin?.loadConsole()
      case 'burst': {
        const count = Number(rest[2] || 6)
        const every = Number(rest[3] || 80)
        for (let i = 0; i < count; i += 1) {
          await capture(rest[0], `${rest[1] || 'burst'}-${i}`)
          await sleep(every)
        }
        return
      }
      case 'tray':
        return log.info(`debug: tray "${arg}" -> ${tray.clickItem(arg)}`)
      case 'confirm':
        confirmQueue.push(rest[0] === 'yes')
        return
      case 'cancel-dialog':
        return cancelDialog()
      case 'click-splash': {
        const wc = splash?.webContents
        if (!wc) return log.info('debug: no splash')
        const ok = await wc.executeJavaScript(
          `(() => { const b = document.querySelector('[data-action=${JSON.stringify(rest[0])}]'); if (b) b.click(); return Boolean(b) })()`,
        )
        return log.info(`debug: click-splash ${rest[0]} -> ${ok}`)
      }
      case 'exec': {
        const which = rest[0]
        const code = arg.slice(which.length).trim()
        const wc = which === 'splash' ? splash?.webContents : mainWin?.webContents
        if (!wc) return log.info(`debug: no ${which}`)
        const result = await wc.executeJavaScript(code).catch((err) => `error: ${err.message}`)
        return log.info(`debug: exec ${which} -> ${JSON.stringify(result)}`)
      }
      case 'splash-state': {
        const wc = splash?.webContents
        if (!wc) return log.info('debug: no splash')
        wc.send('splash:state', JSON.parse(arg))
        return
      }
      case 'theme':
        nativeTheme.themeSource = rest[0]
        return
      case 'close-main':
        if (mainWin?.alive()) mainWin.win.close()
        return
      case 'open':
        return tray.actions.open()
      case 'restart':
        startLaunch({ restart: true, visible: true })
        return
      case 'refresh':
        await tray.refresh()
        return log.info(`debug: tray ${JSON.stringify(tray.view())}`)
      case 'status':
        return log.info(`debug: status ${JSON.stringify(status())}`)
      case 'quit':
        return quitApp({ stopNest: false })
      case 'quit-stop':
        return quitApp({ stopNest: true })
      case 'sleep':
        return sleep(Number(rest[0] || 500))
      default:
        return log.info(`debug: unknown command ${command}`)
    }
  }

  fs.watchFile(commandsFile, { interval: 200 }, () => {
    let text
    try {
      const buffer = fs.readFileSync(commandsFile)
      text = buffer.subarray(offset).toString('utf8')
      const lastNewline = text.lastIndexOf('\n')
      if (lastNewline < 0) return
      text = text.slice(0, lastNewline)
      offset += Buffer.byteLength(text, 'utf8') + 1
    } catch {
      return
    }
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue
      queue = queue.then(() => execute(line)).catch((err) => log.error('debug command failed:', err))
    }
  })

  // Capture the splash shortly after every state change, so its whole run can be reviewed.
  let watched = null
  let timer = null
  let n = 0
  setInterval(() => {
    const { launch } = get()
    if (!launch || launch === watched) return
    watched = launch
    launch.on('change', (state) => {
      clearTimeout(timer)
      timer = setTimeout(() => {
        n += 1
        capture('splash', `auto-${String(n).padStart(2, '0')}-${state.phase}`).catch(() => {})
      }, 420)
    })
  }, 100)

  log.info(`debug harness on: ${commandsFile}`)
}

module.exports = { install }
