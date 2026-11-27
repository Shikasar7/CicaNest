'use strict'

// `npm run backend`: everything the installed app needs to run nest on a computer without Python, uv or Node,
// in out\backend (electron-builder puts it into the installer as resources\backend):
//
//   python\   a standalone Python 3.12 (uv's), nest and its locked dependencies in its site-packages
//   web\      the built console (web\dist: run `npm run build` in web first)
//   LICENSE   nest's own; the dependencies' are in their .dist-info folders
//
// Needs uv. Nothing in it may name this machine: the installer is published (no direct_url.json, no build paths
// in the compiled files, no launchers).
//
// For the system it runs on (uv fetches that system's Python): on Windows for the .exe, on a Mac with Apple
// silicon for the .dmg. The two lay Python out differently (python.exe, Lib\ vs bin/python3, lib/python3.12/).

const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

const PYTHON = '3.12'
const DESKTOP = path.resolve(__dirname, '..')
const REPO = path.resolve(DESKTOP, '..')
const OUT = path.join(DESKTOP, 'out', 'backend')
const WORK = path.join(DESKTOP, 'out', 'backend-work')
const WINDOWS = process.platform === 'win32'
const { pythonIn } = require('../src/config')

// The longest path in OUT: with the install folder (installer.nsh: NEST_LONGEST) and resources/backend/ in front it
// has to stay under Windows' 260.
const LONGEST = 115

// The standard library: Lib on Windows, lib/python3.12 elsewhere; its compiled modules are in DLLs or lib-dynload.
const STDLIB = WINDOWS ? 'Lib' : `lib/python${PYTHON}`
const MODULES = WINDOWS ? 'DLLs' : `${STDLIB}/lib-dynload`
// Parts of the standard library nest never uses (tkinter and its Tcl, the tests, pip's bootstrap, …).
const UNUSED = [
  'include',
  ...(WINDOWS ? ['Scripts', 'libs', 'tcl', 'pythonw.exe'] : ['share', 'lib/pkgconfig']),
  ...['idlelib', 'tkinter', 'turtledemo', 'turtle.py', 'ensurepip', 'test', 'lib2to3', 'pydoc_data', 'venv'].map((n) => `${STDLIB}/${n}`),
  ...(WINDOWS ? ['_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll'] : []).map((n) => `${MODULES}/${n}`),
  `${STDLIB}/EXTERNALLY-MANAGED`,
]
// What may stay in bin (macOS): the interpreter. pip's and the packages' launchers name this machine's paths.
const KEEP_IN_BIN = new Set(['python3', `python${PYTHON}`, 'python'])

function run(command, args, options = {}) {
  const done = spawnSync(command, args, { encoding: 'utf8', windowsHide: true, ...options })
  if (done.status !== 0) {
    throw new Error(`${command} ${args.join(' ')}\n${done.error?.message || ''}${done.stdout || ''}${done.stderr || ''}`)
  }
  return done.stdout
}

function remove(target) {
  fs.rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}

function walk(dir, each) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (each(full, true) !== false) walk(full, each)
    } else {
      each(full, false)
    }
  }
}

/** macOS: uv wrote where it installed Python into the library's own name and the build settings; give them
 * back python-build-standalone's /install (nothing reads them at run time; the library is found through bin/'s
 * python, which has it built in) and sign the library again, as changing it undid its (ad-hoc) signature. */
function unnameMachine(installedAt, python) {
  const lib = path.join(python, STDLIB)
  for (const name of fs.readdirSync(lib).filter((n) => /^_sysconfigdata_.*\.py$/.test(n))) {
    const file = path.join(lib, name)
    fs.writeFileSync(file, fs.readFileSync(file, 'utf8').replaceAll(installedAt, '/install'))
  }
  const dylib = path.join(python, 'lib', `libpython${PYTHON}.dylib`)
  if (fs.existsSync(dylib)) {
    run('install_name_tool', ['-id', `@rpath/libpython${PYTHON}.dylib`, dylib])
    run('codesign', ['--force', '--sign', '-', dylib])
  }
}

function version() {
  const project = fs.readFileSync(path.join(REPO, 'pyproject.toml'), 'utf8')
  const backend = /^version = "([^"]+)"/m.exec(project)[1]
  const app = require(path.join(DESKTOP, 'package.json')).version
  if (backend !== app) throw new Error(`版本不一致：pyproject.toml 是 ${backend}，desktop/package.json 是 ${app}`)
  return backend
}

