'use strict'

// window.nestPet for the 桌宠 overlay (our own local page; the main process checks the sender).

const { contextBridge, ipcRenderer } = require('electron')

function listen(channel, callback) {
  const listener = (_event, value) => callback(value)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

contextBridge.exposeInMainWorld('nestPet', {
  onState: (callback) => listen('pet:state', callback),
  onEvent: (callback) => listen('pet:event', callback),
  onIdle: (callback) => listen('pet:idle', callback),
  // The screens changed (a display added, the taskbar moved): their work areas in page pixels.
  onScreens: (callback) => listen('pet:screens', callback),
  // From a pet's right-click menu: { botId, kind: 'talk' | 'poke' | 'dock' | 'come-out' }.
  onCommand: (callback) => listen('pet:command', callback),
  // Over a pet or its bubble: take the mouse; elsewhere let it through to the windows underneath.
  setInteractive: (yes) => ipcRenderer.invoke('pet:interactive', Boolean(yes)),
  // Where the mouse is now, in page pixels; (-1, -1): not over this overlay.
  pointer: () => ipcRenderer.invoke('pet:pointer'),
  // A bubble with an input opened: take the keyboard.
  focus: () => ipcRenderer.invoke('pet:focus'),
  say: (botId, text) => ipcRenderer.invoke('pet:say', String(botId), String(text)),
  openChat: (botId) => ipcRenderer.invoke('pet:open-chat', String(botId)),
  savePosition: (botId, position) => ipcRenderer.invoke('pet:position', String(botId), position),
  menu: (botId, docked) => ipcRenderer.invoke('pet:menu', String(botId), Boolean(docked)),
})
