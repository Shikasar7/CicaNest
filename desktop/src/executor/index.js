'use strict'

// Windows 执行端: asks nest for the steps its companions want to take on this PC
// (a long poll), judges each by the rules kept here (rules.js), asks the user where needed (the confirm
// window), does it (tools.js) and hands back what came of it. nest never connects to this PC; this asks.
// Free of Electron (what it needs is handed in), so it is tested with node --test.
//
// What keeps it safe:
// - "这次同类不再问" is kept here per companion, task and scope, for 15 minutes and 20 uses at most: a nest
//   that went wrong can at most fail to end it early, never stretch it to other tasks or forever.
// - A routine's step (unattended) is judged as if 完全允许 were off: nobody is there.
// - Each step is done once: the ids taken are kept (in a file, across restarts of this app), and a step
//   nest hands over again is not done again.
// - Switched off, nothing more is done: checked again after the window, and running commands end.

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const crypto = require('node:crypto')
const { decide, forbiddenRoots, HIDDEN_CHARS } = require('./rules')
const { prepare, stopCommands } = require('./tools')

const POLL_TIMEOUT_MS = 40_000 // nest holds an ask up to 25 seconds
const RETRY_MIN_MS = 3000
const RETRY_MAX_MS = 60_000
const ANOTHER_PC_MS = 60_000
const GRANTS_KEPT = 50
const GRANT_MS = 15 * 60_000
const GRANT_USES = 20
const HANDBACK_TRIES = 4
const HANDBACK_TIMEOUT_MS = 30_000
const HANDBACK_PAUSE_MS = 2000
const UNSENT_KEPT = 50
const TAKEN_KEPT = 300
const NAME_MAX = 20

const HIDDEN_ALL = new RegExp(HIDDEN_CHARS.source, 'g')

const RESTARTED =
  '桌面端在做这一步的时候重启了，不知道做完没有，没有再做一次。先看看现在的情况（比如读一下文件）再决定要不要重做。'

/** A companion's name as nest gives it: short, one line, nothing hidden. */
function nameOf(given) {
  const name = String(given || '')
    .replace(HIDDEN_ALL, '')
    .trim()
  return (name || '伙伴').slice(0, NAME_MAX)
}

class Executor {
  /**
   * api(method, path, body, { timeout }) -> { status, json }: nest's API, signed in.
   * ask(data) -> 'once' | 'kind' | 'deny' | 'busy': the confirm window.
   * settings() -> { enabled, allowAll, openForbidden, folders: [{ path, readOnly, freeChange }], key }.
   * extraForbidden: more folders nothing may touch (this app's data, a local nest's).
   * system: what needs Electron — trash(path), open({ path } | { url }) -> error text or ''.
   * stepsFile: where the steps taken are kept across restarts (none: kept in memory only).
   * onStatus(state): 'off' | 'connecting' | 'online' | 'another' | 'login'.
   * onStop(): switched off (the confirm windows are closed as a no).
   */
  constructor({
    api,
    ask,
    settings,
    system = null,
    extraForbidden = [],
    device = os.hostname(),
    stepsFile = null,
    onStatus = () => {},
    onStop = () => {},
    clock = Date.now,
    log = console,
  }) {
    this.api = api
    this.system = system
    this.ask = ask
    this.settings = settings
    this.forbidden = forbiddenRoots(process.env, extraForbidden)
    this.device = device.slice(0, 80)
    this.stepsFile = stepsFile
    this.onStatus = onStatus
    this.onStop = onStop
    this.clock = clock
    this.log = log
    this.state = 'off'
    this.running = false
    this.timer = null
    this.retry = RETRY_MIN_MS
    this.grants = new Map() // companion|task|scope -> Map of kind -> { until, left }
    this.unsent = new Map() // step id -> what came of it, not yet taken by nest
    this.taken = new Map() // step id -> when it was taken (done, or being done)
    this.doing = new Set() // step ids being done right now
    this.decided = new Map() // step id -> what the user said in the window, for nest's record of it
    this.sending = new Set()
    this.handbackPause = HANDBACK_PAUSE_MS
    this.fresh = true
    this.loadSteps()
  }

  // ---- the steps taken, across restarts ----------------------------------------------------------------

  loadSteps() {
    if (!this.stepsFile) return
    try {
      const saved = JSON.parse(fs.readFileSync(this.stepsFile, 'utf8'))
      for (const [id, at] of Object.entries(saved.taken ?? {})) this.taken.set(id, Number(at) || 0)
      for (const [id, text] of Object.entries(saved.unsent ?? {})) this.unsent.set(id, String(text))
    } catch {
      // none yet, or unreadable: start empty
    }
  }

