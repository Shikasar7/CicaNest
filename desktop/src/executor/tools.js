'use strict'

// The steps a companion can take on this PC, done after rules.js allowed them. Each is prepared first —
// what the confirm window shows (the action, the exact target, and for a change the diff, for a command
// the whole command) — and done only after that (and the user's nod where needed). Pure Node: what needs
// Electron (the Recycle Bin, opening with the default program) is handed in as `system`.

const fs = require('node:fs')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { FORBIDDEN_NAMES, HIDDEN_CHARS } = require('./rules')
const { PRELUDE } = require('./job')
const { diff, lines } = require('./diff')

const LIST_MAX = 200
const READ_BYTES = 256 * 1024
const READ_CHARS = 20_000
const WRITE_MAX = 1024 * 1024 // characters she may write in one go
const EDIT_BYTES = 2 * 1024 * 1024 // files larger than this are not edited in place
const NEW_SHOWN = 200 // lines of a new file shown in the window
const COMMAND_SECONDS = 120
const COMMAND_SECONDS_MAX = 600
const OUTPUT_CHARS = 12_000
// The only environment variables a command gets: what
// Windows and PowerShell need to work. Anything else this app was started with (keys, passwords in a
// proxy's or a database's address, this app's own settings) stays out.
const ENV_KEPT = new Set(
  [
    'PATH',
    'PATHEXT',
    'SystemRoot',
    'SystemDrive',
    'windir',
    'ComSpec',
    'TEMP',
    'TMP',
    'USERPROFILE',
    'USERNAME',
    'USERDOMAIN',
    'HOMEDRIVE',
    'HOMEPATH',
    'APPDATA',
    'LOCALAPPDATA',
    'PUBLIC',
    'ProgramData',
    'ALLUSERSPROFILE',
    'ProgramFiles',
    'ProgramFiles(x86)',
    'ProgramW6432',
    'CommonProgramFiles',
    'CommonProgramFiles(x86)',
    'CommonProgramW6432',
    'COMPUTERNAME',
    'OS',
    'PROCESSOR_ARCHITECTURE',
    'PROCESSOR_IDENTIFIER',
    'NUMBER_OF_PROCESSORS',
    'PSModulePath',
    'DriverData',
    'OneDrive',
  ].map((name) => name.toLowerCase()),
)
// Commands running now (each a PowerShell process): ended when the user switches 电脑上做事 off.
const running = new Set()

function size(bytes) {
  if (bytes < 1024) return `${bytes} 字节`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function stat(real) {
  try {
    return fs.statSync(real)
  } catch {
    return null
  }
}

// ---- looking -----------------------------------------------------------------------------------------

function list(real) {
  const info = fs.statSync(real)
  if (!info.isDirectory()) return `${real} 不是文件夹`
  const entries = fs
    .readdirSync(real, { withFileTypes: true })
    .filter((e) => !FORBIDDEN_NAMES.some((pattern) => pattern.test(e.name)))
    .sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name, 'zh-CN'))
  if (!entries.length) return `${real}（空的）`
  const shown = entries.slice(0, LIST_MAX).map((e) => {
    if (e.isDirectory()) return `${e.name}\\`
    if (e.isSymbolicLink()) return `${e.name}（链接）`
    try {
      return `${e.name}（${size(fs.statSync(path.join(real, e.name)).size)}）`
    } catch {
      return e.name
    }
  })
  const more = entries.length > LIST_MAX ? `\n……还有 ${entries.length - LIST_MAX} 项没列出` : ''
  return `${real}：\n${shown.join('\n')}${more}`
}

function read(real) {
  const info = fs.statSync(real)
  if (!info.isFile()) return `${real} 不是文件`
  const handle = fs.openSync(real, 'r')
  let data
  try {
    data = Buffer.alloc(Math.min(info.size, READ_BYTES))
    fs.readSync(handle, data, 0, data.length, 0)
  } finally {
    fs.closeSync(handle)
  }
  if (data.subarray(0, 8192).includes(0)) return `${real} 不是文字文件，读不了`
  let text = data.toString('utf8')
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1)
  const cut = text.length > READ_CHARS || info.size > READ_BYTES
  return cut ? `${text.slice(0, READ_CHARS)}\n……（后面略，文件共 ${size(info.size)}）` : text
}

