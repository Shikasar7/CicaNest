'use strict'

// One GET for update.js: no cookies, the proxy Windows is set to (the default session goes direct, see
// local-name.js), redirects handed back instead of followed.

const { net, session } = require('electron')

const STALL = 60_000 // nothing coming any more

function get(url, { timeout }) {
  return new Promise((resolve, reject) => {
    const req = net.request({
      url,
      session: session.fromPartition('nest-updates'), // in memory only
      credentials: 'omit',
      redirect: 'manual',
      cache: 'no-store',
    })
    const slow = () => {
      reject(Object.assign(new Error('请求超时'), { code: 'ETIMEDOUT' }))
      req.abort()
    }
    let timer = setTimeout(slow, timeout)
    req.on('redirect', (status, _method, location) => {
      clearTimeout(timer)
      resolve({ status, location })
      req.abort()
    })
    req.on('response', (res) => {
      clearTimeout(timer)
      async function* body() {
        const late = new Error('请求超时')
        late.code = 'ETIMEDOUT'
        const wait = () => {
          clearTimeout(timer)
          timer = setTimeout(() => res.destroy(late), STALL)
        }
        try {
          wait()
          for await (const chunk of res) {
            wait()
            yield chunk
          }
        } finally {
          clearTimeout(timer)
        }
      }
      resolve({
        status: res.statusCode,
        body: body(),
        close: () => {
          clearTimeout(timer)
          req.abort()
        },
      })
    })
    req.on('error', (err) => {
      clearTimeout(timer)
      reject(err)
    })
    req.end()
  })
}

module.exports = { get }
