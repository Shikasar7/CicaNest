'use strict'

// The 连接设置 window: try an address, save it (the app then reopens), or go back to this machine's nest.

const bridge = window.nestConnect
const address = document.getElementById('address')
const other = document.getElementById('other')
const result = document.getElementById('result')
const buttons = ['test', 'save', 'local'].map((id) => document.getElementById(id))

function show(text, kind = '') {
  result.textContent = text
  result.className = `result ${kind}`
}

async function busy(work) {
  buttons.forEach((b) => (b.disabled = true))
  try {
    await work()
  } finally {
    buttons.forEach((b) => (b.disabled = false))
  }
}

async function load() {
  const now = await bridge.current()
  document.getElementById('now').textContent = now.remote
    ? `现在连的是 ${now.origin}。`
    : now.installed
      ? '还没有填 nest 的地址。'
      : '现在用的是这台电脑上的 nest（由这个应用启动和停止）。'
  // An installed app has no nest of its own to go back to: only an address can be taken away again.
  if (now.installed && !now.remote) document.getElementById('local').hidden = true
  else if (now.installed) document.getElementById('local').textContent = '不连了'
  if (now.fromEnv) show('地址由环境变量 NEST_REMOTE_URL 指定，这里保存的不会生效。', 'bad')
  address.value = now.saved || ''
  other.value = now.other || ''
  address.focus()
}

document.getElementById('test').addEventListener('click', () =>
  busy(async () => {
    show('正在试……')
    const asked = [address.value, other.value].filter((value) => value.trim())
    const found = await Promise.all((asked.length ? asked : ['']).map((value) => bridge.test(value)))
    const said = found.map((f) => (f.ok ? `连上了：${f.origin}（nest ${f.version || ''}）` : f.error))
    show(said.join('；'), found.every((f) => f.ok) ? 'ok' : 'bad')
  }),
)

document.getElementById('save').addEventListener('click', () =>
  busy(async () => {
    const saved = await bridge.save(address.value, other.value)
    if (!saved.ok) show(saved.error, 'bad')
  }),
)

document.getElementById('local').addEventListener('click', () => busy(() => bridge.save('')))

address.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') document.getElementById('test').click()
})

load()
