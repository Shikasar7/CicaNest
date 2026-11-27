'use strict'

// window.nestExecutor for the 电脑上做事 settings window (our own local page only; the main process checks
// the sender).

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('nestExecutor', {
  state: () => ipcRenderer.invoke('executor:state'),
  setEnabled: (on) => ipcRenderer.invoke('executor:enabled', Boolean(on)),
  add: () => ipcRenderer.invoke('executor:add'),
  remove: (folder) => ipcRenderer.invoke('executor:remove', String(folder)),
  setMode: (folder, mode) => ipcRenderer.invoke('executor:folder-mode', String(folder), String(mode)),
  setAllowAll: (on) => ipcRenderer.invoke('executor:allow-all', Boolean(on)),
  setOpenForbidden: (on) => ipcRenderer.invoke('executor:open-forbidden', Boolean(on)),
  onChange: (callback) => ipcRenderer.on('executor:changed', () => callback()),
})
