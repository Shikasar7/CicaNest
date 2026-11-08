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
  titleBarHeight: 40,
  captionWidth: 140,
  restartNest: () => ipcRenderer.invoke('nest:restart'),
  openLogsFolder: () => ipcRenderer.invoke('nest:open-logs'),
  openDataFolder: () => ipcRenderer.invoke('nest:open-data'),
  getAutostart: () => ipcRenderer.invoke('nest:get-autostart'),
  setAutostart: (enabled) => ipcRenderer.invoke('nest:set-autostart', Boolean(enabled)),
  quit: (options) => ipcRenderer.invoke('nest:quit', { stopNest: Boolean(options && options.stopNest) }),
})
