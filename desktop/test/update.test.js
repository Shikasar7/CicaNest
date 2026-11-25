'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')

const { Updater, allowed, digestIn, newer, offer, plainNotes } = require('../src/update')
const { howTo, runInstaller } = require('../src/update-install')

const SOURCE = 'https://api.github.com/repos/a/nest/releases/latest'
const BYTES = Buffer.alloc(200_000, 'installer')
const SHA = crypto.createHash('sha256').update(BYTES).digest('hex')

const release = (version = '0.9.0', base = 'https://github.com/a/nest', extra = {}) => ({
  tag_name: `v${version}`,
  html_url: `${base}/releases/tag/v${version}`,
  body: '## 下载\n\n| a | b |\n\n## 这一版有什么\n\n- **快**：见 [说明](https://x)\n',
  assets: [
    {
      name: `nest-setup-${version}.exe`,
      browser_download_url: `${base}/releases/download/v${version}/nest-setup-${version}.exe`,
      size: BYTES.length,
      digest: `sha256:${SHA}`,
      ...extra,
    },
    { name: 'CicaNest-server.zip', browser_download_url: `${base}/releases/download/v${version}/CicaNest-server.zip`, size: 9 },
    { name: 'SHA256SUMS.txt', browser_download_url: `${base}/releases/download/v${version}/SHA256SUMS.txt`, size: 100 },
  ],
})

test('versions are compared as numbers; only plain ones count', () => {
  assert.ok(newer('v0.10.0', '0.9.9'))
  assert.ok(newer('1.0.0', '0.99.99'))
  assert.ok(!newer('0.8.0', '0.8.0'))
  assert.ok(!newer('0.7.9', '0.8.0'))
  for (const bad of ['0.9', '0.9.0-beta', '0.9.0.1', 'latest', '', null, '99999999.0.0']) assert.ok(!newer(bad, '0.8.0'), String(bad))
  assert.ok(!newer('0.9.0', 'dev'))
})

test('only https, only these hosts; a server on this computer only for itself', () => {
  assert.ok(allowed(SOURCE, 'https://github.com/a/nest/x'))
  assert.ok(allowed(SOURCE, 'https://release-assets.githubusercontent.com/x?y=1'))
  for (const bad of [
    'http://github.com/x',
    'https://github.com.evil.com/x',
    'https://evil.com/github.com',
    'https://github.com:8443/x',
    'https://u:p@github.com/x',
    'https://raw.githubusercontent.com/x',
    'http://127.0.0.1:1/x',
    'file:///c:/x',
    'nonsense',
  ]) {
    assert.ok(!allowed(SOURCE, bad), bad)
  }
  assert.ok(allowed('http://127.0.0.1:9/latest', 'http://127.0.0.1:9/x'))
  assert.ok(!allowed('http://127.0.0.1:9/latest', 'http://127.0.0.1:8/x'))
  assert.ok(!allowed('http://127.0.0.1:9/latest', 'https://github.com/x'))
  assert.ok(!allowed('http://evil.com/latest', 'http://evil.com/x'))
})

test('what is offered: the right file of a newer release', () => {
  const found = offer(release(), '0.8.0', SOURCE)
  assert.equal(found.version, '0.9.0')
  assert.equal(found.installer.digest, SHA)
  assert.equal(found.notes, '这一版有什么\n\n· 快：见 说明')
  assert.equal(found.page, 'https://github.com/a/nest/releases/tag/v0.9.0')
  assert.equal(offer(release('0.8.0'), '0.8.0', SOURCE), null)
  assert.equal(offer(release('0.7.0'), '0.8.0', SOURCE), null)
  assert.equal(offer({ ...release(), draft: true }, '0.8.0', SOURCE), null)
  assert.equal(offer({ ...release(), prerelease: true }, '0.8.0', SOURCE), null)
  assert.equal(offer({ message: 'Not Found' }, '0.8.0', SOURCE), null)
  assert.equal(offer(null, '0.8.0', SOURCE), null)
})