  saveSteps() {
    while (this.taken.size > TAKEN_KEPT) this.taken.delete(this.taken.keys().next().value)
    while (this.unsent.size > UNSENT_KEPT) this.unsent.delete(this.unsent.keys().next().value)
    if (!this.stepsFile) return
    try {
      fs.mkdirSync(path.dirname(this.stepsFile), { recursive: true })
      const temporary = `${this.stepsFile}.new`
      const data = { taken: Object.fromEntries(this.taken), unsent: Object.fromEntries(this.unsent) }
      fs.writeFileSync(temporary, JSON.stringify(data), 'utf8')
      fs.renameSync(temporary, this.stepsFile)
    } catch (err) {
      this.log.warn?.('executor: could not keep the steps taken', err.message)
    }
  }

  setState(state) {
    if (state === this.state) return
    this.state = state
    this.onStatus(state)
  }

  start() {
    if (this.running || !this.settings().enabled) {
      if (!this.settings().enabled) this.setState('off')
      return
    }
    this.running = true
    this.fresh = true
    this.setState('connecting')
    this.loop()
  }

  /** Switched off (or quitting): no more asking nest, no more windows, the running commands end. */
  stop() {
    this.running = false
    clearTimeout(this.timer)
    this.grants.clear()
    stopCommands()
    this.onStop()
    this.setState('off')
  }

  /** The settings changed: tell nest the folders (for her hint), or stop / start. */
  changed() {
    if (!this.settings().enabled) return this.stop()
    if (!this.running) return this.start()
    return this.report()
  }

  key() {
    return String(this.settings().key || '')
  }

  async report() {
    const settings = this.settings()
    // free_change only when on: a nest from before it refuses a field it does not know.
    const folders = settings.folders.map((f) => ({
      path: f.path,
      read_only: Boolean(f.readOnly),
      ...(!f.readOnly && f.freeChange === true ? { free_change: true } : {}),
    }))
    try {
      await this.api('PUT', '/api/executor/folders', {
        device: this.device,
        key: this.key(),
        folders,
        allow_all: Boolean(settings.allowAll),
      })
    } catch (err) {
      this.log.warn?.('executor: could not report folders', err.message)
    }
  }

  later(ms) {
    if (!this.running) return
    clearTimeout(this.timer)
    this.timer = setTimeout(() => this.loop(), ms)
  }

  async loop() {
    if (!this.running) return
    let res
    try {
      const query =
        `device=${encodeURIComponent(this.device)}&key=${encodeURIComponent(this.key())}` +
        (this.fresh ? '&fresh=true' : '')
      res = await this.api('GET', `/api/executor/requests?${query}`, undefined, { timeout: POLL_TIMEOUT_MS })
    } catch (err) {
      if (err.code === 'login') this.setState('login')
      else this.setState('connecting')
      this.later(this.retry)
      this.retry = Math.min(this.retry * 2, RETRY_MAX_MS)
      return
    }
    if (res.status === 409) {
      this.setState('another')
      this.later(ANOTHER_PC_MS)
      return
    }
    if (res.status === 401) {
      this.setState('login')
      this.later(RETRY_MAX_MS)
      return
    }
    if (res.status !== 200) {
      this.setState('connecting')
      this.later(this.retry)
      this.retry = Math.min(this.retry * 2, RETRY_MAX_MS)
      return
    }
    this.retry = RETRY_MIN_MS
    // Just started, or nest no longer knows the folders (it restarted since they were told): tell them.
    if (this.fresh || res.json?.folders_known === false) {
      this.fresh = false
      this.report()
    }
    this.setState('online')
    for (const id of this.unsent.keys()) this.handBack(id)
    for (const step of res.json?.requests ?? []) {
      this.handle(step).catch((err) => this.log.warn?.('executor: step failed', err.message))
    }
    this.later(0)
  }

  /** A step from nest: done once. Handed over again (nest does that when this app reconnects), it is not. */
  async handle(step) {
    const id = String(step?.id ?? '')
    if (!id) return
    if (this.unsent.has(id)) return this.handBack(id) // done: what came of it, again
    if (this.doing.has(id)) return // being done right now
    if (this.taken.has(id)) {
      // Taken before this app restarted, and nothing was kept of how it ended.
      this.unsent.set(id, RESTARTED)
      this.saveSteps()
      return this.handBack(id)
    }
    this.taken.set(id, this.clock())
    this.doing.add(id)
    this.saveSteps()
    let text
    try {
      text = await this.take(step)
    } finally {
      this.doing.delete(id)
    }
    this.unsent.set(id, text)
    this.saveSteps()
    await this.handBack(id)
  }