// ---- changing files ----------------------------------------------------------------------------------

/** A text file as it is now: { text, bom, crlf, stamp } — or { error }. */
function current(real) {
  const info = stat(real)
  if (!info) return { text: null, stamp: null }
  if (!info.isFile()) return { error: `${real} 不是文件` }
  if (info.size > EDIT_BYTES) return { error: `${real} 太大了（${size(info.size)}），不在这里改` }
  const data = fs.readFileSync(real)
  if (data.subarray(0, 8192).includes(0)) return { error: `${real} 不是文字文件，不在这里改` }
  let text = data.toString('utf8')
  const bom = text.charCodeAt(0) === 0xfeff
  if (bom) text = text.slice(1)
  return { text, bom, crlf: /\r\n/.test(text), stamp: `${info.size}:${info.mtimeMs}` }
}

function unchanged(real, stamp) {
  const info = stat(real)
  return stamp === null ? !info : Boolean(info) && `${info.size}:${info.mtimeMs}` === stamp
}

function written(real, before, text) {
  const body = before.crlf ? text.replace(/\r?\n/g, '\r\n') : text
  fs.writeFileSync(real, (before.bom ? '﻿' : '') + body, 'utf8')
  return Buffer.byteLength(body, 'utf8')
}

const HIDDEN_ALL = new RegExp(HIDDEN_CHARS.source, 'g')

/** Text for the confirm window with what would show as nothing made visible: ⟦U+000D⟧. */
function plain(text) {
  return text.replace(HIDDEN_ALL, (c) => `⟦U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')}⟧`)
}

function changeOf(real, before, after) {
  let detail
  let summary
  if (before.text === null) {
    const all = lines(after)
    const shown = all.slice(0, NEW_SHOWN).map((line) => `+ ${line}`)
    if (all.length > NEW_SHOWN) shown.push(`……还有 ${all.length - NEW_SHOWN} 行没显示`)
    detail = `新文件，${all.length} 行：\n${shown.join('\n')}`
    summary = `新建，${all.length} 行`
  } else {
    const changed = diff(before.text, after)
    detail = changed.text
    summary = `加了 ${changed.added} 行，删了 ${changed.removed} 行`
  }
  // A file she writes may be run later (a script): what does not show is marked.
  if (HIDDEN_CHARS.test(detail)) {
    detail = `（内容里有看不见的字符，已经标成 ⟦U+…⟧）\n${plain(detail)}`
  }
  return { detail, summary }
}

function prepareWrite(decision, given) {
  const content = given?.content
  if (typeof content !== 'string') return { error: '没给要写的内容（content）' }
  if (content.length > WRITE_MAX) return { error: `内容太长了（最多 ${WRITE_MAX} 个字）` }
  const real = decision.real
  const before = current(real)
  if (before.error) return { error: before.error }
  const shown = changeOf(real, before, content)
  return {
    action: before.text === null ? '新建一个文件' : '改写一个文件（整个替换）',
    target: real,
    detail: shown.detail,
    detailKind: 'diff',
    run: async () => {
      if (!unchanged(real, before.stamp)) return '这个文件在确认的时候被别的程序改过了，没写。先重新读一遍再说。'
      const bytes = written(real, before, content)
      return `${before.text === null ? '新建了' : '写好了'}：${real}（${size(bytes)}，${shown.summary}）`
    },
  }
}

