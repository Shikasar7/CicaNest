'use strict'

// Where nest lives and how to reach it. Read once at startup.
//
// NEST_HOME   the nest folder (default: the folder that contains desktop/);
//             any other folder is a test copy and is labelled 「测试副本」 wherever the app shows itself
// NEST_PORT   the console port (default: NEST_PORT in <home>\.env, else 18800)
// NEST_PYTHON the Python that runs nest (default: <home>\.venv\Scripts\python.exe)
// NEST_DESKTOP_PROFILE  a separate Electron profile folder (window size, single-instance lock); for testing

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

const DEFAULT_HOME = path.resolve(__dirname, '..', '..')

function loadConfig(env = process.env) {
  const home = path.resolve(env.NEST_HOME || DEFAULT_HOME)
  const port = validPort(env.NEST_PORT) ?? portFromDotenv(home) ?? DEFAULT_PORT
  const dataDir = path.join(home, 'data')
  return Object.freeze({
    home,
    isTestCopy: home.toLowerCase() !== DEFAULT_HOME.toLowerCase(),
    port,
    origin: `http://127.0.0.1:${port}`,
    python: env.NEST_PYTHON ? path.resolve(env.NEST_PYTHON) : path.join(home, '.venv', 'Scripts', 'python.exe'),
    dataDir,
    logsDir: path.join(dataDir, 'logs'),
    runtimeFile: path.join(dataDir, 'runtime.json'),
    profileDir: env.NEST_DESKTOP_PROFILE ? path.resolve(env.NEST_DESKTOP_PROFILE) : null,
    debugDir: env.NEST_DESKTOP_DEBUG_DIR ? path.resolve(env.NEST_DESKTOP_DEBUG_DIR) : null,
  })
}

module.exports = { loadConfig }
