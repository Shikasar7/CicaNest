'use strict'

// nest's live events (/api/events, Server-Sent Events) for the main process: the desk pets react to what
// their companions say. Local mode signs in with the token from data\runtime.json, remote mode with the
// session's device cookie. Reconnects by itself; stop() ends it.

const http = require('node:http')
const { net, session } = require('electron')
const log = require('./log')

const RETRY_MIN_MS = 2000
const RETRY_MAX_MS = 30_000

class EventStream {
  /** onEvent(kind, data) for each event; onUp(bool) when the stream opens or drops. */
  constructor({ config, nest, onEvent, onUp = () => {} }) {
    this.config = config
    this.nest = nest
    this.onEvent = onEvent
    this.onUp = onUp
    this.request = null
    this.timer = null
    this.retry = RETRY_MIN_MS
    this.stopped = true
    this.buffer = ''
  }

  start() {
    if (!this.stopped) return
    this.stopped = false
    this.open()
  }

  stop() {
    this.stopped = true
    clearTimeout(this.timer)
    try {
      this.request?.abort?.()
      this.request?.destroy?.()
    } catch {
      // already closed
    }
    this.request = null
  }

  later() {
    if (this.stopped) return
    this.onUp(false)
    clearTimeout(this.timer)
    this.timer = setTimeout(() => this.open(), this.retry)
    this.retry = Math.min(this.retry * 2, RETRY_MAX_MS)
  }

  feed(chunk) {
    this.buffer += chunk
    let end
    while ((end = this.buffer.indexOf('\n\n')) >= 0) {
      const block = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + 2)
      let kind = 'message'
      const data = []
      for (const line of block.split('\n')) {
        if (line.startsWith(':')) continue
        if (line.startsWith('event:')) kind = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (!data.length) continue
      try {
        this.onEvent(kind, JSON.parse(data.join('\n')))
      } catch (err) {
        log.warn('event stream: bad event', err.message)
      }
    }
  }

  opened(status) {
    if (status !== 200) {
      log.info(`event stream: HTTP ${status}`)
      this.later()
      return false
    }
    this.retry = RETRY_MIN_MS
    this.buffer = ''
    this.onUp(true)
    return true
  }

  open() {
    if (this.stopped) return
    if (this.config.remote) this.openRemote()
    else this.openLocal()
  }

  openRemote() {
    const req = net.request({
      url: `${this.config.origin}/api/events`,
      session: session.defaultSession,
      useSessionCookies: true,
    })
    req.setHeader('accept', 'text/event-stream')
    req.on('response', (res) => {
      if (!this.opened(res.statusCode)) return
      res.on('data', (chunk) => this.feed(chunk.toString('utf8')))
      res.on('end', () => this.later())
      res.on('error', () => this.later())
    })
    req.on('error', () => this.later())
    req.end()
    this.request = req
  }

  openLocal() {
    const token = this.nest.readRuntime()?.token
    if (!token) {
      this.later()
      return
    }
    const req = http.request(
      {
        host: '127.0.0.1',
        port: this.config.port,
        path: '/api/events',
        headers: { accept: 'text/event-stream', authorization: `Bearer ${token}` },
        agent: false,
      },
      (res) => {
        if (!this.opened(res.statusCode)) {
          res.resume()
          return
        }
        res.setEncoding('utf8')
        res.on('data', (chunk) => this.feed(chunk))
        res.on('end', () => this.later())
        res.on('error', () => this.later())
      },
    )
    req.on('error', () => this.later())
    req.end()
    this.request = req
  }
}

module.exports = { EventStream }
