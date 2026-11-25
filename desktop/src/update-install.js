'use strict'

// Installing a downloaded update over this app, and opening it again.
//
// For this user only, in a folder that can be written to: without a window. Installed for everyone, or the folder is
// not writable: the installer with its windows (it asks for an administrator). Not installed at all (a copied
// folder): only the link to the new version.

const fs = require('node:fs')
const path = require('node:path')
const { execFile, spawn } = require('node:child_process')

const UNINSTALL = '\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall'

function registered(root, uninstaller, run = execFile) {
  return new Promise((resolve) => {
    run('reg', ['query', root + UNINSTALL, '/s', '/d', '/f', uninstaller], { windowsHide: true, timeout: 10_000 }, (err) => resolve(!err))
  })
}

function canWrite(folder) {
  const file = path.join(folder, `.nest-try-${process.pid}`)
  try {
    fs.writeFileSync(file, '')
    fs.unlinkSync(file)
    return true
  } catch {
    return false
  }
}

/** How this app can be replaced: 'silent', 'everyone', 'window' or 'page' (not at all). */
async function howTo(execPath, { exists = fs.existsSync, find = registered, writable = canWrite } = {}) {
  const folder = path.dirname(execPath)
  const uninstaller = path.join(folder, `Uninstall ${path.basename(execPath)}`)
  if (!exists(uninstaller)) return 'page'
  if (await find('HKCU', uninstaller)) return writable(folder) ? 'silent' : 'window'
  if (await find('HKLM', uninstaller)) return 'everyone'
  return 'page'
}

const ARGS = {
  silent: ['/S', '--updated', '--force-run', '/currentuser'],
  everyone: ['--updated', '/allusers'],
  window: [],
}

/** Starts the installer; it goes on after this app has quit (`started` gets the process id first). When it does not
 * end well, what is left of the app is opened again, and says so. The paths are handed over in the environment: cmd
 * reads nothing in them. */
function runInstaller(file, how, execPath, { run = spawn, env = process.env } = {}) {
  const command = `"%NEST_UPDATE_SETUP%" ${ARGS[how].join(' ')} || if exist "%NEST_UPDATE_APP%" start "" "%NEST_UPDATE_APP%"`
  return new Promise((resolve, reject) => {
    const child = run(path.join(env.SystemRoot || 'C:\\Windows', 'System32', 'cmd.exe'), ['/d', '/s', '/v:off', '/c', `"${command}"`], {
      cwd: path.dirname(file), // not the folder that is being replaced
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
      windowsVerbatimArguments: true,
      env: { ...env, NEST_UPDATE_SETUP: file, NEST_UPDATE_APP: execPath },
    })
    child.on('error', reject)
    child.on('spawn', () => {
      child.unref()
      resolve(child.pid)
    })
  })
}

module.exports = { howTo, runInstaller }