function prepareEdit(decision, given) {
  const old = given?.old
  const replacement = given?.new
  if (typeof old !== 'string' || !old) return { error: '没给要换掉的原文（old）' }
  if (typeof replacement !== 'string') return { error: '没给换成的新内容（new）' }
  const real = decision.real
  const before = current(real)
  if (before.error) return { error: before.error }
  if (before.text === null) return { error: `没有这个文件：${real}` }
  const find = (needle) => before.text.split(needle).length - 1
  let from = old
  let to = replacement
  if (find(from) === 0 && before.crlf) {
    from = old.replace(/\r?\n/g, '\r\n')
    to = replacement.replace(/\r?\n/g, '\r\n')
  }
  const count = find(from)
  if (count === 0) return { error: '文件里没找到这段原文（要一字不差，先读一遍文件）' }
  if (count > 1) return { error: `这段原文在文件里出现了 ${count} 次，多给一点上下文，让它只出现一次` }
  const after = before.text.replace(from, () => to)
  const shown = changeOf(real, before, after)
  return {
    action: '改一个文件里的一段',
    target: real,
    detail: shown.detail,
    detailKind: 'diff',
    run: async () => {
      if (!unchanged(real, before.stamp)) return '这个文件在确认的时候被别的程序改过了，没改。先重新读一遍再说。'
      // Written as it is now plus the change (no conversion of the line ends already in `after`).
      fs.writeFileSync(real, (before.bom ? '﻿' : '') + after, 'utf8')
      return `改好了：${real}（${shown.summary}）`
    },
  }
}

function count(real) {
  let files = 0
  let folders = 0
  const walk = (dir, depth) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.isDirectory() && !e.isSymbolicLink()) {
        folders += 1
        if (depth < 20 && files + folders < 100_000) walk(path.join(dir, e.name), depth + 1)
      } else files += 1
    }
  }
  try {
    walk(real, 0)
  } catch {
    // what could be counted
  }
  return { files, folders }
}

function prepareDelete(decision, given, system) {
  const real = decision.real
  const info = stat(real)
  if (!info) return { error: `没有这个文件或文件夹：${real}` }
  if (!system?.trash) return { error: '这台电脑上的桌面端删不了文件（没有回收站可用）' }
  let what = `文件，${size(info.size)}`
  if (info.isDirectory()) {
    const inside = count(real)
    what = `文件夹，里面有 ${inside.files} 个文件、${inside.folders} 个文件夹`
  }
  return {
    action: '删除（放进回收站，可以从回收站找回）',
    target: real,
    detail: what,
    run: async () => {
      await system.trash(real)
      return `放进回收站了：${real}`
    },
  }
}

// Copied file by file (not fs.cpSync / fs.rmSync: on Windows, Node 24's native versions crash the whole
// process on a folder with a Chinese name).
function copyTree(from, to) {
  const info = fs.lstatSync(from)
  if (info.isSymbolicLink()) throw new Error(`${from} 是链接，不跨磁盘搬`)
  if (info.isDirectory()) {
    fs.mkdirSync(to)
    for (const name of fs.readdirSync(from)) copyTree(path.join(from, name), path.join(to, name))
  } else {
    fs.copyFileSync(from, to, fs.constants.COPYFILE_EXCL)
  }
}

function prepareMove(decision, given, system) {
  const from = decision.real
  const info = stat(from)
  if (!info) return { error: `没有这个文件或文件夹：${from}` }
  let to = decision.to
  const there = stat(to)
  if (there?.isDirectory()) to = path.join(to, path.basename(from))
  if (stat(to)) return { error: `目标位置已经有同名的东西了：${to}（不覆盖）` }
  if (!stat(path.dirname(to))?.isDirectory()) return { error: `目标文件夹不存在：${path.dirname(to)}` }
  const across = path.parse(from).root.toLowerCase() !== path.parse(to).root.toLowerCase()
  return {
    action: path.dirname(from).toLowerCase() === path.dirname(to).toLowerCase() ? '改名' : '移动',
    target: `${from}\n→ ${to}`,
    run: async () => {
      if (stat(to)) return `目标位置已经有同名的东西了：${to}（不覆盖），没动。`
      if (across) {
        // Another drive: copied, then the original into the Recycle Bin (nothing is wiped).
        if (!system?.trash) return '跨磁盘移动要用回收站收走原件，这台电脑上用不了，没动。'
        copyTree(from, to)
        await system.trash(from)
      } else {
        fs.renameSync(from, to)
      }
      return `${from} → ${to}`
    },
  }
}

// ---- commands ----------------------------------------------------------------------------------------

function cleanEnv(env) {
  return Object.fromEntries(Object.entries(env).filter(([name]) => ENV_KEPT.has(name.toLowerCase())))
}

function end(child) {
  spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
}