test('a file of another name, version, repo or place is not taken', () => {
  const bad = [
    { name: 'nest-setup-0.9.1.exe' },
    { name: 'nest-setup.exe' },
    { browser_download_url: 'https://evil.com/a/nest/releases/download/v0.9.0/nest-setup-0.9.0.exe' },
    { browser_download_url: 'https://github.com/b/nest/releases/download/v0.9.0/nest-setup-0.9.0.exe' },
    { browser_download_url: 'https://github.com/a/nest/releases/download/v0.8.9/nest-setup-0.9.0.exe' },
    { browser_download_url: 'https://github.com/a/nest/../../b/nest/releases/download/v0.9.0/nest-setup-0.9.0.exe' },
    { browser_download_url: undefined },
    { size: 0 },
    { size: 9e12 },
  ]
  for (const extra of bad) {
    assert.throws(() => offer(release('0.9.0', undefined, extra), '0.8.0', SOURCE), /有新版本 0.9.0/, JSON.stringify(extra))
  }
})

test('SHA256SUMS.txt is read by the exact name', () => {
  const text = `${'1'.repeat(64)}  xnest-setup-0.9.0.exe\r\n${'2'.repeat(64)} *nest-setup-0.9.0.exe\r\n`
  assert.equal(digestIn(text, 'nest-setup-0.9.0.exe'), '2'.repeat(64))
  assert.equal(digestIn(text, 'setup-0.9.0.exe'), '')
  assert.equal(digestIn('', 'x'), '')
})

test('the notes: without the list of files', () => {
  assert.equal(plainNotes('前言\n## 下载\nx\n### 细节\ny\n## 修了\n* `a`'), '前言\n修了\n· a')
  assert.equal(plainNotes(null), '')
})

// ---- downloading, from a server here -----------------------------------------------------------------

function get(url, { timeout }) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { agent: false, timeout }, (res) =>
      resolve({ status: res.statusCode, location: res.headers.location, body: res, close: () => req.destroy() }),
    )
    req.on('error', reject)
  })
}

