'use strict'

// What a companion may do on this PC. Kept here, on the PC, and decided here: nest
// only asks. Pure (no Electron), so it is tested with node --test.
//
// - Paths are judged after links and junctions are resolved (fs.realpathSync.native), so a link inside an
//   authorized folder cannot lead out of it, and nothing reaches the never-list through a side door.
// - The never-list (FORBIDDEN_NAMES, forbiddenRoots) is refused outright: no window, nothing to allow.
// - Inside a folder the user authorized: looking is automatic. Anywhere else: the user's nod, every time
//   (or, if they said so, for the rest of that answer or task, for the same tool in the same folder).
// - Changing files (write, edit, delete to the Recycle Bin, move), commands and opening: the user's nod
//   everywhere, shown exactly (the diff, the whole command). A read-only folder is not changed at all.
//   A folder the user marked 改不用问 (freeChange) is changed without a nod — also in a routine, since the
//   user chose that folder by itself: a move only when both ends are in such folders.
//   "同类" is the same tool in the same folder (a move: from the same folder into the same one, and only
//   into an authorized folder). Commands have no "同类": each one is asked
//   ("the same program" is no limit when `a; b` starts with `a`).
// - 完全允许 (allowAll, the user's own switch on this PC): no nod at all — file tools, commands and opening
//   (the window that turns it on says plainly that a command is not held by the never-list). A program
//   is still never run by opening it, and the command guard list still holds unless the never-list is open
//   too. Only with 完全允许 may the user also open the never-list (guard: false). Paths are still resolved
//   and must still be full local ones. A routine's step is judged as if 完全允许 were off (index.js).
// - What the user is shown is what runs: a command may not carry characters that show as nothing or as
//   something else (a lone carriage return PowerShell reads as a new line, direction marks).

const fs = require('node:fs')
const path = require('node:path')

// Names of the kind that hold keys, anywhere on the way.
const FORBIDDEN_NAMES = [
  /^\.env$/i,
  /^\.env\..+$/i,
  /\.pem$/i,
  /\.key$/i,
  /\.kdbx?$/i,
  /\.ppk$/i,
  /^id_rsa/i,
  /^id_ed25519/i,
  /^id_ecdsa/i,
  /^credentials/i,
  /^auth\.json$/i,
  /^\.netrc$/i,
  /^\.git-credentials$/i,
  /^login data/i,
  /^cookies$/i,
  /^\.npmrc$/i,
  /^\.pypirc$/i,
  /^\.pgpass$/i,
  /^hosts\.yml$/i, // GitHub CLI's tokens
]

// Characters that show as nothing, or move what is around them (a lone \r: PowerShell starts a new line
// there, the window does not). Tabs and line feeds are fine.
const HIDDEN_CHARS = /[\x00-\x08\x0b-\x1f\x7f\u0085\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2069\ufeff]/

// look: automatic inside an authorized folder. change: asked everywhere, refused in a read-only folder.
// run / open: asked everywhere. (完全允许: none asked.)
const LEVELS = {
  win_list_files: 'look',
  win_read_file: 'look',
  win_write_file: 'change',
  win_edit_file: 'change',
  win_delete: 'change',
  win_move: 'change',
  win_run_command: 'run',
  win_open: 'open',
}

// Which given fields are paths, judged one by one (the first is where "同类" is counted).
const PATHS = {
  win_list_files: ['path'],
  win_read_file: ['path'],
  win_write_file: ['path'],
  win_edit_file: ['path'],
  win_delete: ['path'],
  win_move: ['path', 'to'],
  win_open: ['target'],
}