/** Ends every command still running (电脑上做事 switched off). */
function stopCommands() {
  for (const child of running) end(child)
}

/** Runs a PowerShell command in `cwd`; what it printed (both streams), and how it ended. */
function runCommand({ command, cwd, seconds, env = process.env, shell = 'powershell.exe' }) {
  return new Promise((resolve) => {
    // The prelude (job.js), then the command as given (encoded, so no quoting of ours changes it).
    const script = `${PRELUDE}\n${command}`
    const encoded = Buffer.from(script, 'utf16le').toString('base64')
    const child = spawn(shell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], {
      cwd,
      env: cleanEnv(env),
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    running.add(child)
    const chunks = []
    let length = 0
    let cut = false
    const take = (chunk) => {
      if (length >= OUTPUT_CHARS * 4) {
        cut = true
        return
      }
      chunks.push(chunk)
      length += chunk.length
    }
    child.stdout.on('data', take)
    child.stderr.on('data', take)
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      end(child)
    }, seconds * 1000)
    const finish = (code, error) => {
      clearTimeout(timer)
      running.delete(child)
      let text = Buffer.concat(chunks).toString('utf8').replace(/\r\n/g, '\n').trimEnd()
      if (text.length > OUTPUT_CHARS || cut) text = `${text.slice(0, OUTPUT_CHARS)}\n……（后面的输出略）`
      const ended = error ? `没跑起来：${error.message}` : timedOut ? `超过 ${seconds} 秒，停掉了` : `退出码 ${code}`
      resolve(`${ended}\n${text || '（没有输出）'}`)
    }
    child.on('error', (error) => finish(null, error))
    child.on('close', (code) => finish(code, null))
  })
}

function prepareCommand(decision, given, system) {
  const asked = Number(given?.timeout)
  const seconds =
    Number.isFinite(asked) && asked > 0 ? Math.min(Math.round(asked), COMMAND_SECONDS_MAX) : COMMAND_SECONDS
  return {
    action: `跑一条 PowerShell 命令（最多 ${seconds} 秒，不提权）`,
    target: `工作目录：${decision.cwd}`,
    detail: decision.command,
    detailKind: 'command',
    run: () => (system?.runCommand ?? runCommand)({ command: decision.command, cwd: decision.cwd, seconds }),
  }
}

// ---- opening -----------------------------------------------------------------------------------------

function prepareOpen(decision, given, system) {
  if (!system?.open) return { error: '这台电脑上的桌面端打不开东西' }
  if (decision.url) {
    return {
      action: '用浏览器打开一个网址',
      target: decision.url,
      run: async () => {
        await system.open({ url: decision.url })
        return `打开了：${decision.url}`
      },
    }
  }
  const info = stat(decision.real)
  if (!info) return { error: `没有这个文件或文件夹：${decision.real}` }
  return {
    action: info.isDirectory() ? '在资源管理器里打开一个文件夹' : '用默认程序打开一个文件',
    target: decision.real,
    run: async () => {
      const failed = await system.open({ path: decision.real })
      return failed ? `没打开：${failed}` : `打开了：${decision.real}`
    },
  }
}

// ---- all of them -------------------------------------------------------------------------------------

const PREPARE = {
  win_list_files: (decision) => ({
    action: '看一个文件夹里有什么',
    target: decision.real,
    run: async () => list(decision.real),
  }),
  win_read_file: (decision) => ({
    action: '读一个文件',
    target: decision.real,
    run: async () => read(decision.real),
  }),
  win_write_file: prepareWrite,
  win_edit_file: prepareEdit,
  win_delete: prepareDelete,
  win_move: prepareMove,
  win_run_command: prepareCommand,
  win_open: prepareOpen,
}

/** { error } or { action, target, detail?, detailKind?, run() -> Promise<text> } for an allowed step. */
function prepare(tool, decision, given, system) {
  try {
    return PREPARE[tool](decision, given, system)
  } catch (err) {
    return { error: err.code === 'ENOENT' ? '没有这个文件或文件夹' : err.message }
  }
}

module.exports = { prepare, list, read, runCommand, cleanEnv, stopCommands, plain }
