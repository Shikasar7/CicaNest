'use strict'

// 电脑上做事: the rules kept on this PC and the loop that asks nest for steps.
// node --test test/

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { decide, judge, forbiddenRoots } = require('../src/executor/rules')
const { Executor } = require('../src/executor')

function scratch() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-exec-'))
  const shared = path.join(root, '资料')
  const outside = path.join(root, 'outside')
  const secret = path.join(root, 'secret-home')
  for (const dir of [shared, outside, secret]) fs.mkdirSync(dir)
  fs.writeFileSync(path.join(shared, '清单.txt'), '1. 订票\n2. 带伞\n', 'utf8')
  fs.writeFileSync(path.join(outside, 'notes.txt'), '外面的', 'utf8')
  fs.writeFileSync(path.join(secret, 'key.txt'), '不该看到', 'utf8')
  // A link inside the authorized folder that leads out of it (a junction: no admin right needed).
  fs.symlinkSync(secret, path.join(shared, 'link'), 'junction')
  const options = { folders: [{ path: shared, readOnly: true }], forbidden: [secret] }
  return { root, shared, outside, secret, options }
}

test('inside an authorized folder looking is automatic; elsewhere it asks; the never-list is refused', () => {
  const { shared, outside, secret, options } = scratch()
  assert.equal(decide('win_read_file', { path: path.join(shared, '清单.txt') }, options).level, 'auto')
  assert.equal(decide('win_list_files', { path: shared }, options).level, 'auto')
  const asked = decide('win_read_file', { path: path.join(outside, 'notes.txt') }, options)
  assert.equal(asked.level, 'confirm')
  assert.match(asked.kind, /^win_read_file\|/)
  // Through the junction in the authorized folder: judged where it really leads, which is forbidden.
  const through = decide('win_read_file', { path: path.join(shared, 'link', 'key.txt') }, options)
  assert.equal(through.level, 'deny')
  assert.equal(decide('win_list_files', { path: secret }, options).level, 'deny')
  for (const bad of ['.env', 'id_rsa', 'server.pem', 'credentials.json', 'vault.kdbx']) {
    fs.writeFileSync(path.join(shared, bad), 'x')
    assert.equal(decide('win_read_file', { path: path.join(shared, bad) }, options).level, 'deny', bad)
  }
  for (const bad of ['', 'relative\\path.txt', '\\\\server\\share\\x', '/etc/passwd', 'D:\\a\u0000b']) {
    assert.equal(judge(bad, options).ok, false, bad)
  }
  assert.equal(decide('win_format_disk', { path: shared }, options).level, 'deny')
})

test('the never-list covers keys, browsers, password managers, QQ and Windows', () => {
  const env = {
    USERPROFILE: 'C:\\Users\\a',
    APPDATA: 'C:\\Users\\a\\AppData\\Roaming',
    LOCALAPPDATA: 'C:\\Users\\a\\AppData\\Local',
    SystemRoot: 'C:\\Windows',
  }
  const roots = forbiddenRoots(env, ['D:\\nest-data']).map((r) => r.toLowerCase())
  for (const must of [
    'c:\\users\\a\\.ssh',
    'c:\\users\\a\\appdata\\local\\google\\chrome\\user data',
    'c:\\users\\a\\appdata\\roaming\\bitwarden',
    'c:\\users\\a\\documents\\tencent files',
    'c:\\windows',
    'd:\\nest-data',
  ]) {
    assert.ok(roots.includes(must), must)
  }
})

function harness({ answers = [], steps = [], settings }) {
  const posted = []
  const asked = []
  let served = false
  const api = async (method, pathname, body) => {
    if (method === 'GET') {
      if (served) return new Promise(() => {}) // the next long poll: hangs, as nest would
      served = true
      return { status: 200, json: { requests: steps } }
    }
    if (method === 'POST') posted.push({ pathname, body })
    return { status: 200, json: { ok: true } }
  }
  const executor = new Executor({
    api,
    ask: async (data) => {
      asked.push(data)
      return answers.shift() ?? 'deny'
    },
    settings: () => settings,
    device: 'PC-1',
    log: {},
  })
  return { executor, posted, asked }
}