function build() {
  const nest = version()
  const web = path.join(REPO, 'web', 'dist')
  if (!fs.existsSync(path.join(web, 'index.html'))) throw new Error('没有 web/dist：先在 web 里运行 npm ci 和 npm run build')
  remove(OUT)
  remove(WORK)
  fs.mkdirSync(OUT, { recursive: true })
  fs.mkdirSync(WORK, { recursive: true })

  // uv checks the download's hash. Into a folder of its own: the one uv keeps for this machine stays untouched.
  console.log(`Python ${PYTHON}`)
  const installs = path.join(WORK, 'python')
  run('uv', ['python', 'install', PYTHON, '--install-dir', installs, '--no-bin', '--no-registry', '--managed-python'])
  const found = fs.readdirSync(installs, { withFileTypes: true }).find((e) => e.isDirectory() && e.name.startsWith(`cpython-${PYTHON}.`))
  if (!found) throw new Error(`uv 没有装好 Python ${PYTHON}`)
  const python = path.join(OUT, 'python')
  fs.renameSync(path.join(installs, found.name), python)
  const exe = pythonIn(python)
  for (const name of UNUSED) remove(path.join(python, name))
  for (const name of fs.readdirSync(path.join(python, MODULES))) {
    if (/^_(test|ctypes_test|tkinter)/.test(name)) remove(path.join(python, MODULES, name))
  }
  if (!WINDOWS) {
    // Tcl/Tk and its packages (tcl9, itcl4.3.8, thread3.0.6…), only for tkinter
    for (const name of fs.readdirSync(path.join(python, 'lib'))) {
      if (/^(lib)?(tcl|tk|itcl|thread)\d/.test(name)) remove(path.join(python, 'lib', name))
    }
    unnameMachine(path.join(installs, found.name), python)
  }
  const site = path.join(python, STDLIB, 'site-packages')
  remove(site)
  fs.mkdirSync(site, { recursive: true })

  console.log(`nest ${nest} 和依赖`)
  const locked = path.join(WORK, 'requirements.txt')
  run('uv', ['export', '--frozen', '--no-dev', '--no-emit-project', '-q', '-o', locked], { cwd: REPO })
  run('uv', ['pip', 'install', '-q', '--python', exe, '--require-hashes', '--no-compile-bytecode', '-r', locked])
  run('uv', ['build', '--wheel', '-q', '-o', WORK], { cwd: REPO })
  const wheel = fs.readdirSync(WORK).find((name) => name.endsWith('.whl'))
  run('uv', ['pip', 'install', '-q', '--python', exe, '--no-deps', '--no-compile-bytecode', path.join(WORK, wheel)])
  remove(path.join(python, 'Scripts'))
  remove(path.join(site, 'bin'))
  if (!WINDOWS) {
    for (const name of fs.readdirSync(path.join(python, 'bin'))) {
      if (!KEEP_IN_BIN.has(name)) remove(path.join(python, 'bin', name))
    }
  }
  walk(python, (full, dir) => {
    if (dir && path.basename(full) === '__pycache__') {
      remove(full)
      return false
    }
    if (!dir && path.basename(full) === 'direct_url.json') remove(full) // names the folder it was built in
    return true
  })
  // Compiled now: the app's folder is not written to while it runs. unchecked-hash: no source times to compare
  // (unpacking changes them). -s: the paths in tracebacks start at the standard library's folder.
  spawnSync(exe, ['-I', '-B', '-m', 'compileall', '-q', '-f', '-j', '0', '-s', python, '--invalidation-mode', 'unchecked-hash', path.join(python, STDLIB)], {
    stdio: 'ignore',
    windowsHide: true,
  })

  fs.cpSync(web, path.join(OUT, 'web'), { recursive: true })
  fs.copyFileSync(path.join(REPO, 'LICENSE'), path.join(OUT, 'LICENSE'))
  const others = path.join(REPO, 'web', 'THIRD-PARTY-LICENSES.txt') // the console's, when the release script made it
  if (fs.existsSync(others)) fs.copyFileSync(others, path.join(OUT, 'THIRD-PARTY-LICENSES.txt'))

  check(exe, nest)
  remove(WORK)
}

/** It runs, as the version it should be, and no file in it names the folder it was built in. */
function check(exe, nest) {
  const said = run(exe, ['-I', '-B', '-m', 'nest', '--version']).trim()
  if (said !== `nest ${nest}`) throw new Error(`打包的后台报的版本是 ${said}`)
  // Importing every module of nest finds a dependency that was left out (or cut away above).
  run(exe, [
    '-I',
    '-B',
    '-c',
    'import importlib, pkgutil, nest\n' +
      'for m in pkgutil.walk_packages(nest.__path__, "nest."):\n' +
      '    if not m.name.endswith("__main__"): importlib.import_module(m.name)',
  ])
  const here = [REPO, REPO.replaceAll('\\', '/'), REPO.replaceAll('\\', '\\\\'), process.env.USERPROFILE || process.env.HOME]
    .filter(Boolean)
    .map((text) => Buffer.from(text.toLowerCase()))
  const bad = []
  const long = []
  let bytes = 0
  walk(OUT, (full, dir) => {
    if (dir) return true
    if (fs.lstatSync(full).isSymbolicLink()) return true // bin/python3 -> python3.12
    if (path.relative(OUT, full).length > LONGEST) long.push(path.relative(OUT, full))
    const data = fs.readFileSync(full)
    bytes += data.length
    const lower = Buffer.from(data.toString('latin1').toLowerCase(), 'latin1')
    if (here.some((text) => lower.includes(text))) bad.push(path.relative(OUT, full))
    return true
  })
  if (bad.length) throw new Error(`这些文件里有本机路径：\n${bad.slice(0, 20).join('\n')}`)
  if (long.length) throw new Error(`这些路径太长：${long.join('、')}`)
  console.log(`out/backend：${Math.round(bytes / 1024 / 1024)} MB`)
}

try {
  build()
} catch (err) {
  console.error(err.message)
  process.exit(1)
}
