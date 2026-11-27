'use strict'

// An installed app that brings its own nest: which nest it opens, how it starts the one it brings.

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')

const { bundledBackend, loadConfig, portFromDotenv, pythonIn } = require('../src/config')
const { prepareHome } = require('../src/local-setup')
const { movedAway } = require('../src/login-item')
const { Nest, backendEnv, portTaken } = require('../src/nest')

function temp(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-local-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}

/** The resources folder of an app built with a nest in it. */
function resources(dir) {
  const folder = path.join(dir, 'resources')
  const python = pythonIn(path.join(folder, 'backend', 'python'))
  fs.mkdirSync(path.dirname(python), { recursive: true })
  fs.writeFileSync(python, '')
  return folder
}

test('the bundled Python is where each system keeps it', () => {
  assert.equal(pythonIn('/b', { platform: 'darwin' }), path.join('/b', 'bin', 'python3'))
  assert.equal(pythonIn('/h/.venv', { venv: true, platform: 'darwin' }), path.join('/h/.venv', 'bin', 'python'))
  assert.equal(pythonIn('C:\\b', { platform: 'win32' }), path.join('C:\\b', 'python.exe'))
  assert.equal(pythonIn('C:\\h', { venv: true, platform: 'win32' }), path.join('C:\\h', 'Scripts', 'python.exe'))
})

test('installed: undecided until the user picks, then local or remote', (t) => {
  const dir = temp(t)
  const bundled = bundledBackend(resources(dir))
  const profile = path.join(dir, 'profile')
  const load = (connection, env = {}) => loadConfig(env, connection, profile, bundled)

  const first = load({})
  assert.equal(first.undecided, true)
  assert.equal(first.canRunHere, true)
  assert.equal(first.remote, false)
  assert.equal(first.home, path.join(profile, 'home'))

  const local = load({ mode: 'local' })
  assert.equal(local.undecided, false)
  assert.equal(local.remote, false)
  assert.equal(local.isTestCopy, false)
  assert.equal(local.python, bundled.python)
  assert.equal(local.bundled, bundled)

  const remote = load({ mode: 'remote', remote: 'http://my-mac.local:18800', other: '192.168.0.9' })
  assert.equal(remote.undecided, false)
  assert.equal(remote.remote, true)
  assert.equal(remote.bundled, null)
  assert.deepEqual(remote.origins, ['http://my-mac.local:18800', 'http://192.168.0.9:18800'])
})

test('installed: an address saved by an older app stays remote; going local keeps it for later', (t) => {
  const dir = temp(t)
  const bundled = bundledBackend(resources(dir))
  const old = loadConfig({}, { remote: 'http://my-mac.local:18800' }, dir, bundled)
  assert.equal(old.remote, true)
  assert.equal(old.undecided, false)
  const local = loadConfig({}, { mode: 'local', remote: 'http://my-mac.local:18800' }, dir, bundled)
  assert.equal(local.remote, false)
  assert.equal(local.undecided, false)
})

test('installed without a nest in it: only an address will do', (t) => {
  const dir = temp(t)
  assert.equal(bundledBackend(dir), null)
  const config = loadConfig({}, { mode: 'local' }, dir, null)
  assert.equal(config.undecided, true)
  assert.equal(config.canRunHere, false)
})

test('NEST_HOME and NEST_PYTHON still decide, and a folder with its own Python keeps using it', (t) => {
  const dir = temp(t)
  const bundled = bundledBackend(resources(dir))
  const home = path.join(dir, 'source')
  const plain = loadConfig({ NEST_HOME: home }, {}, dir, bundled)
  assert.equal(plain.undecided, false)
  assert.equal(plain.isTestCopy, true)
  assert.equal(plain.python, bundled.python)
  const venv = path.join(home, '.venv', 'Scripts', 'python.exe')
  fs.mkdirSync(path.dirname(venv), { recursive: true })
  fs.writeFileSync(venv, '')
  const own = loadConfig({ NEST_HOME: home }, {}, dir, bundled)
  assert.equal(own.python, venv)
  assert.equal(own.bundled, null)
  const named = loadConfig({ NEST_HOME: home, NEST_PYTHON: path.join(dir, 'py.exe') }, {}, dir, bundled)
  assert.equal(named.python, path.join(dir, 'py.exe'))
  assert.equal(named.bundled, null)
})

test('from the sources nothing changes', () => {
  const config = loadConfig({}, {})
  assert.equal(config.undecided, false)
  assert.equal(config.canRunHere, true)
  assert.equal(config.bundled, null)
  assert.match(config.python, /\.venv[\\/]Scripts[\\/]python\.exe$/)
  assert.equal(loadConfig({}, { mode: 'remote', remote: 'mac.local' }).remote, true)
  assert.equal(loadConfig({}, { mode: 'local', remote: 'mac.local' }).remote, false)
})

