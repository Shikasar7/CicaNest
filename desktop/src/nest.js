'use strict'

// Talking to the nest backend from the main process: health, the token in data\runtime.json,
// authenticated API calls, and starting/stopping the background process (`python -m nest ...`).
// The token never leaves this process except as the httpOnly cookie set in main.js.

const fs = require('node:fs')
const http = require('node:http')
const { spawn } = require('node:child_process')
const log = require('./log')

const START_TIMEOUT_MS = 25_000
const STOP_TIMEOUT_MS = 15_000

class NestError extends Error {
  constructor(message, { code, short } = {}) {
    super(message)
    this.name = 'NestError'
    this.code = code
    this.short = short // a few words for the step's right-hand detail
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason)
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(signal.reason)
      },
      { once: true },
    )
  })
}

function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (err) {
    return err.code === 'EPERM'
  }
}

async function waitExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (pidAlive(pid)) {
    if (Date.now() > deadline) return false
    await sleep(200)
  }
  return true
}

// The last meaningful line a `python -m nest ...` command printed (its messages are plain Chinese).
function lastLine(output) {
  const lines = String(output || '')
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
  const line = lines.at(-1) || ''
  return line.length > 160 ? `${line.slice(0, 157)}…` : line
}

function detailOf(res) {
  const detail = res?.json?.detail
  if (typeof detail === 'string' && detail) return detail
  return `HTTP ${res?.status ?? '?'}`
}

class Nest {
  constructor(config) {
    this.config = config
    this.token = null
  }

  request(method, pathname, { token, body, timeout = 5000 } = {}) {
    return new Promise((resolve, reject) => {
      const payload = body === undefined ? null : Buffer.from(JSON.stringify(body))
      const headers = { accept: 'application/json' }
      if (token) headers.authorization = `Bearer ${token}`
      if (payload) {
        headers['content-type'] = 'application/json'
        headers['content-length'] = String(payload.length)
      } else if (method !== 'GET') {
        headers['content-length'] = '0'
      }
      const req = http.request(
        { host: '127.0.0.1', port: this.config.port, path: pathname, method, headers, agent: false, timeout },
        (res) => {
          const chunks = []
          res.on('data', (chunk) => chunks.push(chunk))
          res.on('error', reject)
          res.on('end', () => {
            const text = Buffer.concat(chunks).toString('utf8')
            let json = null
            try {
              json = text ? JSON.parse(text) : null
            } catch {
              json = null
            }
            resolve({ status: res.statusCode, json, text })
          })
        },
      )
      req.on('timeout', () => req.destroy(Object.assign(new Error('请求超时'), { code: 'ETIMEDOUT' })))
      req.on('error', reject)
      req.end(payload ?? undefined)
    })
  }

  // The health endpoint is public; it also tells nest apart from another program on the port.
  async health() {
    try {
      const res = await this.request('GET', '/api/health', { timeout: 2500 })
      return res.status === 200 && res.json?.app === 'nest' ? res.json : null
    } catch {
      return null
    }
  }

  readRuntime() {
    try {
      const info = JSON.parse(fs.readFileSync(this.config.runtimeFile, 'utf8'))
      if (!info || typeof info.token !== 'string' || !info.token) return null
      return { pid: Number(info.pid), port: Number(info.port), token: info.token, version: info.version }
    } catch {
      return null
    }
  }

  // Authenticated call. A 401 means nest restarted with a new token: re-read runtime.json once.
  async api(method, pathname, body, { timeout = 8000 } = {}) {
    let res = null
    for (let attempt = 0; attempt < 2; attempt += 1) {
      if (!this.token || attempt > 0) this.token = this.readRuntime()?.token ?? null
      if (!this.token) throw new NestError('读不到 data\\runtime.json，没法和 nest 后台通话。', { code: 'no-token' })
      res = await this.request(method, pathname, { token: this.token, body, timeout })
      if (res.status !== 401) break
    }
    return res
  }

