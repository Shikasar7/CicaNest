'use strict'

// The 连接设置 window, and the first thing an installed app shows: nest on this computer, or one elsewhere (try its
// address first). Saving reopens the app.

const bridge = window.nestConnect
const byId = (id) => document.getElementById(id)
const address = byId('address')
const other = byId('other')
const result = byId('result')
const radios = [...document.querySelectorAll('input[name="mode"]')]
let now = null
let anyway = false // the address did not answer, and the user was told

function show(text, kind = '') {
  result.textContent = text
  result.className = `result ${kind}`
}

const picked = () => radios.find((r) => r.checked)?.value

/** The choice the app already runs with, if any. */
const current = () => (now.undecided ? null : now.remote ? 'remote' : 'local')

function draw() {
  const mode = picked()
  byId('local').hidden = mode !== 'local'
  byId('remote').hidden = mode !== 'remote'
  byId('test').hidden = mode !== 'remote'
  // Already running here: nothing to save.
  byId('save').hidden = mode === 'local' && current() === 'local'
  byId('save').textContent = now.undecided ? '开始使用' : '保存并重新打开'
  anyway = false
  show('')
}

async function busy(work) {
  const controls = [byId('test'), byId('save'), ...radios]
  controls.forEach((c) => (c.disabled = true))
  try {
    await work()
  } finally {
    controls.forEach((c) => (c.disabled = false))
  }
}

async function load() {
  now = await bridge.current()
  byId('now').textContent = now.undecided
    ? ''
    : now.remote
      ? `现在连的是 ${now.origin}。`
      : '现在用的是这台电脑上的 nest（由这个应用启动和停止）。'
  byId('now').hidden = now.undecided
  byId('home').textContent = now.home
  byId('choice-local').hidden = !now.canRunHere
  address.value = now.saved || ''
  other.value = now.other || ''
  other.closest('details').open = Boolean(now.other)
  const first = current() ?? (now.canRunHere ? 'local' : 'remote')
  radios.forEach((r) => (r.checked = r.value === first))
  draw()
  if (now.fromEnv) show('地址由环境变量 NEST_REMOTE_URL 指定，这里保存的不会生效。', 'bad')
  if (first === 'remote') address.focus()
  else byId('save').focus()
}

radios.forEach((r) => r.addEventListener('change', draw))

byId('test').addEventListener('click', () =>
  busy(async () => {
    show('正在试……')
    const asked = [address.value, other.value].filter((value) => value.trim())
    const found = await Promise.all((asked.length ? asked : ['']).map((value) => bridge.test(value)))
    const said = found.map((f) => (f.ok ? `连上了：${f.origin}（nest ${f.version || ''}）` : f.error))
    show(said.join('\n\n'), found.some((f) => f.ok) ? 'ok' : 'bad')
  }),
)

byId('save').addEventListener('click', () =>
  busy(async () => {
    show(picked() === 'remote' ? '正在连……' : '')
    const saved = await bridge.save({ mode: picked(), address: address.value, other: other.value, anyway })
    if (saved.ok) return
    show(saved.error || '', 'bad')
    // Off now, but the right address: a second click keeps it all the same.
    anyway = Boolean(saved.unreachable)
    if (anyway) byId('save').textContent = '还是用这个地址'
  }),
)

for (const field of [address, other]) {
  field.addEventListener('input', () => {
    if (anyway) draw()
  })
}

address.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') byId('test').click()
})

load()
