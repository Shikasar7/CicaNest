'use strict'

// Bridge for ui/splash.html: state in, button presses out. Nothing else is exposed.

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('splash', {
  onState: (callback) => ipcRenderer.on('splash:state', (_event, state) => callback(state)),
  onEnter: (callback) => ipcRenderer.on('splash:enter', () => callback()),
  onLeave: (callback) => ipcRenderer.on('splash:leave', () => callback()),
  act: (id) => ipcRenderer.send('splash:action', String(id)),
  ready: () => ipcRenderer.send('splash:ready'),
})
