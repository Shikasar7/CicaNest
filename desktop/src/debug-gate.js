'use strict'

// Whether the debug harness (debug.js) may start. It can
// answer the 电脑上做事 confirm windows and run script in the app's pages, so it is for a launch made for a
// test, never for the app as the user runs it every day:
// - NEST_DESKTOP_DEBUG_DIR set for this launch only — not kept in the user's or the machine's environment
//   (where one command could leave it for every launch after);
// - its folder not inside a folder a companion was given (where she could write its commands file).

const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

const NAME = 'NEST_DESKTOP_DEBUG_DIR'
const KEPT_IN = ['HKCU\\Environment', 'HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment']

function keptInEnvironment(run = execFileSync) {
  for (const key of KEPT_IN) {
    try {
      run('reg', ['query', key, '/v', NAME], { stdio: 'ignore', windowsHide: true, timeout: 5000 })
      return true
    } catch {
      // not there (or reg failed): the next one
    }
  }
  return false
}

function within(child, parent) {
  const c = path.resolve(child).toLowerCase()
  const p = path.resolve(parent).toLowerCase().replace(/[\\/]+$/, '')
  return c === p || c.startsWith(`${p}\\`) || c.startsWith(`${p}/`)
}

/** { ok, why }: may the harness start for `dir`? `executorFile`: 电脑上做事's settings (the folders). */
function debugAllowed(dir, executorFile, { kept = keptInEnvironment } = {}) {
  if (kept()) return { ok: false, why: `${NAME} 存在用户或系统的环境变量里（不是这次启动临时设的）` }
  let folders = []
  try {
    folders = JSON.parse(fs.readFileSync(executorFile, 'utf8')).folders ?? []
  } catch {
    // no settings yet
  }
  for (const f of folders) {
    if (f && typeof f.path === 'string' && within(dir, f.path)) {
      return { ok: false, why: `调试文件夹在给伙伴的授权文件夹（${f.path}）里面` }
    }
  }
  return { ok: true, why: '' }
}

module.exports = { debugAllowed, keptInEnvironment }