  /**
   * What came of a step, to nest. Tried a few times (the app's requests share a handful of connections
   * with its windows' live updates, so one can wait its turn past a short timeout); still not through, it
   * is kept and tried again after the next poll that gets through. Without this nest waited for it in
   * vain, and she with it, for 21 minutes.
   */
  async handBack(id) {
    if (this.sending.has(id) || !this.unsent.has(id)) return
    this.sending.add(id)
    try {
      for (let attempt = 1; attempt <= HANDBACK_TRIES; attempt += 1) {
        try {
          // Any answer from nest settles it: taken, or no longer waited for (it timed out, nest restarted).
          const result = { device: this.device, key: this.key(), text: this.unsent.get(id) }
          const where = `/api/executor/requests/${encodeURIComponent(id)}`
          const decided = this.decided.get(id)
          const told = decided ? { ...result, decided } : result
          const res = await this.api('POST', where, told, { timeout: HANDBACK_TIMEOUT_MS })
          // A nest older than this app does not know `decided`: without it, then.
          if (decided && res?.status === 422) await this.api('POST', where, result, { timeout: HANDBACK_TIMEOUT_MS })
          this.decided.delete(id)
          this.unsent.delete(id)
          this.saveSteps()
          return
        } catch (err) {
          this.log.warn?.(`executor: could not hand back a result (try ${attempt})`, err.message)
          if (attempt < HANDBACK_TRIES)
            await new Promise((resolve) => setTimeout(resolve, this.handbackPause * attempt))
        }
      }
    } finally {
      this.sending.delete(id)
    }
  }

  /** One step: judged here, asked about if needed, done. What she is told. */
  async take(step) {
    const settings = this.settings()
    if (!settings.enabled) return '对方把「电脑上做事」关了，这一步没做。'
    // A routine's task (定时任务): nobody is there, so 完全允许 does not reach it.
    const decision = decide(step.tool, step.given, {
      folders: settings.folders,
      forbidden: this.forbidden,
      allowAll: Boolean(settings.allowAll) && !step.unattended,
      guard: !(settings.allowAll && settings.openForbidden),
    })
    const botName = nameOf(step.bot_name)
    this.log.info?.(`executor: ${botName} ${step.tool} -> ${decision.level}`)
    if (decision.level === 'deny') return `这一步不能做：${decision.why}`
    // Prepared before asking: the window shows what will really be done (the diff, the whole command).
    const prepared = prepare(step.tool, decision, step.given, this.system)
    if (prepared.error) return `没做：${prepared.error}`
    if (decision.level === 'confirm' && step.unattended) {
      return '这是定时任务，对方不在电脑前，这一步要对方点头，没做。别再试了，在汇报里说一声卡在这。'
    }
    if (decision.level === 'confirm' && !this.granted(step, decision.kind)) {
      const answer = await this.ask({
        botName,
        action: prepared.action,
        target: prepared.target,
        detail: prepared.detail || '',
        detailKind: prepared.detailKind || '',
        why: decision.why,
        kindLabel: decision.kind ? decision.kindLabel : '',
      })
      if (answer === 'busy') return '电脑那边已经有好几件事在等对方点头了，这一步先没做。等前面的有了回答再试。'
      this.decided.set(String(step.id), answer === 'once' || answer === 'kind' ? answer : 'denied')
      if (answer !== 'once' && answer !== 'kind') return '对方没有允许这一步，没做。'
      // Switched off while the window was open: not done after all.
      if (!this.settings().enabled) return '对方把「电脑上做事」关了，这一步没做。'
      if (answer === 'kind' && decision.kind) this.grant(step, decision.kind)
    }
    try {
      return await prepared.run()
    } catch (err) {
      return `没做成：${err.code === 'ENOENT' ? '没有这个文件或文件夹' : err.message}`
    }
  }

  /** Which companion, task and scope a step's "同类" is kept under. */
  scopeOf(step) {
    if (!step.scope) return null
    return `${step.bot_id ?? ''}|${step.task_id ?? ''}|${step.scope}`
  }

  granted(step, kind) {
    const scope = this.scopeOf(step)
    const grant = scope && kind ? this.grants.get(scope)?.get(kind) : null
    if (!grant) return false
    if (grant.until <= this.clock() || grant.left <= 0) {
      this.grants.get(scope).delete(kind)
      return false
    }
    grant.left -= 1
    return true
  }

  grant(step, kind) {
    const scope = this.scopeOf(step)
    if (!scope || !kind) return
    if (!this.grants.has(scope)) this.grants.set(scope, new Map())
    this.grants.get(scope).set(kind, { until: this.clock() + GRANT_MS, left: GRANT_USES })
    while (this.grants.size > GRANTS_KEPT) this.grants.delete(this.grants.keys().next().value)
  }
}

/** A new key for this PC as an executor (kept in its settings): nest knows it by this, not by its name. */
function newKey() {
  return crypto.randomBytes(24).toString('hex')
}

module.exports = { Executor, newKey, nameOf, GRANT_MS, GRANT_USES }
