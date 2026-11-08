'use strict'

// The launch sequence shown in the splash (or run silently from the tray / at login).
// Pure logic: it talks to nest and emits snapshots; splash.js draws them, main.js reacts to 'finish'.
//
// Steps: [停止 nest 后台 (restart only)] · 启动 nest 后台 · 登录控制台 · 连接 SnowLuma · 等待 QQ 上线
// A failed step can be retried; the retry resumes at that step.

const { EventEmitter } = require('node:events')
const { NestError, sleep, detailOf } = require('./nest')
const log = require('./log')

const QQ_WAIT_MS = 20_000
const POLL_MS = 1000
const POLL_WAITING_MS = 1500

// The three bots, in this order. An account is matched to a glyph by its name.
const KNOWN_BOTS = [
  { char: '鲸', hue: '#2F7FD0' },
  { char: '马', hue: '#E2557F' },
  { char: '龙', hue: '#1E9C82' },
]

const isOnline = (account) => account?.status?.state === 'connected'

function glyphsFor(accounts) {
  const used = new Set()
  const glyphs = []
  for (const bot of KNOWN_BOTS) {
    const match = accounts.find((a) => !used.has(a.id) && String(a.name || '').includes(bot.char))
    if (!match) continue
    used.add(match.id)
    glyphs.push({ key: match.id, char: bot.char, hue: bot.hue, name: match.name || match.self_id, online: isOnline(match) })
  }
  const freeHues = KNOWN_BOTS.map((b) => b.hue).filter((h) => !glyphs.some((g) => g.hue === h))
  let n = 0
  for (const account of accounts) {
    if (used.has(account.id)) continue
    const name = String(account.name || account.self_id || '?')
    const hue = freeHues[n] ?? KNOWN_BOTS[n % KNOWN_BOTS.length].hue
    n += 1
    glyphs.push({ key: account.id, char: Array.from(name)[0], hue, name, online: isOnline(account) })
  }
  return glyphs.slice(0, 5)
}

const PLACEHOLDER_GLYPHS = KNOWN_BOTS.map((b) => ({ key: b.char, char: b.char, hue: b.hue, name: '', online: false }))

const STEP_LABELS = {
  stop: '停止 nest 后台',
  backend: '启动 nest 后台',
  login: '登录控制台',
  snowluma: '连接 SnowLuma',
  qq: '等待 QQ 上线',
}

function networkMessage(err) {
  if (err?.code === 'ECONNREFUSED' || err?.code === 'ECONNRESET') return '连不上 nest 后台，它可能刚刚退出了。'
  if (err?.code === 'ETIMEDOUT') return 'nest 后台没有及时回应。'
  return null
}

class Launch extends EventEmitter {
  /**
   * @param {import('./nest').Nest} nest
   * @param {{ restart?: boolean, onLogin: (token: string) => Promise<void> }} options
   */
  constructor(nest, { restart = false, onLogin }) {
    super()
    this.nest = nest
    this.restart = restart
    this.onLogin = onLogin
    const ids = restart ? ['stop', 'backend', 'login', 'snowluma', 'qq'] : ['backend', 'login', 'snowluma', 'qq']
    this.steps = ids.map((id) => ({ id, label: STEP_LABELS[id], state: 'pending', detail: '' }))
    this.phase = 'running' // running · waiting (QQ) · failed · done
    this.glyphs = PLACEHOLDER_GLYPHS
    this.qqWaiting = false
    this.message = null
    this.actions = []
    this.system = null // GET /api/system, when this nest has it
    this.finished = null
    this.controller = null
    this.resumeAt = 0
  }

  snapshot() {
    const titles = {
      running: this.restart ? '正在重启…' : '正在启动…',
      waiting: '等 QQ 登录',
      failed: '没有启动完成',
      done: '准备好了',
    }
    return {
      mode: this.restart ? 'restart' : 'launch',
      phase: this.phase,
      title: titles[this.phase],
      steps: this.steps.map((s) => ({ ...s })),
      glyphs: this.glyphs.map((g) => ({ ...g })),
      qqWaiting: this.qqWaiting,
      message: this.message,
      actions: this.actions,
    }
  }

