'use strict'

// Talking to a nest that runs elsewhere (remote mode, see config.js) from the main process: the same
// health() and api() the tray uses with a local nest, but signed in with the device cookie the console got
// when you entered the password (Electron's default session keeps it). Nothing here holds a token.

const { net, session } = require('electron')
const { NestError } = require('./nest')
const { explain, kindOf } = require('./why')

const DEFAULT_TIMEOUT = 8000

/** One request with the session's cookies. Resolves { status, json, text }; rejects on network errors. */
function request(origin, method, pathname, { body, timeout = DEFAULT_TIMEOUT, cookies = true } = {}) {
  return new Promise((resolve, reject) => {
    const req = net.request({
      method,
      url: `${origin}${pathname}`,
      session: session.defaultSession,
      useSessionCookies: cookies,
      redirect: 'error',
    })
    req.setHeader('accept', 'application/json')
    // nest refuses a write without its own Origin when it comes with a device cookie.
    if (method !== 'GET') req.setHeader('origin', origin)
    const timer = setTimeout(() => {
      req.abort()
      reject(Object.assign(new Error('请求超时'), { code: 'ETIMEDOUT' }))
    }, timeout)
    req.on('response', (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('error', (err) => {
        clearTimeout(timer)
        reject(err)
      })
      res.on('end', () => {
        clearTimeout(timer)
        const text = Buffer.concat(chunks).toString('utf8')
        let json = null
        try {
          json = text ? JSON.parse(text) : null
        } catch {
          json = null
        }
        resolve({ status: res.statusCode, json, text })
      })
    })
    req.on('error', (err) => {
      clearTimeout(timer)
      reject(err)
    })
    if (body !== undefined) {
      req.setHeader('content-type', 'application/json')
      req.write(JSON.stringify(body))
    }
    req.end()
  })
}

/** Whether `origin` answers as a nest (the health endpoint is public). Its health, or null. */
async function probe(origin, timeout = 4000) {
  try {
    const res = await request(origin, 'GET', '/api/health', { timeout, cookies: false })
    return res.status === 200 && res.json?.app === 'nest' ? res.json : null
  } catch {
    return null
  }
}

/** Why `origin` cannot be reached, in words (why.js); null when it can. */
async function whyNot(origin, timeout = 5000) {
  let seen
  try {
    const res = await request(origin, 'GET', '/api/health', { timeout, cookies: false })
    if (res.status === 200 && res.json?.app === 'nest') return null
    seen = { status: res.status }
  } catch (error) {
    seen = { error }
  }
  return explain(kindOf(seen), origin)
}

/** Which of these addresses to use: the first that answers, in their order (all are asked at once); the first
 * one when none does. `ask` is probe, replaced in tests. */
async function pickOrigin(origins, ask = probe) {
  if (origins.length < 2) return origins[0]
  const answers = origins.map((origin) => ask(origin))
  for (let n = 0; n < origins.length; n += 1) if (await answers[n]) return origins[n]
  return origins[0]
}

class RemoteNest {
  constructor(config) {
    this.config = config
  }

  health() {
    return probe(this.config.origin, 2500)
  }

  // Never a local runtime file: the main process cannot sign the console in by itself here.
  readRuntime() {
    return null
  }

  async api(method, pathname, body, { timeout = DEFAULT_TIMEOUT } = {}) {
    const res = await request(this.config.origin, method, pathname, { body, timeout })
    if (res.status === 401) throw new NestError('还没有登录：在聊天窗口里输入远程访问密码。', { code: 'login' })
    return res
  }
}

module.exports = { RemoteNest, pickOrigin, probe, whyNot }