test('the bundled nest is told its folders and kept apart from the Python on this computer', () => {
  const env = { Path: 'C:\\x', PYTHONPATH: 'C:\\mine', PythonHome: 'C:\\py', VIRTUAL_ENV: 'C:\\v', NEST_HOME: 'C:\\else' }
  const own = backendEnv({ bundled: null, home: 'C:\\h' }, env)
  assert.equal(own.PYTHONPATH, 'C:\\mine')
  assert.equal(own.NEST_HOME, 'C:\\else')
  assert.equal(own.NEST_BUNDLED, undefined)
  const bundled = backendEnv({ bundled: { web: 'C:\\app\\web' }, home: 'C:\\h' }, env)
  for (const name of ['PYTHONPATH', 'PythonHome', 'VIRTUAL_ENV']) assert.equal(bundled[name], undefined)
  assert.equal(bundled.Path, 'C:\\x')
  assert.equal(bundled.NEST_HOME, 'C:\\h')
  assert.equal(bundled.NEST_WEB_DIST_DIR, 'C:\\app\\web')
  assert.equal(bundled.PYTHONSAFEPATH, '1')
  assert.equal(bundled.NEST_BUNDLED, '1')
})

test('the first time, a port something else uses is passed over and the choice is kept in .env', async (t) => {
  const home = path.join(temp(t), '她的 家')
  assert.equal(await prepareHome(home, {}, async (port) => port < 18802), 18802)
  assert.equal(portFromDotenv(home), 18802)
  // Kept, even when another one is taken now.
  assert.equal(await prepareHome(home, {}, async () => true), 18802)
})

test('the usual port needs no .env; what the user wrote there stays', async (t) => {
  const free = path.join(temp(t), 'a')
  assert.equal(await prepareHome(free, {}, async () => false), 18800)
  assert.equal(fs.existsSync(path.join(free, '.env')), false)
  const written = temp(t)
  fs.writeFileSync(path.join(written, '.env'), 'KEY=secret')
  assert.equal(await prepareHome(written, {}, async (port) => port === 18800), 18801)
  assert.equal(fs.readFileSync(path.join(written, '.env'), 'utf8'), 'KEY=secret\nNEST_PORT=18801\n')
  // With data its nest may be the one on that port: nothing is moved.
  const used = temp(t)
  fs.mkdirSync(path.join(used, 'data'))
  fs.writeFileSync(path.join(used, 'data', 'nest.db'), '')
  assert.equal(await prepareHome(used, {}, async () => true), 18800)
  assert.equal(fs.existsSync(path.join(used, '.env')), false)
  assert.equal(await prepareHome(temp(t), { NEST_PORT: '19000' }, async () => true), 19000)
  await assert.rejects(prepareHome(temp(t), {}, async () => true), /都被占用/)
})

/** A nest (or not) on a free port: what it answers, and the app's config for it. */
async function serve(t, { token = 'ours', version = '1.0.0', app = 'nest' } = {}) {
  const server = http.createServer((req, res) => {
    const signedIn = req.headers.authorization === `Bearer ${token}`
    if (req.url === '/api/health') res.end(JSON.stringify({ app, ok: true, version }))
    else if (req.url === '/api/accounts' && signedIn) res.end('[]')
    else {
      res.statusCode = 401
      res.end('{}')
    }
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(() => server.close())
  const home = temp(t)
  const { port } = server.address()
  const runtimeFile = path.join(home, 'runtime.json')
  fs.writeFileSync(runtimeFile, JSON.stringify({ pid: 0, port: 1, token: 'ours', version }))
  const bundled = { folder: home, python: process.execPath, web: home }
  return { home, port, runtimeFile, python: process.execPath, bundled, version: '1.0.0' }
}

test('a nest that takes our token is the one to use', async (t) => {
  assert.deepEqual(await new Nest(await serve(t)).start(), { already: true })
})

test('another nest on the port is not taken for ours', async (t) => {
  const config = await serve(t, { token: 'theirs' })
  await assert.rejects(new Nest(config).start(), (err) => err.code === 'foreign' && err.message.includes(String(config.port)))
})

test('another program on the port is named, not waited for', async (t) => {
  const config = await serve(t, { app: 'something else' })
  assert.equal(await portTaken(config.port), true)
  await assert.rejects(new Nest(config).start(), (err) => err.code === 'port')
})

test('a nest left running by an older version is stopped first', async (t) => {
  const nest = new Nest(await serve(t, { version: '0.9.0' }))
  let stopped = 0
  nest.stop = async () => {
    stopped += 1
  }
  // (the fake one keeps its port: the start ends there)
  await assert.rejects(nest.start(), (err) => err.code === 'port')
  assert.equal(stopped, 1)
  // Run from the sources, an older backend is the developer's business.
  const source = new Nest({ ...(await serve(t, { version: '0.9.0' })), bundled: null })
  assert.deepEqual(await source.start(), { already: true })
})

test('started at login from a folder the app is no longer in: taken over; anything else is left alone', () => {
  const here = 'D:\\Apps\\new\\nest.exe'
  const item = { name: 'nest', path: 'C:\\old\\nest.exe', args: ['--hidden'], enabled: true }
  const gone = () => false
  assert.equal(movedAway([item], 'nest', here, gone), true)
  assert.equal(movedAway([item], 'nest', here, () => true), false) // that one is still there
  assert.equal(movedAway([{ ...item, enabled: false }], 'nest', here, gone), false) // switched off by the user
  assert.equal(movedAway([{ ...item, path: here.toUpperCase() }], 'nest', here, gone), false)
  assert.equal(movedAway([{ ...item, path: 'C:\\dev\\electron.exe' }], 'nest', here, gone), false) // from the sources
  assert.equal(movedAway([{ ...item, name: 'other' }], 'nest', here, gone), false)
  assert.equal(movedAway(undefined, 'nest', here, gone), false)
})