async function until(check) {
  for (let i = 0; i < 100; i += 1) {
    if (check()) return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error('timed out')
}

test('the loop takes steps from nest, asks where it must, and remembers "同类" per scope only', async () => {
  const { shared, outside, options } = scratch()
  const settings = { enabled: true, folders: options.folders }
  const notes = path.join(outside, 'notes.txt')
  const step = (id, scope, given) => ({ id, scope, tool: 'win_read_file', given, bot_name: '阿鲸' })
  const { executor, posted, asked } = harness({
    settings,
    answers: ['kind', 'once'],
    steps: [step('a', 's1.0', { path: path.join(shared, '清单.txt') }), step('b', 's1.0', { path: notes })],
  })
  executor.forbidden = options.forbidden
  executor.start()
  await until(() => posted.length === 2)
  executor.running = false // no more polling (stop() would also forget what was allowed)
  const byId = Object.fromEntries(posted.map((p) => [p.pathname.split('/').pop(), p.body]))
  assert.match(byId.a.text, /带伞/) // automatic: no window
  assert.equal(byId.a.device, 'PC-1')
  assert.equal(byId.b.text, '外面的')
  assert.equal(asked.length, 1)
  assert.equal(asked[0].botName, '阿鲸')
  assert.equal(asked[0].target, fs.realpathSync.native(notes)) // the real path, exactly
  // Allowed "for the rest of it": same scope, same tool, same folder — not asked again.
  assert.equal(await executor.take(step('c', 's1.0', { path: notes })), '外面的')
  assert.equal(asked.length, 1)
  // She read something from outside since (a new scope): asked again ('once' this time).
  assert.equal(await executor.take(step('d', 's1.1', { path: notes })), '外面的')
  assert.equal(asked.length, 2)
  // Denied, and switched off.
  assert.equal(await executor.take(step('e', 's2.0', { path: notes })), '对方没有允许这一步，没做。')
  settings.enabled = false
  assert.match(await executor.take(step('f', 's1.0', { path: notes })), /关了/)
})

test('完全允许: nothing asks; only with it can the never-list be opened, and turning it off closes it', () => {
  const { shared, outside, secret, options } = scratch()
  const notes = path.join(outside, 'notes.txt')
  assert.equal(decide('win_read_file', { path: notes }, { ...options, allowAll: true }).level, 'auto')
  // The never-list still holds under 完全允许 alone...
  assert.equal(decide('win_list_files', { path: secret }, { ...options, allowAll: true }).level, 'deny')
  fs.writeFileSync(path.join(shared, '.env'), 'x')
  assert.equal(
    decide('win_read_file', { path: path.join(shared, '.env') }, { ...options, allowAll: true }).level,
    'deny',
  )
  // ...and opens with it.
  const open = { ...options, allowAll: true, guard: false }
  assert.equal(decide('win_list_files', { path: secret }, open).level, 'auto')
  assert.equal(decide('win_read_file', { path: path.join(shared, '.env') }, open).level, 'auto')
  // What is not a full local path, or not a known step, is still refused.
  assert.equal(decide('win_read_file', { path: String.raw`\\server\share\x` }, open).level, 'deny')
  assert.equal(decide('win_format_disk', { path: shared }, open).level, 'deny')

  const { ExecutorStore } = require('../src/executor/store')
  const store = new ExecutorStore(path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'nest-store-')), 'executor.json'))
  assert.deepEqual([store.get().allowAll, store.get().openForbidden], [false, false])
  store.setOpenForbidden(true) // not without 完全允许
  assert.equal(store.get().openForbidden, false)
  store.setAllowAll(true)
  store.setOpenForbidden(true)
  assert.deepEqual([store.get().allowAll, store.get().openForbidden], [true, true])
  // Not kept across a restart of the app: only the user's click opens it.
  assert.deepEqual(
    [new ExecutorStore(store.file).get().allowAll, new ExecutorStore(store.file).get().openForbidden],
    [true, false],
  )
  assert.match(store.get().key, /^[0-9a-f]{48}$/) // this PC's key for nest, made once
  assert.equal(new ExecutorStore(store.file).get().key, store.get().key)
  store.setAllowAll(false)
  assert.deepEqual([store.get().allowAll, store.get().openForbidden], [false, false])
})

test('the loop under 完全允许 asks nothing and tells nest so', async () => {
  const { outside, options } = scratch()
  const settings = {
    enabled: true,
    allowAll: true,
    openForbidden: false,
    folders: options.folders,
  }
  const { executor, posted, asked } = harness({ settings, steps: [] })
  executor.forbidden = options.forbidden
  const step = {
    id: 'a',
    scope: 's.0',
    tool: 'win_read_file',
    given: { path: path.join(outside, 'notes.txt') },
  }
  assert.equal(await executor.take(step), '外面的')
  assert.equal(asked.length, 0)
  const sent = []
  executor.api = async (method, pathname, body) => {
    sent.push(body)
    return { status: 200, json: {} }
  }
  await executor.report()
  assert.equal(sent[0].allow_all, true)
  assert.equal(posted.length, 0)
})