  changed() {
    this.emit('change', this.snapshot())
  }

  set(step, state, detail = '') {
    step.state = state
    step.detail = detail
    this.changed()
  }

  start() {
    this.attempt()
  }

  async attempt() {
    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    const { signal } = controller
    this.phase = 'running'
    this.message = null
    this.actions = []
    for (const step of this.steps.slice(this.resumeAt)) {
      step.state = 'pending'
      step.detail = ''
    }
    this.changed()

    let current = null
    try {
      for (let i = this.resumeAt; i < this.steps.length; i += 1) {
        current = this.steps[i]
        this.resumeAt = i
        await this[current.id](current, signal)
        if (signal.aborted) return
      }
      this.qqWaiting = false
      this.phase = 'done'
      this.message = null
      this.actions = []
      this.changed()
      // Let the last check mark land before the splash fades.
      await sleep(520, signal)
      this.finish('done')
    } catch (err) {
      if (signal.aborted) return
      log.warn(`launch step ${current?.id} failed:`, err)
      this.qqWaiting = false
      const text = err instanceof NestError ? err.message : networkMessage(err) || `出错了：${err?.message || err}`
      if (current) {
        current.state = 'failed'
        current.detail = err?.short || '失败'
      }
      this.phase = 'failed'
      this.message = { tone: 'error', text, link: { id: 'open-logs', label: '打开日志文件夹' } }
      const backendUp = current && (current.id === 'snowluma' || current.id === 'qq')
      this.actions = [
        ...(backendUp ? [{ id: 'enter-console', label: '先进入控制台', kind: 'ghost' }] : []),
        { id: 'retry', label: '重试', kind: 'primary' },
      ]
      this.changed()
      this.emit('failed', text)
    }
  }

  // --- user actions -------------------------------------------------------------------------

  retry() {
    if (this.finished || this.phase !== 'failed') return
    this.attempt()
  }

  enterConsole() {
    if (this.finished) return
    this.finish('console')
  }

  async openQQ() {
    if (this.finished) return
    try {
      const res = await this.nest.api('POST', '/api/system/qq/open', undefined, { timeout: 20_000 })
      if (res.status === 200) {
        this.message = { tone: 'info', text: '已经打开 QQ。登录以后，这里会自动继续。' }
        this.actions = [{ id: 'enter-console', label: '先进入控制台', kind: 'ghost' }]
      } else if (res.status === 404) {
        this.message = { tone: 'warn', text: '这个版本的 nest 还不能替你打开 QQ，请手动打开并登录。' }
      } else {
        this.message = { tone: 'error', text: `打开 QQ 失败：${detailOf(res)}` }
      }
    } catch (err) {
      this.message = { tone: 'error', text: networkMessage(err) || `打开 QQ 失败：${err.message}` }
    }
    this.changed()
  }

  cancel() {
    this.controller?.abort()
    this.finish('cancelled')
  }

  finish(result) {
    if (this.finished) return
    this.finished = result
    this.controller?.abort()
    this.emit('finish', result)
  }

  // --- steps --------------------------------------------------------------------------------

  async stop(step) {
    this.set(step, 'running', '停止中…')
    const { wasRunning } = await this.nest.stop()
    this.set(step, 'done', wasRunning ? '已停止' : '本来就没在运行')
  }

  async backend(step, signal) {
    this.set(step, 'running', '检查中…')
    const result = await this.nest.start({
      signal,
      onWait: (seconds) => {
        const detail = seconds >= 2 ? `启动中 · ${seconds} 秒` : '启动中…'
        if (step.detail !== detail) this.set(step, 'running', detail)
      },
    })
    this.set(step, 'done', result.already ? '已在运行' : `已启动 · ${result.seconds.toFixed(1)} 秒`)
  }

  async login(step) {
    this.set(step, 'running', '')
    const runtime = this.nest.readRuntime()
    if (!runtime) {
      throw new NestError('nest 在运行，但读不到 data\\runtime.json，没法登录控制台。重启 nest 一般能解决。', {
        short: '读不到凭据',
      })
    }
    await this.onLogin(runtime.token)
    this.set(step, 'done', '已登录')
  }

