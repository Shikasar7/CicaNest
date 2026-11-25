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
  captionWidth: 140,
  restartNest: () => ipcRenderer.invoke('nest:restart'),
  openLogsFolder: () => ipcRenderer.invoke('nest:open-logs'),
  openDataFolder: () => ipcRenderer.invoke('nest:open-data'),
  getAutostart: () => ipcRenderer.invoke('nest:get-autostart'),
  setAutostart: (enabled) => ipcRenderer.invoke('nest:set-autostart', Boolean(enabled)),
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
  // Light or dark and the theme's colours, for the app's own windows and the title bar (src/theme.js).
  setTheme: (theme) =>
    ipcRenderer.invoke('nest:set-theme', {
      mode: String(theme && theme.mode ? theme.mode : 'system'),
      colors: theme && theme.colors ? JSON.parse(JSON.stringify(theme.colors)) : null,
    }),
  // The notification was clicked: show this bot's chat.
  onOpenBot: (callback) => {
    const listener = (_event, botId) => callback(String(botId))
    ipcRenderer.on('nest:open-bot', listener)
    return () => ipcRenderer.removeListener('nest:open-bot', listener)
  },
})