// ---- 第 2 步: changing files, commands, opening ----------------------------------------------------------

const { diff } = require('../src/executor/diff')
const { programOf } = require('../src/executor/rules')

test('the diff shows what changes, with a little around it', () => {
  const before = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十'].join('\n') + '\n'
  const after = before.replace('五', '伍').replace('十', '十\n十一')
  const shown = diff(before, after)
  assert.equal(shown.added, 2)
  assert.equal(shown.removed, 1)
  assert.match(shown.text, /^@@ 第 2 行/)
  assert.match(shown.text, /- 五\n\+ 伍/)
  assert.match(shown.text, /\+ 十一/)
  assert.doesNotMatch(shown.text, / {2}一\n/) // too far from any change
  assert.equal(diff('a\r\nb\r\n', 'a\nb\n').text, '（内容没有变化）')
})

function workspace() {
  const made = scratch()
  const work = path.join(made.root, '工作')
  fs.mkdirSync(work)
  made.options.folders.push({ path: work, readOnly: false })
  return { ...made, work }
}

test('changing is asked everywhere and refused in a read-only folder; commands are asked every time (done under 完全允许)', () => {
  const { shared, outside, work, options } = workspace()
  const note = path.join(work, '笔记.txt')
  const write = decide('win_write_file', { path: note }, options)
  assert.equal(write.level, 'confirm') // even in an authorized folder
  assert.equal(decide('win_write_file', { path: note }, { ...options, allowAll: true }).level, 'auto')
  assert.match(decide('win_write_file', { path: path.join(shared, 'x.txt') }, options).why, /只读/)
  const into = { path: path.join(outside, 'notes.txt'), to: path.join(shared, 'n.txt') }
  assert.equal(decide('win_move', into, options).level, 'deny')
  assert.equal(decide('win_delete', { path: work }, { ...options, allowAll: true }).level, 'deny') // the folder itself
  assert.equal(decide('win_delete', { path: 'C:\\' }, { ...options, allowAll: true }).level, 'deny')
  // Opening a program is running it: that goes through a command.
  fs.writeFileSync(path.join(work, 'go.bat'), 'echo hi')
  assert.equal(decide('win_open', { target: path.join(work, 'go.bat') }, options).level, 'deny')
  const page = decide('win_open', { target: 'https://example.com/a?b=1' }, options)
  assert.equal(page.level, 'confirm')
  assert.equal(page.kind, 'win_open|example.com')
  // Commands: the whole command is asked about, every time — no "同类", and not under 完全允许 either.
  const command = decide('win_run_command', { command: 'git status', cwd: work }, options)
  assert.equal(command.level, 'confirm')
  assert.equal(command.kind, null)
  // 完全允许 takes in commands too.
  assert.equal(
    decide('win_run_command', { command: 'git status', cwd: work }, { ...options, allowAll: true }).level,
    'auto',
  )
  assert.equal(programOf(String.raw`& "C:\Program Files\Git\bin\git.exe" log`), 'git')
  assert.equal(programOf('Get-ChildItem | Select-Object -First 3'), 'get-childitem')
  for (const bad of [
    'netsh advfirewall set allprofiles state off',
    'Set-MpPreference -DisableRealtimeMonitoring $true',
    'schtasks /create /tn x /tr calc.exe',
    'Start-Process powershell -Verb RunAs',
    'shutdown /s /t 0',
    String.raw`reg add HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v x /d y`,
    'Format-Volume -DriveLetter D',
    'Start-Process powershell -v RunAs',
    'Start-Process powershell -Verb:RunAs',
    'Register-ScheduledJob -Name x -ScriptBlock { calc }',
    "Copy-Item x ([Environment]::GetFolderPath('Startup'))",
    "[Environment]::SetEnvironmentVariable('NEST_DESKTOP_DEBUG_DIR', 'D:/debug', 'User')",
  ]) {
    assert.equal(decide('win_run_command', { command: bad, cwd: work }, options).level, 'deny', bad)
  }
  // Format-Table is not formatting a disk.
  const table = { command: 'Get-Process | Format-Table', cwd: work }
  assert.equal(decide('win_run_command', table, options).level, 'confirm')
  // With the never-list opened (only under 完全允许), the guard list is lifted too.
  const open = { ...options, allowAll: true, guard: false }
  assert.equal(decide('win_run_command', { command: 'shutdown /a', cwd: work }, open).level, 'auto')
  assert.equal(decide('win_run_command', { command: 'dir', cwd: path.join(work, 'nope') }, options).level, 'deny')
})

