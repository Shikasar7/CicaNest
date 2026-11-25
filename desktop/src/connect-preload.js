'use strict'

// window.nestConnect for the 连接设置 window (our own local page only; the main process checks the sender).

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('nestConnect', {
  current: () => ipcRenderer.invoke('connect:current'),
  test: (address) => ipcRenderer.invoke('connect:test', String(address ?? '')),
  save: (choice) =>
    ipcRenderer.invoke('connect:save', {
      mode: choice?.mode === 'local' ? 'local' : 'remote',
      address: String(choice?.address ?? ''),
      other: String(choice?.other ?? ''),
      anyway: Boolean(choice?.anyway),
    }),
})