async function setup(t, change = () => {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-update-'))
  const routes = {}
  const asked = []
  const server = http.createServer((req, res) => {
    asked.push(req.url)
    const route = routes[req.url]
    if (!route) return res.writeHead(404).end()
    route(req, res)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  const data = { release: release('0.9.0', base), bytes: BYTES, sums: '', asked, routes, base, dir }
  routes['/latest'] = (_req, res) => res.end(typeof data.release === 'string' ? data.release : JSON.stringify(data.release))
  routes['/releases/download/v0.9.0/nest-setup-0.9.0.exe'] = (_req, res) => res.writeHead(302, { location: '/real' }).end()
  routes['/real'] = (_req, res) => res.end(data.bytes)
  routes['/releases/download/v0.9.0/SHA256SUMS.txt'] = (_req, res) => res.end(data.sums)
  change(data)
  t.after(() => {
    server.closeAllConnections()
    server.close()
    fs.rmSync(dir, { recursive: true, force: true })
  })
  const updater = new Updater({ version: '0.8.0', source: `${base}/latest`, dir, get })
  return { ...data, data, updater, server }
}

test('checked, downloaded over a redirect, verified', async (t) => {
  const { updater, dir } = await setup(t)
  const seen = []
  updater.on('change', (s) => seen.push(s.state))
  await updater.check()
  assert.equal(updater.state, 'available')
  assert.equal(updater.snapshot().size, BYTES.length)
  assert.deepEqual(fs.readdirSync(dir), [], 'nothing is downloaded unasked')
  await updater.download()
  assert.equal(updater.state, 'ready', updater.error)
  assert.equal(updater.percent, 100)
  assert.deepEqual(fs.readdirSync(dir), ['nest-setup-0.9.0.exe'])
  assert.equal(await updater.verified(), path.join(dir, 'nest-setup-0.9.0.exe'))
  assert.deepEqual([...new Set(seen)], ['checking', 'available', 'downloading', 'ready'])
  await updater.check()
  assert.equal(updater.state, 'ready', 'a check does not forget it')
})

test('already downloaded before: not again', async (t) => {
  const { updater, dir, asked } = await setup(t)
  fs.writeFileSync(path.join(dir, 'nest-setup-0.9.0.exe'), BYTES)
  await updater.check()
  await updater.download()
  assert.equal(updater.state, 'ready')
  assert.ok(!asked.includes('/real'))
})

test('the same version is the latest', async (t) => {
  const { updater } = await setup(t, (d) => (d.release = release('0.8.0', d.base)))
  await updater.check()
  assert.equal(updater.state, 'latest')
  assert.ok(updater.checkedAt)
})

test('another file than the published one is deleted and said', async (t) => {
  for (const [bytes, words] of [
    [Buffer.alloc(BYTES.length, 'x'), /校验值对不上/],
    [BYTES.subarray(0, 1000), /没有完整/],
    [Buffer.concat([BYTES, BYTES]), /大小对不上/],
  ]) {
    const { updater, dir } = await setup(t, (d) => (d.bytes = bytes))
    await updater.check()
    await updater.download()
    assert.equal(updater.state, 'available')
    assert.match(updater.error, words)
    assert.equal(updater.file, null)
    assert.deepEqual(fs.readdirSync(dir), [])
  }
})

test('changed between the download and the installing: not run', async (t) => {
  const { updater, dir } = await setup(t)
  await updater.check()
  await updater.download()
  fs.appendFileSync(path.join(dir, 'nest-setup-0.9.0.exe'), 'x')
  assert.equal(await updater.verified(), null)
  assert.match(updater.error, /重新下载/)
  assert.deepEqual(fs.readdirSync(dir), [])
  await updater.download()
  assert.equal(updater.state, 'ready')
})

test('no digest: SHA256SUMS.txt; none at all: not downloaded', async (t) => {
  const without = (d) => delete d.release.assets[0].digest
  const good = await setup(t, (d) => {
    without(d)
    d.sums = `${SHA}  nest-setup-0.9.0.exe\n`
  })
  await good.updater.check()
  await good.updater.download()
  assert.equal(good.updater.state, 'ready', good.updater.error)
  const none = await setup(t, without)
  await none.updater.check()
  await none.updater.download()
  assert.match(none.updater.error, /没有这个安装包的校验值/)
  assert.ok(!none.asked.includes('/real'))
})

test('a redirect elsewhere, or in circles, is not followed', async (t) => {
  const away = await setup(t, (d) => (d.routes['/releases/download/v0.9.0/nest-setup-0.9.0.exe'] = (_q, r) => r.writeHead(302, { location: 'http://127.0.0.1:9/x' }).end()))
  await away.updater.check()
  await away.updater.download()
  assert.match(away.updater.error, /地址不对/)
  const round = await setup(t, (d) => (d.routes['/real'] = (_q, r) => r.writeHead(302, { location: '/real' }).end()))
  await round.updater.check()
  await round.updater.download()
  assert.match(round.updater.error, /跳转太多次/)
})

test('no network, too many questions, nonsense: said, and what was known stays', async (t) => {
  const { updater, data, server, routes } = await setup(t)
  data.release = '<html>'
  await updater.check()
  assert.match(updater.error, /回应不对/)
  routes['/latest'] = (_q, r) => r.writeHead(403).end()
  await updater.check()
  assert.match(updater.error, /问得太多/)
  routes['/latest'] = (_q, r) => r.end(JSON.stringify(data.release))
  data.release = release('0.9.0', data.base)
  await updater.check()
  assert.equal(updater.state, 'available')
  server.closeAllConnections()
  await new Promise((resolve) => server.close(resolve))
  await updater.check()
  assert.equal(updater.state, 'available')
  assert.match(updater.error, /连不上 GitHub/)
  await updater.download()
  assert.equal(updater.state, 'available')
  assert.match(updater.error, /连不上 GitHub/)
})

test('cut off in the middle: nothing is left', async (t) => {
  const { updater, dir } = await setup(
    t,
    (d) =>
      (d.routes['/real'] = (_q, r) => {
        r.writeHead(200, { 'content-length': BYTES.length })
        r.write(BYTES.subarray(0, 5000), () => r.destroy())
      }),
  )
  await updater.check()
  await updater.download()
  assert.equal(updater.state, 'available')
  assert.ok(updater.error)
  assert.deepEqual(fs.readdirSync(dir), [])
})

test('two clicks are one download', async (t) => {
  const { updater, asked } = await setup(t)
  await Promise.all([updater.check(), updater.check()])
  await Promise.all([updater.download(), updater.download(), updater.check()])
  assert.equal(asked.filter((u) => u === '/real').length, 1)
  assert.equal(updater.state, 'ready')
})

// ---- over the installation ---------------------------------------------------------------------------

test('the next start knows what became of it, once', async (t) => {
  const { updater, dir, base } = await setup(t)
  assert.equal(updater.outcome(), null)
  await updater.check()
  updater.begin(77)
  assert.deepEqual(updater.outcome((pid) => pid === 77), { to: '0.9.0', running: 77 })
  assert.deepEqual(updater.outcome(() => true, Date.now() + 3_600_000), { to: '0.9.0', ok: false }, 'not waited for that long')
  assert.equal(updater.outcome(), null)
  updater.begin(77)
  assert.deepEqual(updater.outcome(() => false), { to: '0.9.0', ok: false })
  updater.begin(77)
  const after = new Updater({ version: '0.9.0', source: `${base}/latest`, dir, get })
  assert.deepEqual(after.outcome(() => true), { to: '0.9.0', ok: true }, 'the installer may still be ending')
  fs.writeFileSync(path.join(dir, 'installing.json'), '{"to":"../x"}')
  assert.equal(updater.outcome(), null)
})

test('old installers and half downloads are cleared, a newer one stays', async (t) => {
  const { updater, dir } = await setup(t)
  for (const name of ['nest-setup-0.7.0.exe', 'nest-setup-0.8.0.exe', 'nest-setup-0.9.0.exe', 'nest-setup-0.9.0.exe.part', 'mine.exe']) {
    fs.writeFileSync(path.join(dir, name), '')
  }
  updater.tidy()
  assert.deepEqual(fs.readdirSync(dir).sort(), ['mine.exe', 'nest-setup-0.9.0.exe'])
})

test('how it can be installed over', async () => {
  const at = (found, { exists = true, writable = true } = {}) =>
    howTo('D:\\装 在\\nest\\nest.exe', {
      exists: (file) => exists && file === 'D:\\装 在\\nest\\Uninstall nest.exe',
      find: async (root) => root === found,
      writable: () => writable,
    })
  assert.equal(await at('HKCU'), 'silent')
  assert.equal(await at('HKCU', { writable: false }), 'window')
  assert.equal(await at('HKLM'), 'everyone')
  assert.equal(await at(''), 'page')
  assert.equal(await at('HKCU', { exists: false }), 'page')
})

test('the installer runs on its own; when it fails the app is opened again', { skip: process.platform !== 'win32' }, async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nest 装 '))
  // Not rmSync: with this path Node 24.11 crashes.
  t.after(async () => {
    await new Promise((r) => setTimeout(r, 500))
    for (const name of fs.readdirSync(dir)) fs.unlinkSync(path.join(dir, name))
    fs.rmdirSync(dir)
  })
  const system = path.join(process.env.SystemRoot, 'System32')
  const app = path.join(dir, 'app.cmd')
  fs.writeFileSync(app, '@echo opened> "%~dp0opened.txt"\r\n@exit\r\n')
  const opened = path.join(dir, 'opened.txt')
  const ended = async (pid) => {
    for (let n = 0; n < 100; n += 1) {
      try {
        process.kill(pid, 0)
      } catch {
        return
      }
      await new Promise((r) => setTimeout(r, 100))
    }
    assert.fail('still running')
  }
  const good = path.join(dir, 'nest-setup-0.9.0.exe')
  fs.copyFileSync(path.join(system, 'dllhost.exe'), good) // takes anything, ends well
  await ended(await runInstaller(good, 'silent', app))
  await new Promise((r) => setTimeout(r, 500))
  assert.ok(!fs.existsSync(opened), 'it went well: the installer opens the new one itself')
  const bad = path.join(dir, 'nest-setup-0.9.1.exe')
  fs.copyFileSync(path.join(system, 'where.exe'), bad) // not what it takes
  await runInstaller(bad, 'silent', app)
  for (let n = 0; n < 100 && !fs.existsSync(opened); n += 1) await new Promise((r) => setTimeout(r, 100))
  assert.ok(fs.existsSync(opened))
})

test('for this user without a window; for everyone with the installer\'s own', async () => {
  const run = (how) => {
    let line
    const spawned = (_file, args) => {
      line = args.at(-1)
      return { on: (name, fn) => name === 'spawn' && fn(), unref() {}, pid: 5 }
    }
    return runInstaller('D:\\u\\nest-setup-0.9.0.exe', how, 'D:\\nest\\nest.exe', { run: spawned }).then((pid) => {
      assert.equal(pid, 5)
      assert.ok(!line.includes('D:'), 'paths only in the environment')
      return /SETUP%" (.*?) ?\|\|/.exec(line)[1]
    })
  }
  assert.equal(await run('silent'), '/S --updated --force-run /currentuser')
  assert.equal(await run('everyone'), '--updated /allusers')
  assert.equal(await run('window'), '')
})