function stepper(work, answers = []) {
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-bin-'))
  const settings = { enabled: true, folders: [{ path: work, readOnly: false }] }
  const asked = []
  const trashed = []
  const opened = []
  const executor = new Executor({
    api: async () => ({ status: 200, json: {} }),
    ask: async (data) => {
      asked.push(data)
      const answer = answers.shift() ?? 'once'
      return typeof answer === 'function' ? answer(data) : answer
    },
    settings: () => settings,
    system: {
      trash: async (target) => {
        // Moved aside (not fs.rmSync: Node 24 crashes removing a Chinese-named folder on Windows).
        trashed.push(target)
        fs.renameSync(target, path.join(bin, `${trashed.length}`))
      },
      open: async (what) => {
        opened.push(what)
        return ''
      },
    },
    device: 'PC-1',
    log: {},
  })
  let n = 0
  const take = (tool, given, scope = 's.0') => {
    n += 1
    return executor.take({ id: `x${n}`, scope, tool, given, bot_name: '阿鲸' })
  }
  return { executor, asked, trashed, opened, take, settings }
}

test('writing and editing show the diff first, keep the file as it was written, and check it did not move on', async () => {
  const { work } = workspace()
  const { asked, take } = stepper(work)
  const note = path.join(work, '笔记.txt')
  assert.match(await take('win_write_file', { path: note, content: '买牛奶\n买面包\n' }), /新建了/)
  assert.match(asked[0].detail, /新文件，2 行/)
  assert.equal(asked[0].detailKind, 'diff')
  assert.equal(fs.readFileSync(note, 'utf8'), '买牛奶\n买面包\n')
  // A file with Windows line ends and a BOM keeps them.
  fs.writeFileSync(note, '\ufeff买牛奶\r\n买面包\r\n', 'utf8')
  assert.match(await take('win_edit_file', { path: note, old: '买面包\n', new: '买面包\n买鸡蛋\n' }), /改好了/)
  assert.match(asked[1].detail, /\+ 买鸡蛋/)
  assert.equal(fs.readFileSync(note, 'utf8'), '\ufeff买牛奶\r\n买面包\r\n买鸡蛋\r\n')
  assert.match(await take('win_edit_file', { path: note, old: '买', new: '卖' }), /出现了 3 次/)
  assert.match(await take('win_edit_file', { path: note, old: '买咖啡', new: 'x' }), /没找到/)
  assert.match(await take('win_write_file', { path: note, content: '只剩一行\n' }), /写好了/)
  assert.equal(fs.readFileSync(note, 'utf8'), '\ufeff只剩一行\r\n')
  // Changed by something else while the window was open: not written.
  const other = stepper(work, [
    () => {
      fs.writeFileSync(note, '别人改的，长一点', 'utf8')
      return 'once'
    },
  ])
  assert.match(await other.take('win_write_file', { path: note, content: 'x' }), /被别的程序改过/)
  assert.equal(fs.readFileSync(note, 'utf8'), '别人改的，长一点')
})

test('"同类不再问" for writing holds for the same folder only; a no is a no', async () => {
  // "The same folder": the one the file is in (not the authorized folder as a whole).
  const { work, outside } = workspace()
  const { asked, take } = stepper(work, ['kind', 'once', 'deny'])
  await take('win_write_file', { path: path.join(work, 'a.txt'), content: 'a' })
  assert.match(asked[0].kindLabel, /同一个文件夹/)
  await take('win_write_file', { path: path.join(work, 'b.txt'), content: 'b' }) // same folder: not asked
  assert.equal(asked.length, 1)
  fs.mkdirSync(path.join(work, '子'))
  await take('win_write_file', { path: path.join(work, '子', 'c.txt'), content: 'c' }) // a subfolder: asked
  assert.equal(asked.length, 2)
  const elsewhere = { path: path.join(outside, 'c.txt'), content: 'c' }
  assert.equal(await take('win_write_file', elsewhere), '对方没有允许这一步，没做。')
  assert.equal(asked.length, 3)
  assert.equal(fs.existsSync(path.join(outside, 'c.txt')), false)
})