// Commands that change the PC's safety or startup, or end it. Every command is asked
// anyway: this list cannot catch every way of writing one, it is one more guard. Lifted with the never-list.
const BLOCKED_COMMANDS = [
  /\bnetsh\b/i,
  /\b(new|set|remove|enable|disable)-netfirewall/i,
  /\b(set|add|remove)-mppreference\b/i,
  /\bschtasks\b/i,
  /\b(register|new|set|enable)-scheduledtask\b/i,
  /currentversion\\run/i,
  /\\start menu\\programs\\startup|shell:startup/i,
  /\brunas\b/i,
  /-verb\s+['"]?runas/i,
  /\bsudo\b/i,
  /\b(shutdown|restart-computer|stop-computer)\b/i,
  /\b(format-volume|clear-disk|initialize-disk|diskpart|bcdedit|vssadmin)\b/i,
  /(^|[\s;|&])format(\.com)?\s+[a-z]:/i,
  /\breg(\.exe)?\s+(add|delete|import|load|restore)\b/i,
  /\b(set|new|remove)-item(property)?\b[^;|]*\bhk(lm|cu|ey_)/i,
  /\bset-executionpolicy\b/i,
  /\b(wevtutil\s+cl|clear-eventlog)\b/i,
  /\bregister-scheduledjob\b/i,
  /-v(e(rb?)?)?[\s:]+['"]?runas/i, // -Verb RunAs, also abbreviated or with a colon
  /getfolderpath\(\s*['"]?(common)?startup/i,
  /\bNEST_(DESKTOP_|REMOTE_URL|HOME)/i, // this app's debug channel and which nest it trusts
  /\bexecutor(-steps)?\.json\b/i, // these settings
]
const COMMAND_MAX = 4000

// Opened, these run something (or connect somewhere and hand over the Windows sign-in): they go through
// win_run_command, where the user sees the whole command. Refused even under 完全允许.
const PROGRAM_TYPES =
  /\.(exe|com|bat|cmd|ps1|psm1|psd1|ps1xml|psc1|vbs|vbe|js|jse|wsf|wsh|wsc|sct|hta|msi|msp|mst|scr|pif|lnk|url|website|reg|jar|jnlp|cpl|msc|inf|appref-ms|application|appinstaller|appx|appxbundle|msix|msixbundle|chm|hlp|rdp|theme|themepack|deskthemepack|search-ms|searchconnector-ms|library-ms|settingcontent-ms|scf|iqy|slk|diagcab|gadget|xll|xbap|mof|vhd|vhdx|iso|img|py|pyw|pyz)$/i
// Opened, these only show something: asked as usual (and not under 完全允许). Any other kind is asked
// every time, 完全允许 or not: the window says it may run something.
const DOCUMENT_TYPES =
  /\.(txt|md|log|csv|tsv|json|xml|yaml|yml|toml|ini|cfg|conf|pdf|epub|doc|docx|xls|xlsx|ppt|pptx|odt|ods|odp|rtf|wps|et|dps|png|jpe?g|gif|bmp|webp|svg|ico|tiff?|heic|avif|psd|kra|blend|mp3|wav|flac|m4a|aac|ogg|opus|mp4|mkv|mov|avi|webm|wmv|flv|zip|7z|rar|tar|gz|bz2|xz|html?|css|ts|tsx|jsx|java|c|cpp|h|hpp|cs|go|rs|rb|php|sql|srt|ass|vtt|ttf|otf|woff2?)$/i

/** The folders nothing may touch: keys, credentials, browsers' and password managers' data, nest's own. */
function forbiddenRoots(env = process.env, extra = []) {
  const home = env.USERPROFILE || env.HOME || ''
  const roaming = env.APPDATA || (home && path.join(home, 'AppData', 'Roaming'))
  const local = env.LOCALAPPDATA || (home && path.join(home, 'AppData', 'Local'))
  const found = [
    ...['.ssh', '.aws', '.azure', '.gnupg', '.docker', '.kube', '.codex', '.claude', '.config', '.nest'].map(
      (name) => home && path.join(home, name),
    ),
    roaming && path.join(roaming, 'Microsoft', 'Credentials'),
    roaming && path.join(roaming, 'Microsoft', 'Protect'),
    roaming && path.join(roaming, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup'),
    local && path.join(local, 'Microsoft', 'Credentials'),
    local && path.join(local, 'Microsoft', 'Vault'),
    local && path.join(local, 'Google', 'Chrome', 'User Data'),
    local && path.join(local, 'Microsoft', 'Edge', 'User Data'),
    local && path.join(local, 'BraveSoftware'),
    roaming && path.join(roaming, 'Mozilla'),
    roaming && path.join(roaming, 'Opera Software'),
    roaming && path.join(roaming, 'Bitwarden'),
    local && path.join(local, '1Password'),
    roaming && path.join(roaming, 'KeePass'),
    roaming && path.join(roaming, 'KeePassXC'),
    roaming && path.join(roaming, 'Tencent'), // QQ's data
    home && path.join(home, 'Documents', 'Tencent Files'), // QQ's chats and received files
    home && path.join(home, 'Documents', 'WeChat Files'),
    roaming && path.join(roaming, 'GitHub CLI'),
    roaming && path.join(roaming, 'Claude'),
    roaming && path.join(roaming, 'discord'),
    roaming && path.join(roaming, 'Telegram Desktop'),
    roaming && path.join(roaming, 'Code', 'User', 'globalStorage'), // VS Code extensions' tokens
    local && path.join(local, 'Chromium', 'User Data'),
    local && path.join(local, 'Vivaldi', 'User Data'),
    local && path.join(local, 'Yandex', 'YandexBrowser', 'User Data'),
    local && path.join(local, 'Packages', 'TheBrowserCompany.Arc_ttt1ap7aakyb4'),
    env.SystemRoot || 'C:\\Windows',
    ...extra,
  ]
  return [...new Set(found.filter(Boolean).map((p) => path.resolve(p)))]
}

function same(a, b) {
  return a.toLowerCase() === b.toLowerCase()
}

/** Whether `child` is `parent` or inside it (Windows paths: no case). */
function inside(child, parent) {
  const c = child.toLowerCase()
  const p = parent.toLowerCase().replace(/[\\/]+$/, '')
  return c === p || c.startsWith(p + path.sep) || c.startsWith(p + '/')
}

function real(target) {
  try {
    return fs.realpathSync.native(target)
  } catch {
    return null
  }
}

/** Where `target` really is: itself resolved, or (not there yet) its folder resolved plus its name. */
function resolve(target) {
  const found = real(target)
  if (found) return found
  const folder = real(path.dirname(target))
  return folder ? path.join(folder, path.basename(target)) : null
}

function share(place) {
  return place.startsWith('\\\\') || place.startsWith('//')
}

function isFolder(place) {
  try {
    return fs.statSync(place).isDirectory()
  } catch {
    return false
  }
}

/**
 * Judge one path. `folders`: [{ path, readOnly }] the user authorized. Returns
 * { ok: false, why } — never; or { ok: true, real, folder (the authorized one it is in, or null), readOnly,
 * free (that folder is 改不用问) }.
 */
function judge(given, { folders = [], forbidden = forbiddenRoots(), guard = true } = {}) {
  const text = String(given ?? '').trim()
  if (!text) return { ok: false, why: '没给路径' }
  if (/[\x00-\x1f]/.test(text) || HIDDEN_CHARS.test(text)) return { ok: false, why: '路径里有不能用的字符' }
  if (share(text)) return { ok: false, why: '不碰网络共享（\\\\ 开头的路径）' }
  if (!path.win32.isAbsolute(text) || !/^[a-zA-Z]:[\\/]/.test(text)) {
    return { ok: false, why: '要写完整路径，比如 D:\\资料\\清单.txt' }
  }
  const absolute = path.resolve(text)
  const where = resolve(absolute)
  if (!where) return { ok: false, why: `没有这个地方：${absolute}` }
  // A link that leads onto another machine (\\server\share): not followed (that would hand over the sign-in).
  if (share(where)) return { ok: false, why: '这个路径通到网络共享上，不碰' }
  for (const place of guard ? [absolute, where] : []) {
    const parts = place.split(/[\\/]+/)
    if (parts.some((part) => FORBIDDEN_NAMES.some((pattern) => pattern.test(part)))) {
      return { ok: false, why: '这类文件（密钥、凭据、.env）不能碰' }
    }
    const root = forbidden.find((r) => inside(place, real(r) || r) || inside(place, r))
    if (root)
      return { ok: false, why: '这个地方（密钥、凭据、浏览器或密码管理器的数据、系统文件夹、nest 自己的数据）不能碰' }
  }
  let folder = null
  for (const f of folders) {
    const root = real(f.path)
    if (root && inside(where, root) && (!folder || root.length > folder.root.length)) {
      folder = { root, readOnly: Boolean(f.readOnly), free: !f.readOnly && f.freeChange === true, path: f.path }
    }
  }
  return {
    ok: true,
    real: where,
    folder: folder ? folder.root : null,
    readOnly: folder ? folder.readOnly : false,
    free: folder ? folder.free : false,
  }
}

/**
 * What to do with a step: { level: 'deny' | 'auto' | 'confirm', why, kind, kindLabel, real, to, url, cwd,
 * command, program }. `kind` is what "同类" means for it (null: no "同类", asked each time), `kindLabel`
 * the same in words for the window.
 */
function decide(tool, given, options = {}) {
  const level = LEVELS[tool]
  if (!level) return { level: 'deny', why: `这台电脑不认识 ${tool} 这一步` }
  const asked = (decision, why) =>
    options.allowAll ? { ...decision, level: 'auto' } : { ...decision, level: 'confirm', why }
  if (level === 'run') return decideCommand(given, options)
  if (tool === 'win_open' && /^https?:\/\//i.test(String(given?.target ?? '').trim())) {
    return decideUrl(String(given.target).trim(), asked)
  }
  const judged = []
  for (const key of PATHS[tool]) {
    const one = judge(given?.[key], options)
    if (!one.ok) return { level: 'deny', why: key === 'to' ? `目标位置：${one.why}` : one.why }
    judged.push(one)
  }
  const [first, second] = judged
  // "同类": the same tool in the folder the file is in — not the whole authorized folder.
  const place = tool === 'win_list_files' ? first.real : path.dirname(first.real)
  const decision = {
    real: first.real,
    to: second?.real,
    kind: `${tool}|${place.toLowerCase()}`,
    kindLabel: `同一种事，在同一个文件夹（${place}）里`,
  }
  if (tool === 'win_move') {
    // From the same folder into the same folder, and only into an authorized one: a move out of them (a
    // synced cloud folder, a share) is asked every time.
    const there = isFolder(second.real) ? second.real : path.dirname(second.real)
    decision.kind = second.folder ? `${tool}|${place.toLowerCase()}|${there.toLowerCase()}` : null
    decision.kindLabel = second.folder ? `从 ${place} 移到 ${there}` : ''
  }
  if (level === 'look') {
    if (first.folder) return { ...decision, level: 'auto' }
    return asked(decision, '不在你授权的文件夹里')
  }
  if (level === 'change') {
    if (judged.some((j) => j.readOnly)) return { level: 'deny', why: '那个授权文件夹标了只读，不能改' }
    if (tool === 'win_delete' || tool === 'win_move') {
      const root = path.parse(first.real).root
      if (same(first.real, root) || same(first.real, first.folder || '')) {
        return { level: 'deny', why: '不能删除或移走整个磁盘、整个授权文件夹' }
      }
    }
    if (judged.every((j) => j.free)) return { ...decision, level: 'auto' }
    return asked(decision, '会改动这台电脑上的文件')
  }
  // open: a program is never run this way (完全允许 or not); a kind this does not know is asked each time.
  if (PROGRAM_TYPES.test(first.real)) {
    return {
      level: 'deny',
      why: '这是程序、脚本或会连到别处的文件，打开就是运行它：要跑请用 win_run_command（你会看到完整命令）',
    }
  }
  if (!isFolder(first.real) && !DOCUMENT_TYPES.test(first.real)) {
    const unusual = { ...decision, kind: null, kindLabel: '' }
    return options.allowAll
      ? { ...unusual, level: 'auto' }
      : { ...unusual, level: 'confirm', why: '这种文件不常见，打开后可能会运行程序' }
  }
  return asked(decision, '会用默认程序打开')
}

function decideUrl(url, asked) {
  let parsed
  try {
    parsed = new URL(url)
  } catch {
    return { level: 'deny', why: '网址写得不对' }
  }
  if (url.length > 2000) return { level: 'deny', why: '网址太长了' }
  if (HIDDEN_CHARS.test(url)) return { level: 'deny', why: '网址里有看不见的字符' }
  const host = parsed.host.toLowerCase()
  const label = `打开同一个网站（${host}）的网址`
  return asked({ url: parsed.href, kind: `win_open|${host}`, kindLabel: label }, '会用浏览器打开网址')
}

/** The program a command starts with (its name only, lower case), for the log. */
function programOf(command) {
  const first = command.replace(/^[\s&.]+/, '').match(/^(?:"([^"]+)"|'([^']+)'|(\S+))/)
  const word = (first?.[1] ?? first?.[2] ?? first?.[3] ?? '').replace(/[;|].*$/, '')
  return path.win32
    .basename(word)
    .toLowerCase()
    .replace(/\.exe$/, '')
}

/** A command: asked every time (done without asking under 完全允许), never "同类". Its line ends made plain
 * (\n). */
function decideCommand(given, options) {
  const command = String(given?.command ?? '')
    .replace(/\r\n/g, '\n')
    .trim()
  if (!command) return { level: 'deny', why: '没给命令' }
  if (command.length > COMMAND_MAX) return { level: 'deny', why: `命令太长了（最多 ${COMMAND_MAX} 个字）` }
  if (HIDDEN_CHARS.test(command)) {
    return {
      level: 'deny',
      why: '命令里有看不见的字符（单独的回车、方向符号、零宽字符），窗口里显示的会和真正跑的不一样',
    }
  }
  const cwd = judge(given?.cwd, options)
  if (!cwd.ok) return { level: 'deny', why: `工作目录：${cwd.why}` }
  if (!isFolder(cwd.real)) return { level: 'deny', why: `工作目录不是文件夹：${cwd.real}` }
  if (options.guard !== false && BLOCKED_COMMANDS.some((pattern) => pattern.test(command))) {
    return {
      level: 'deny',
      why: '这类命令不能跑：改防火墙、杀毒、开机启动、计划任务、注册表、提权、关机重启、格式化、这个应用自己的设置',
    }
  }
  const program = programOf(command)
  const decision = { cwd: cwd.real, command, program, kind: null, kindLabel: '' }
  return options.allowAll
    ? { ...decision, level: 'auto' }
    : { ...decision, level: 'confirm', why: '要在这台电脑上跑命令' }
}

module.exports = {
  FORBIDDEN_NAMES,
  HIDDEN_CHARS,
  LEVELS,
  BLOCKED_COMMANDS,
  forbiddenRoots,
  judge,
  decide,
  programOf,
  inside,
  same,
}
