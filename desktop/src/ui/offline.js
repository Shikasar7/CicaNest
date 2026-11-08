'use strict'

// Buttons of the offline page. The main process reloads the console by itself once nest answers again.

const desktop = window.nestDesktop

document.getElementById('logs').addEventListener('click', () => desktop?.openLogsFolder())

document.getElementById('restart').addEventListener('click', (event) => {
  event.currentTarget.disabled = true
  desktop?.restartNest()
})