test('delete goes to the Recycle Bin, move does not overwrite, opening uses the default program', async () => {
  const { work } = workspace()
  const { asked, trashed, opened, take } = stepper(work)
  const old = path.join(work, '旧')
  fs.mkdirSync(old)
  fs.writeFileSync(path.join(old, '1.txt'), '1')
  assert.match(await take('win_delete', { path: old }), /放进回收站了/)
  assert.match(asked[0].detail, /1 个文件/)
  assert.deepEqual(trashed, [path.join(fs.realpathSync.native(work), '旧')])
  const a = path.join(work, 'a.txt')
  fs.writeFileSync(a, 'a')
  fs.mkdirSync(path.join(work, '归档'))
  assert.match(await take('win_move', { path: a, to: path.join(work, '归档') }), /→/) // into the folder
  assert.ok(fs.existsSync(path.join(work, '归档', 'a.txt')))
  fs.writeFileSync(a, 'again')
  assert.match(await take('win_move', { path: a, to: path.join(work, '归档', 'a.txt') }), /不覆盖/)
  assert.match(await take('win_move', { path: a, to: path.join(work, 'b.txt') }), /→/)
  assert.equal(asked.at(-1).action, '改名')
  assert.match(await take('win_open', { target: 'https://example.com/' }), /打开了/)
  assert.match(await take('win_open', { target: path.join(work, 'b.txt') }), /打开了/)
  assert.deepEqual(opened[0], { url: 'https://example.com/' })
  assert.ok(opened[1].path.endsWith('b.txt'))
})

test('a command runs in PowerShell in its folder, shows exactly, and gets no secrets', async () => {
  const { work } = workspace()
  const { asked, take } = stepper(work)
  const command = "Write-Output '你好'; (Get-Location).Path; [bool]$env:NEST_TEST_API_KEY"
  process.env.NEST_TEST_API_KEY = 'should-not-pass'
  try {
    const out = await take('win_run_command', { command, cwd: work })
    assert.match(out, /^退出码 0/)
    assert.match(out, /你好/)
    assert.ok(out.includes(fs.realpathSync.native(work)))
    assert.match(out, /False/)
  } finally {
    delete process.env.NEST_TEST_API_KEY
  }
  assert.equal(asked[0].detail, command)
  assert.equal(asked[0].detailKind, 'command')
  assert.match(asked[0].target, /工作目录/)
  assert.match(await take('win_run_command', { command: 'exit 3', cwd: work }), /^退出码 3/)
  assert.match(await take('win_run_command', { command: 'Start-Sleep 5', cwd: work, timeout: 1 }), /超过 1 秒/)
})

test('what came of a step reaches nest even when handing it back fails at first', async () => {
  const { work } = workspace()
  let failures = 3
  const posted = []
  const executor = new Executor({
    api: async (method, pathname, body) => {
      if (method !== 'POST') return { status: 200, json: { requests: [] } }
      if (failures > 0) {
        failures -= 1
        throw Object.assign(new Error('请求超时'), { code: 'ETIMEDOUT' })
      }
      posted.push({ pathname, body })
      return { status: 200, json: { ok: true } }
    },
    ask: async () => 'once',
    settings: () => ({ enabled: true, folders: [{ path: work, readOnly: false }] }),
    device: 'PC-1',
    log: {},
  })
  executor.handbackPause = 1
  await executor.handle({ id: 'a', scope: 's.0', tool: 'win_list_files', given: { path: work }, bot_name: '阿鲸' })
  assert.equal(posted.length, 1) // the fourth try got through
  assert.match(posted[0].body.text, /空的/)
  assert.equal(executor.unsent.size, 0)
  // Every try fails: kept, and handed back after the next poll that gets through.
  failures = 10
  await executor.handle({ id: 'b', scope: 's.0', tool: 'win_list_files', given: { path: work }, bot_name: '阿鲸' })
  assert.equal(executor.unsent.size, 1)
  failures = 0
  await executor.handBack('b')
  assert.equal(posted.at(-1).pathname, '/api/executor/requests/b')
  assert.equal(executor.unsent.size, 0)
})

test('a routine step (nobody there) that needs a nod is not done and opens no window, 完全允许 or not', async () => {
  const { work, outside } = workspace()
  const { executor, asked, settings } = stepper(work)
  const note = path.join(outside, 'notes.txt')
  const step = (tool, given) => ({ id: 'r', scope: 's.0', tool, given, bot_name: '阿鲸', unattended: true })
  assert.match(await executor.take(step('win_read_file', { path: note })), /定时任务/)
  assert.match(
    await executor.take(step('win_write_file', { path: path.join(work, 'x.txt'), content: 'x' })),
    /定时任务/,
  )
  assert.equal(fs.existsSync(path.join(work, 'x.txt')), false)
  // Inside an authorized folder looking needs no nod: done.
  assert.match(await executor.take(step('win_list_files', { path: work })), /空的/)
  // 完全允许 does not reach a routine's task.
  settings.allowAll = true
  assert.match(await executor.take(step('win_read_file', { path: note })), /定时任务/)
  assert.equal(asked.length, 0)
  assert.equal(await executor.take({ ...step('win_read_file', { path: note }), unattended: false }), '外面的')
})
