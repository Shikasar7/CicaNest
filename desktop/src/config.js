'use strict'

// Where nest lives and how to reach it. Read once at startup.
//
// Two ways to run:
//   local   this machine runs nest: the app starts it, signs the console in with its token, and stops it
//   remote  nest runs elsewhere (another computer): the app only opens its console and chat; you sign in with the
//           remote-access password, like a browser would. Set in the app (托盘 → 连接设置…, saved in
//           <profile>\connection.json) or with NEST_REMOTE_URL.
//
// Installed (an .exe made with `npm run dist`) there is no nest folder around it: it keeps its own files in the
// profile, and asks for the address of the nest to connect to when none is set.
//
// NEST_HOME   the nest folder (default: the folder that contains desktop/);
//             any other folder is a test copy and is labelled 「测试副本」 wherever the app shows itself
// NEST_PORT   the console port (default: NEST_PORT in <home>\.env, else 18800)
// NEST_PYTHON the Python that runs nest (default: <home>\.venv\Scripts\python.exe)
// NEST_REMOTE_URL  a nest elsewhere, e.g. http://My-Mac.local:18800 (overrides connection.json)
// NEST_DESKTOP_PROFILE  a separate Electron profile folder (window size, single-instance lock); for testing
// NEST_PETS_DIR    the 桌宠 sprite packs (default: <profile>\pets)

const fs = require('node:fs')
const path = require('node:path')

const DEFAULT_PORT = 18800

function validPort(value) {
  const port = Number.parseInt(String(value ?? '').trim(), 10)
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : null
}

// Only the NEST_PORT line is read; nothing else from .env is kept or logged (it holds API keys).
function portFromDotenv(home) {
  let text
  try {
    text = fs.readFileSync(path.join(home, '.env'), 'utf8')
  } catch {
    return null
  }
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?NEST_PORT\s*=\s*['"]?([0-9]+)['"]?\s*$/.exec(line)
    if (match) return validPort(match[1])
  }
  return null
}

/** A nest address as typed ("My-Mac.local:18800", "http://192.168.1.20:18800/"): its origin, or
 * null when it is not one. Only http(s), a host and a port; nothing after it. */
function remoteOrigin(value) {
  let text = String(value ?? '').trim()
  if (!text) return null
  if (!/^[a-z]+:\/\//i.test(text)) text = `http://${text}`
  let url
  try {
    url = new URL(text)
  } catch {
    return null
  }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null
  if ((url.pathname && url.pathname !== '/') || url.search || url.hash) return null
  if (!url.port) url.port = String(DEFAULT_PORT)
  return url.origin
}

function readConnection(file) {
  try {
    const saved = JSON.parse(fs.readFileSync(file, 'utf8'))
    return saved && typeof saved === 'object' ? saved : {}
  } catch {
    return {}
  }
}

const DEFAULT_HOME = path.resolve(__dirname, '..', '..')

/** `installed`: the profile folder of an installed app, null when it runs from the sources. */
function loadConfig(env = process.env, connection = {}, installed = null) {
  const usual = installed ? path.join(installed, 'home') : DEFAULT_HOME
  const home = path.resolve(env.NEST_HOME || usual)
  const port = validPort(env.NEST_PORT) ?? portFromDotenv(home) ?? DEFAULT_PORT
  const dataDir = path.join(home, 'data')
  const remote = remoteOrigin(env.NEST_REMOTE_URL) ?? remoteOrigin(connection.remote)
  // Another way to the same nest (Tailscale first, the home network second, say): tried when the first does
  // not answer at start.
  const other = remote && !remoteOrigin(env.NEST_REMOTE_URL) ? remoteOrigin(connection.other) : null
  return Object.freeze({
    home,
    isTestCopy: !remote && home.toLowerCase() !== path.resolve(usual).toLowerCase(),
    needsAddress: Boolean(installed) && !remote && !env.NEST_HOME,
    port,
    remote: Boolean(remote),
    origin: remote ?? `http://127.0.0.1:${port}`,
    origins: remote ? [remote, ...(other && other !== remote ? [other] : [])] : [],
    python: env.NEST_PYTHON ? path.resolve(env.NEST_PYTHON) : path.join(home, '.venv', 'Scripts', 'python.exe'),
    dataDir,
    logsDir: path.join(dataDir, 'logs'),
    runtimeFile: path.join(dataDir, 'runtime.json'),
    profileDir: env.NEST_DESKTOP_PROFILE ? path.resolve(env.NEST_DESKTOP_PROFILE) : null,
    debugDir: env.NEST_DESKTOP_DEBUG_DIR ? path.resolve(env.NEST_DESKTOP_DEBUG_DIR) : null,
    petsDir: env.NEST_PETS_DIR ? path.resolve(env.NEST_PETS_DIR) : null,
  })
}

module.exports = { loadConfig, readConnection, remoteOrigin, DEFAULT_PORT }