  async snowluma(step) {
    this.set(step, 'running', '检查中…')
    // The first look at the process list can take a few seconds on Windows.
    const sys = await this.nest.api('GET', '/api/system', undefined, { timeout: 20_000 })
    if (sys.status === 200 && sys.json) {
      this.system = sys.json
      const snow = sys.json.snowluma || {}
      if (!snow.configured) return this.set(step, 'skipped', '未设置，跳过')
      if (snow.running) return this.set(step, 'done', '已在运行')
    } else if (sys.status !== 404) {
      throw new NestError(`读不到 SnowLuma 的状态：${detailOf(sys)}`, { short: '读不到状态' })
    }
    this.set(step, 'running', '启动中…')
    const res = await this.nest.api('POST', '/api/system/snowluma/start', undefined, { timeout: 30_000 })
    if (res.status === 404) return this.set(step, 'skipped', '这个版本不支持，跳过')
    if (res.status === 400) {
      log.info(`snowluma start refused: ${detailOf(res)}`)
      return this.set(step, 'skipped', '未设置，跳过')
    }
    if (res.status !== 200) throw new NestError(`SnowLuma 没能启动：${detailOf(res)}`, { short: '没有启动' })
    this.set(step, 'done', res.json?.started === false ? '已在运行' : '已启动')
  }

  async qq(step, signal) {
    this.set(step, 'running', '')
    this.qqWaiting = true
    this.actions = [{ id: 'enter-console', label: '先进入控制台', kind: 'ghost' }]
    const deadline = Date.now() + QQ_WAIT_MS
    for (;;) {
      const res = await this.nest.api('GET', '/api/accounts')
      if (signal.aborted) return
      if (res.status !== 200 || !Array.isArray(res.json)) {
        throw new NestError(`读不到账号状态：${detailOf(res)}`, { short: '读不到账号' })
      }
      const accounts = res.json.filter((a) => a.enabled !== false)
      this.glyphs = glyphsFor(accounts)
      if (accounts.length === 0) {
        this.qqWaiting = false
        return this.set(step, 'skipped', '还没有账号')
      }
      const offline = accounts.filter((a) => !isOnline(a))
      if (offline.length === 0) {
        this.qqWaiting = false
        return this.set(step, 'done', accounts.length === 1 ? '在线' : `${accounts.length} 个号都在线`)
      }
      const detail = `${accounts.length - offline.length}/${accounts.length} 在线`
      if (this.phase === 'running' && Date.now() > deadline) {
        this.phase = 'waiting'
        const names = offline.slice(0, 3).map((a) => a.name || a.self_id)
        const more = offline.length > 3 ? ' 等' : ''
        this.message = { tone: 'warn', text: `还有 ${offline.length} 个号没上线：${names.join('、')}${more}` }
        const canOpenQQ = Boolean(this.system?.qq?.configured)
        this.actions = [
          { id: 'enter-console', label: '先进入控制台', kind: canOpenQQ ? 'ghost' : 'primary' },
          ...(canOpenQQ ? [{ id: 'open-qq', label: '打开 QQ', kind: 'primary' }] : []),
        ]
        log.info(`still waiting for ${offline.length} account(s)`)
      }
      if (this.phase === 'waiting') {
        // Keep the count in the message current while the user logs in.
        if (this.message?.tone === 'warn' && this.message.text.startsWith('还有 ')) {
          const names = offline.slice(0, 3).map((a) => a.name || a.self_id)
          const more = offline.length > 3 ? ' 等' : ''
          this.message = { tone: 'warn', text: `还有 ${offline.length} 个号没上线：${names.join('、')}${more}` }
        }
        this.set(step, 'warn', detail)
      } else {
        this.set(step, 'running', detail)
      }
      await sleep(this.phase === 'waiting' ? POLL_WAITING_MS : POLL_MS, signal)
    }
  }
}

module.exports = { Launch, glyphsFor, KNOWN_BOTS }