  // `python -m nest <args>` in the nest folder, without a console window. Resolves with exit code and output.
  run(args, { timeoutMs = 60_000 } = {}) {
    return new Promise((resolve) => {
      let output = ''
      let child
      try {
        // detached: outside the job object Node puts its children in, which dies with this app;
        // `nest serve` (started by `nest start`) inherits it and would take the bots down on quit.
        child = spawn(this.config.python, ['-m', 'nest', ...args], {
          cwd: this.config.home,
          detached: true,
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
        })
      } catch (err) {
        resolve({ code: -1, output: err.message })
        return
      }
      child.unref()
      const collect = (chunk) => {
        output = (output + chunk.toString('utf8')).slice(-20_000)
      }
      child.stdout.on('data', collect)
      child.stderr.on('data', collect)
      const timer = setTimeout(() => child.kill(), timeoutMs)
      child.on('error', (err) => {
        clearTimeout(timer)
        resolve({ code: -1, output: `${output}\n${err.message}` })
      })
      child.on('close', (code) => {
        clearTimeout(timer)
        log.info(`nest ${args.join(' ')} -> ${code}: ${lastLine(output)}`)
        resolve({ code: code ?? -1, output })
      })
    })
  }

  // Make sure the backend answers. onWait(seconds) is called while waiting.
  async start({ signal, onWait } = {}) {
    if (await this.health()) return { already: true }
    if (!fs.existsSync(this.config.python)) {
      throw new NestError(`找不到运行 nest 的 Python：${this.config.python}。在 nest 文件夹里运行一次 uv sync 再试。`, {
        code: 'no-python',
        short: '找不到 Python',
      })
    }
    const began = Date.now()
    let finished = null
    this.run(['start', '--no-browser'], { timeoutMs: START_TIMEOUT_MS + 5000 }).then((result) => {
      finished = result
    })
    for (;;) {
      if (signal?.aborted) throw signal.reason
      if (finished && finished.code !== 0) {
        const why = lastLine(finished.output) || `退出码 ${finished.code}`
        throw new NestError(`nest 后台没能启动：${why}`, { code: 'start-failed', short: '没有启动' })
      }
      if ((await this.health()) && this.readRuntime()) {
        this.token = null
        return { already: false, seconds: (Date.now() - began) / 1000 }
      }
      const elapsed = Date.now() - began
      if (elapsed > START_TIMEOUT_MS) {
        throw new NestError('nest 后台 25 秒内没有响应。', { code: 'start-timeout', short: '没有响应' })
      }
      onWait?.(Math.floor(elapsed / 1000))
      await sleep(300, signal)
    }
  }

  // Stop the backend and wait until its process is gone (its QQ connections go with it).
  async stop() {
    const healthy = Boolean(await this.health())
    const runtime = this.readRuntime()
    if (healthy && runtime) {
      try {
        const res = await this.request('POST', '/api/shutdown', { token: runtime.token })
        if (res.status === 200 && (await waitExit(runtime.pid, STOP_TIMEOUT_MS))) {
          this.token = null
          log.info(`nest stopped (pid ${runtime.pid})`)
          return { wasRunning: true }
        }
        log.warn(`shutdown answered ${res.status}; falling back to nest stop`)
      } catch (err) {
        log.warn('shutdown request failed; falling back to nest stop', err)
      }
    }
    if (!healthy && !runtime) return { wasRunning: false }
    if (!fs.existsSync(this.config.python)) {
      throw new NestError('旧的 nest 进程停不下来：找不到 Python。', { code: 'no-python', short: '停不下来' })
    }
    // `nest stop` also ends a process that stopped answering but never exited.
    const result = await this.run(['stop'], { timeoutMs: 40_000 })
    if (result.code !== 0) {
      throw new NestError(lastLine(result.output) || '旧的 nest 进程停不下来。', { code: 'stop-failed', short: '停不下来' })
    }
    this.token = null
    return { wasRunning: healthy }
  }
}

module.exports = { Nest, NestError, sleep, detailOf }
