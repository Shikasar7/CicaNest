'use strict'

// The page shown while nest does not answer. The main process reloads the console by itself once it does again.
// A nest on another computer: why it cannot be reached, what to check there, and the ways out (try again, another
// address, or nest on this computer when it came with the app).

const desktop = window.nestDesktop
const byId = (id) => document.getElementById(id)

async function explain() {
  const why = await desktop.whyOffline()
  byId('title').textContent = why.title
  byId('text').textContent = why.text
  byId('steps').replaceChildren(
    ...why.steps.map((step) => {
      const item = document.createElement('li')
      item.textContent = step
      return item
    }),
  )
  byId('steps').hidden = why.steps.length === 0
  byId('here').hidden = !why.canRunHere
  byId('note').textContent = '连上后这个窗口会自动恢复。'
}

// A nest on another machine is restarted there, not from here.
if (desktop?.remote) {
  byId('restart').hidden = true
  byId('logs').hidden = true
  byId('retry').hidden = false
  byId('change').hidden = false
  byId('title').textContent = '连不上 nest'
  byId('text').textContent = '正在看是哪里的问题……'
  explain().catch(() => {
    byId('text').textContent = '那台电脑可能关机了、睡着了，或者不在同一个网络里。连上后这个窗口会自动恢复。'
  })
}

byId('retry').addEventListener('click', () => desktop?.retry())
byId('change').addEventListener('click', () => desktop?.openConnection())
byId('logs').addEventListener('click', () => desktop?.openLogsFolder())

byId('use-here').addEventListener('click', async (event) => {
  const button = event.currentTarget
  button.disabled = true
  const done = await desktop.useThisComputer().catch((err) => ({ ok: false, error: err.message }))
  if (done.ok) return
  button.disabled = false
  byId('note').textContent = done.error
})

byId('restart').addEventListener('click', (event) => {
  event.currentTarget.disabled = true
  desktop?.restartNest()
})
