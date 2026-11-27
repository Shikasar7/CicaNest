'use strict'

// A newer nest: looked for in the Releases on GitHub, downloaded when the user says so, checked against the digest
// published with it, and installed over this one by its own installer. Installed apps only (update-app.js has the
// dialogs; update-net.js the requests).
//
// The installer is not code-signed, so what makes it ours is where it comes from (https, these hosts, this repo's
// download folder for this version) and that its SHA-256 is the published one; it is checked again right before it
// runs. Anything else is deleted and said.

const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { EventEmitter } = require('node:events')

const HOSTS = ['github.com', 'api.github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com']
const MAX_HOPS = 5
const MAX_TEXT = 2 * 1024 * 1024 // an answer that is read as text
const MAX_INSTALLER = 600 * 1024 * 1024
const INSTALLER = /^nest-setup-(\d+\.\d+\.\d+)\.exe$/
// What a new version is downloaded as here: Windows' installer, or (macOS) the disk image to drag in.
const MAC = process.platform === 'darwin'
const installerName = (version) => (MAC ? `nest-${version}-arm64.dmg` : `nest-setup-${version}.exe`)
const PENDING = 'installing.json'
const PENDING_FOR = 15 * 60_000 // an installer that takes longer than this is not waited for

class UpdateError extends Error {}

/** [major, minor, patch] of "v1.2.3" or "1.2.3"; null for anything else (a pre-release is not offered). */
function parts(version) {
  const match = /^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})$/.exec(String(version ?? '').trim())
  return match ? match.slice(1).map(Number) : null
}

/** Whether `a` is a higher version than `b`; false when either is not one. */
function newer(a, b) {
  const [x, y] = [parts(a), parts(b)]
  if (!x || !y) return false
  for (let n = 0; n < 3; n += 1) if (x[n] !== y[n]) return x[n] > y[n]
  return false
}

/** Where things may come from, given where the releases are asked for. That is in package.json, inside the app; a
 * server on this computer there (a test build) stands for itself only. */
function allowed(source, url) {
  let from
  let to
  try {
    from = new URL(source)
    to = new URL(url)
  } catch {
    return false
  }
  if (from.protocol === 'http:' && from.hostname === '127.0.0.1') return to.origin === from.origin
  return to.protocol === 'https:' && to.port === '' && !to.username && !to.password && HOSTS.includes(to.hostname)
}

/** The release notes as plain text: no 下载 section (which file to take), no Markdown marks. */
function plainNotes(body) {
  const lines = []
  let skipping = 0
  for (const line of String(body ?? '').replace(/\r\n/g, '\n').slice(0, 20_000).split('\n')) {
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      if (skipping && heading[1].length <= skipping) skipping = 0
      if (!skipping && /^下载/.test(heading[2])) skipping = heading[1].length
    }
    if (skipping) continue
    lines.push(
      (heading ? heading[2] : line)
        .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
        .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
        .replace(/<[^>]+>/g, '')
        .replace(/(\*\*|__|`)/g, '')
        .replace(/^(\s*)[-*]\s+/, '$1· '),
    )
  }
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, 4000)
}

/** What a release (GitHub's answer) offers someone on `current`: null when nothing newer. Throws an UpdateError for
 * a newer one that cannot be taken. */
function offer(release, current, source) {
  const version = parts(release?.tag_name)?.join('.')
  if (!version || release.draft || release.prerelease || !newer(version, current)) return null
  const assets = Array.isArray(release.assets) ? release.assets : []
  const named = (name) => {
    const asset = assets.find((a) => a?.name === name && a.state !== 'open')
    if (!asset) return null
    // This version's own download folder: a link elsewhere on the same host is not it.
    const url = String(asset.browser_download_url ?? '')
    if (!allowed(source, url) || !new URL(url).pathname.endsWith(`/releases/download/v${version}/${name}`)) return null
    if (source.startsWith('https:')) {
      const repo = /^\/repos\/([^/]+\/[^/]+)\//.exec(new URL(source).pathname)?.[1]
      if (!url.toLowerCase().startsWith(`https://github.com/${repo}/releases/download/`.toLowerCase())) return null
    }
    return { name, url, size: Number(asset.size), digest: /^sha256:([0-9a-f]{64})$/i.exec(asset.digest ?? '')?.[1].toLowerCase() ?? '' }
  }
  const installer = named(installerName(version))
  const page = String(release.html_url ?? '')
  const found = { version, notes: plainNotes(release.body), page: allowed(source, page) ? page : '' }
  if (!installer) throw Object.assign(new UpdateError(`有新版本 ${version}，但发布页上没有它的${MAC ? ' Mac ' : ' Windows '}安装包。`), { found })
  if (!(installer.size > 0 && installer.size <= MAX_INSTALLER)) {
    throw Object.assign(new UpdateError(`有新版本 ${version}，但它的安装包大小不对。`), { found })
  }
  return { ...found, installer, sums: named('SHA256SUMS.txt') }
}

/** The SHA-256 of `name` in a SHA256SUMS.txt; '' when it is not listed. */
function digestIn(text, name) {
  for (const line of String(text).split(/\r?\n/)) {
    const match = /^([0-9a-f]{64})\s+\*?(.+?)\s*$/i.exec(line)
    if (match && match[2] === name) return match[1].toLowerCase()
  }
  return ''
}

/** Deletes a file, if it is there. (Not fs.rmSync: Node 24.11's crashes the process at a path with Chinese in it.) */
function remove(file) {
  try {
    fs.unlinkSync(file)
  } catch (err) {
    if (err.code !== 'ENOENT') throw err
  }
}

function hashOf(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256')
    fs.createReadStream(file)
      .on('data', (chunk) => hash.update(chunk))
      .on('error', reject)
      .on('end', () => resolve(hash.digest('hex')))
  })
}

