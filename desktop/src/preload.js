'use strict'

// window.nestDesktop for the console (and the offline page). Everything is async over IPC;
// the main process checks that each call comes from the nest console or our own page.

const { contextBridge, ipcRenderer } = require('electron')

function argument(name) {
  const prefix = `--${name}=`
  const found = process.argv.find((arg) => arg.startsWith(prefix))
  return found ? found.slice(prefix.length) : ''
}

contextBridge.exposeInMainWorld('nestDesktop', {
  isDesktop: true,
  version: argument('nest-desktop-version'),
  // True when this window shows a nest other than the usual one (a test copy with fake accounts).
  testCopy: argument('nest-test-copy') === '1',
  // nest runs elsewhere (another computer): the console signs in with the password, and nest cannot be restarted here.
  remote: argument('nest-remote') === '1',
  // "chat": the chat window, drawn without the console's navigation.
  view: argument('nest-view') || 'console',
  titleBarHeight: 40,
  // The window's own buttons: Windows' three on the right (drawn over the page), macOS' three lights on the left.
  captionWidth: process.platform === 'darwin' ? 80 : 140,
  captionSide: process.platform === 'darwin' ? 'left' : 'right',
  // win32 or darwin: what the console offers (电脑上做事 is Windows' only).
  platform: process.platform,
  restartNest: () => ipcRenderer.invoke('nest:restart'),
  // The offline page: why, and the ways out.
  whyOffline: () => ipcRenderer.invoke('nest:why-offline'),
  retry: () => ipcRenderer.invoke('nest:retry'),
  openConnection: () => ipcRenderer.invoke('nest:open-connection'),
  useThisComputer: () => ipcRenderer.invoke('nest:use-this-computer'),
  openLogsFolder: () => ipcRenderer.invoke('nest:open-logs'),
  openDataFolder: () => ipcRenderer.invoke('nest:open-data'),
  getAutostart: () => ipcRenderer.invoke('nest:get-autostart'),
  setAutostart: (enabled) => ipcRenderer.invoke('nest:set-autostart', Boolean(enabled)),
  // Newer versions of this app (null: it cannot be updated, run from the sources say). Each gives the state after it.
  getUpdate: () => ipcRenderer.invoke('nest:get-update'),
  checkUpdate: () => ipcRenderer.invoke('nest:check-update'),
  downloadUpdate: () => ipcRenderer.invoke('nest:download-update'),
  installUpdate: () => ipcRenderer.invoke('nest:install-update'),
  openUpdatePage: () => ipcRenderer.invoke('nest:update-page'),
  setUpdateAuto: (on) => ipcRenderer.invoke('nest:set-update-auto', Boolean(on)),
  // Something about it changed: ask again. Returns the unsubscribe.
  onUpdate: (callback) => {
    const listener = () => callback()
    ipcRenderer.on('nest:update', listener)
    return () => ipcRenderer.removeListener('nest:update', listener)
  },
  quit: (options) => ipcRenderer.invoke('nest:quit', { stopNest: Boolean(options && options.stopNest) }),
  // A companion said something while this window was not in front: a Windows notification.
  notify: (note) =>
    ipcRenderer.invoke('nest:notify', {
      title: String(note && note.title ? note.title : ''),
      body: String(note && note.body ? note.body : ''),
      botId: String(note && note.botId ? note.botId : ''),
    }),
  // Her desktop pet's pictures, for the 小窝.
  petSprite: (botId) => ipcRenderer.invoke('nest:pet-sprite', String(botId || '')),
  openChat: () => ipcRenderer.invoke('nest:open-chat'),
  openConsole: () => ipcRenderer.invoke('nest:open-console'),
  // The 电脑上做事 settings window (its folders, 改不用问), from the console's 文件 page.
  openExecutor: () => ipcRenderer.invoke('nest:open-executor'),
  // Light or dark and the theme's colours, for the app's own windows and the title bar (src/theme.js).
  setTheme: (theme) =>
    ipcRenderer.invoke('nest:set-theme', {
      mode: String(theme && theme.mode ? theme.mode : 'system'),
      colors: theme && theme.colors ? JSON.parse(JSON.stringify(theme.colors)) : null,
    }),
  // A notification or a pet was clicked: show this bot's chat. Also one asked for while the page was still
  // loading (the chat window only: the console is not what was opened).
  onOpenBot: (callback) => {
    let on = true
    const show = (botId) => {
      if (!on || !botId) return
      ipcRenderer.invoke('nest:bot-shown', botId).catch(() => {})
      callback(botId)
    }
    const listener = (_event, botId) => show(String(botId))
    ipcRenderer.on('nest:open-bot', listener)
    if (argument('nest-view') === 'chat') {
      ipcRenderer.invoke('nest:wanted-bot').then((botId) => show(String(botId || ''))).catch(() => {})
    }
    return () => {
      on = false
      ipcRenderer.removeListener('nest:open-bot', listener)
    }
  },
})
