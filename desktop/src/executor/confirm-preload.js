'use strict'

// window.nestConfirm for the confirm window (our own local page only; the main process checks the sender).

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('nestConfirm', {
  data: () => ipcRenderer.invoke('confirm:data'),
  answer: (choice) => ipcRenderer.invoke('confirm:answer', String(choice)),
})