/** Why a request failed, in words. */
function reason(err) {
  if (err instanceof UpdateError) return err.message
  const code = String(err?.code || err?.message || err)
  if (/ENOSPC/.test(code)) return '磁盘空间不够，下载没有完成。'
  if (/^(EACCES|EPERM|EBUSY|EROFS)/.test(code)) return `写不了下载文件夹（${code}）。`
  return `连不上 GitHub（${code.replace(/^net::/, '').slice(0, 80)}）。检查网络或代理后再试。`
}

class Updater extends EventEmitter {
  /**
   * @param {{ version: string, source: string, dir: string,
   *   get: (url: string, options: { timeout: number }) => Promise<{ status: number, location?: string, body?: AsyncIterable<Buffer>, close?: () => void }>,
   *   log?: { info: Function, warn: Function } }} options
   * `get`: one request, redirects not followed.
   */
  constructor({ version, source, dir, get, log = { info() {}, warn() {} } }) {
    super()
    this.version = version
    this.source = source
    this.dir = dir
    this.get = get
    this.log = log
    this.found = null // what is on offer (offer())
    this.file = null // downloaded and checked
    this.state = 'idle' // idle · checking · latest · available · downloading · ready · installing
    this.percent = 0
    this.error = ''
    this.checkedAt = 0
    this.working = null
  }

  snapshot() {
    return {
      current: this.version,
      state: this.state,
      version: this.found?.version ?? '',
      notes: this.found?.notes ?? '',
      page: this.found?.page ?? '',
      size: this.found?.installer?.size ?? 0,
      percent: this.percent,
      error: this.error,
      checkedAt: this.checkedAt,
    }
  }

  set(patch) {
    Object.assign(this, patch)
    this.emit('change', this.snapshot())
  }

  /** Follows redirects, each checked. The answer's `body` is still to be read. */
  async open(url, timeout = 20_000) {
    for (let hop = 0; hop < MAX_HOPS; hop += 1) {
      if (!allowed(this.source, url)) throw new UpdateError('下载地址不对，没有下载。')
      const res = await this.get(url, { timeout })
      if (res.status >= 300 && res.status < 400 && res.location) {
        res.close?.()
        url = new URL(res.location, url).href
        continue
      }
      if (res.status === 200) return res
      res.close?.()
      if (res.status === 403 || res.status === 429) throw new UpdateError('GitHub 暂时不让查（问得太多了），过一会儿再试。')
      if (res.status === 404) throw new UpdateError('发布页上找不到这个文件（404）。')
      throw new UpdateError(`GitHub 没有正常回应（HTTP ${res.status}）。`)
    }
    throw new UpdateError('下载地址跳转太多次，没有下载。')
  }

  async text(url) {
    const res = await this.open(url)
    const chunks = []
    let size = 0
    for await (const chunk of res.body) {
      size += chunk.length
      if (size > MAX_TEXT) {
        res.close?.()
        throw new UpdateError('GitHub 的回应不对。')
      }
      chunks.push(chunk)
    }
    return Buffer.concat(chunks).toString('utf8')
  }

  /** One thing at a time: the same one again gives its result, another waits for it. */
  async once(name, work) {
    if (this.working?.name === name) return this.working.done
    while (this.working) await this.working.done
    const done = work()
      .catch((err) => this.log.warn(`update: ${name}:`, err))
      .finally(() => {
        this.working = null
      })
    this.working = { name, done }
    return done
  }

  /** Looks for a newer version. Never throws: `error` says what went wrong. */
  check() {
    return this.once('check', async () => {
      if (this.state === 'installing') return
      const before = { state: this.state, found: this.found, file: this.file }
      this.set({ state: 'checking', error: '' })
      try {
        let release
        try {
          release = JSON.parse(await this.text(this.source))
        } catch (err) {
          throw err instanceof SyntaxError ? new UpdateError('GitHub 的回应不对。') : err
        }
        const found = offer(release, this.version, this.source)
        const same = found && before.file && before.found?.version === found.version
        this.log.info(`update: ${found ? `${found.version} is out` : `${this.version} is the latest`}`)
        this.set({
          found,
          file: same ? before.file : null,
          state: same ? 'ready' : found ? 'available' : 'latest',
          checkedAt: Date.now(),
        })
      } catch (err) {
        this.log.warn('update: check:', err.message)
        // A new version that cannot be taken here is still worth the link to it.
        if (err.found) this.set({ state: 'idle', found: err.found, file: null, error: err.message, checkedAt: Date.now() })
        else this.set({ ...before, state: before.state === 'latest' ? 'idle' : before.state, error: reason(err) })
      }
    })
  }

