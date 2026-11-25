'use strict'

// Buttons of the offline page. The main process reloads the console by itself once nest answers again.

const desktop = window.nestDesktop

// A nest on another machine is restarted there, not from here.
if (desktop?.remote) {
  document.getElementById('restart').hidden = true
  document.querySelector('h1').textContent = '连不上 nest'
  document.querySelector('p').textContent = '那台电脑可能关机了、睡着了，或者不在同一个网络里。连上后这个窗口会自动恢复。'
}

document.getElementById('logs').addEventListener('click', () => desktop?.openLogsFolder())

document.getElementById('restart').addEventListener('click', (event) => {
  event.currentTarget.disabled = true
  desktop?.restartNest()
})