  /** Downloads what was found and checks it. `file` is set when it is all right, else `error`. */
  download() {
    return this.once('download', async () => {
      const found = this.found
      if (!found?.installer || this.file || this.state === 'installing') return
      const { installer } = found
      const file = path.join(this.dir, installer.name)
      const part = `${file}.part`
      this.set({ state: 'downloading', percent: 0, error: '' })
      try {
        fs.mkdirSync(this.dir, { recursive: true })
        const digest = installer.digest || (found.sums ? digestIn(await this.text(found.sums.url), installer.name) : '')
        if (!digest) throw new UpdateError('发布页上没有这个安装包的校验值，没有下载。')
        installer.digest = digest
        if (!(fs.existsSync(file) && (await hashOf(file)) === digest)) {
          await this.fetch(installer, digest, part)
          remove(file)
          fs.renameSync(part, file)
        }
        this.log.info(`update: ${installer.name} downloaded and checked`)
        this.set({ state: 'ready', file, percent: 100 })
      } catch (err) {
        this.log.warn('update: download:', err.message)
        try {
          remove(part)
        } catch {
          // tidy() takes it later
        }
        this.set({ state: 'available', percent: 0, error: reason(err) })
      }
    })
  }

  async fetch(installer, digest, part) {
    const res = await this.open(installer.url)
    const hash = crypto.createHash('sha256')
    const out = fs.openSync(part, 'w')
    let size = 0
    try {
      for await (const chunk of res.body) {
        size += chunk.length
        if (size > installer.size) throw new UpdateError('下载的文件大小对不上，已经删掉。')
        hash.update(chunk)
        fs.writeSync(out, chunk)
        const percent = Math.floor((size / installer.size) * 100)
        if (percent !== this.percent) this.set({ percent })
      }
    } finally {
      fs.closeSync(out)
      res.close?.()
    }
    if (size !== installer.size) throw new UpdateError('下载没有完整，已经删掉，再试一次。')
    if (hash.digest('hex') !== digest) throw new UpdateError('下载的文件和发布的校验值对不上，已经删掉。')
  }

  /** The installer, checked once more right before it runs; null (and `error`) when it is not the one any more. */
  async verified() {
    const { file, found } = this
    try {
      if (file && (await hashOf(file)) === found.installer.digest) return file
    } catch {
      // gone
    }
    try {
      if (file) remove(file)
    } catch {
      // it is not run either way
    }
    this.set({ file: null, state: 'available', error: '下载好的安装包被改动或删掉了，请重新下载。' })
    return null
  }

  // ---- what is kept over the installation ----------------------------------------------------------

  /** Written right before the installer starts: the next start knows whether it worked. */
  begin(pid = 0) {
    fs.mkdirSync(this.dir, { recursive: true })
    const note = { from: this.version, to: this.found.version, at: Date.now(), pid }
    fs.writeFileSync(path.join(this.dir, PENDING), JSON.stringify(note), 'utf8')
  }

  forget() {
    try {
      remove(path.join(this.dir, PENDING))
    } catch (err) {
      this.log.warn('update:', err.message) // never in the way of starting the app
    }
  }

  /** At the start: what became of an installation, { to, ok } or { to, running: pid }; null when there was none. */
  outcome(alive = () => false, now = Date.now()) {
    let note
    try {
      note = JSON.parse(fs.readFileSync(path.join(this.dir, PENDING), 'utf8'))
    } catch {
      return null
    }
    if (!parts(note?.to)) {
      this.forget()
      return null
    }
    const ok = !newer(note.to, this.version)
    if (!ok && now - note.at < PENDING_FOR && note.pid > 0 && alive(note.pid)) return { to: note.to, running: note.pid }
    this.forget()
    return { to: note.to, ok }
  }

  /** Half downloads, and installers of versions this one is or is past. */
  tidy() {
    let names = []
    try {
      names = fs.readdirSync(this.dir)
    } catch {
      return
    }
    for (const name of names) {
      const version = INSTALLER.exec(name)?.[1]
      if (!name.endsWith('.part') && !(version && !newer(version, this.version))) continue
      if (name.endsWith('.part') && this.state === 'downloading') continue
      try {
        remove(path.join(this.dir, name))
      } catch {
        // still in use (the installer that has just started this): next time
      }
    }
  }
}

module.exports = { Updater, UpdateError, allowed, digestIn, newer, offer, plainNotes, reason }
