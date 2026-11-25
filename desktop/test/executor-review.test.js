'use strict'

// 电脑上做事, what keeps it safe: what the confirm window shows is what runs,
// "同类" stays small and short, a step is done once, switched off means off, commands do not leave anything
// running, the debug harness only for a test launch. node --test test/

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { decide } = require('../src/executor/rules')
const { Executor, GRANT_MS, GRANT_USES } = require('../src/executor')
const { runCommand } = require('../src/executor/tools')
const { debugAllowed } = require('../src/debug-gate')

function place() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'nest-review-'))
  const work = path.join(root, '工作')
  const other = path.join(root, '别处')
  fs.mkdirSync(work)
  fs.mkdirSync(other)
  return { root, work, other, folders: [{ path: work, readOnly: false }] }
}

function executor({ folders, answers = [], clock = () => 0, stepsFile = null, settings = {} }) {
  const asked = []
  const state = { enabled: true, folders, ...settings }
  const made = new Executor({
    api: async () => ({ status: 200, json: {} }),
    ask: async (data) => {
      asked.push(data)
      const answer = answers.shift() ?? 'once'
      return typeof answer === 'function' ? answer(data) : answer
    },
    settings: () => state,
    system: { trash: async () => {}, open: async () => '' },
    device: 'PC-1',
    stepsFile,
    clock,
    log: {},
  })
  return { executor: made, asked, state }
}

test('a command with a lone carriage return, direction marks or zero-width characters is refused', () => {
  const { work, folders } = place()
  const options = { folders, forbidden: [] }
  for (const hidden of [
    'Get-ChildItem # 列一下\rRemove-Item -Recurse D:\\资料',
    'Get-ChildItem # \u202e;"txt.evil"',
    'Get-Date\u200b; Remove-Item x',
    'Get-Date\u2028Remove-Item x',
  ]) {
    const decision = decide('win_run_command', { command: hidden, cwd: work }, options)
    assert.equal(decision.level, 'deny', JSON.stringify(hidden))
    assert.match(decision.why, /看不见的字符/)
  }
  // Windows line ends are fine: made plain, and that is what is shown and run.
  const lines = decide('win_run_command', { command: 'Get-Date\r\nGet-Location', cwd: work }, options)
  assert.equal(lines.level, 'confirm')
  assert.equal(lines.command, 'Get-Date\nGet-Location')
  // A path with a direction mark is refused too.
  assert.equal(decide('win_read_file', { path: path.join(work, 'a\u202etxt.exe') }, options).level, 'deny')
})

test('what she writes is shown with anything invisible marked', async () => {
  const { work, folders } = place()
  const { executor: pc, asked } = executor({ folders })
  const file = path.join(work, 'run.ps1')
  await pc.take({
    id: 'w',
    scope: 's',
    tool: 'win_write_file',
    given: { path: file, content: 'Write-Output hi # x\rdel *\n' },
  })
  assert.match(asked[0].detail, /看不见的字符/)
  assert.match(asked[0].detail, /⟦U\+000D⟧/)
})

test('a command is never "同类": asked each time, the window offers no such button', async () => {
  const { work, folders } = place()
  const { executor: pc, asked } = executor({ folders, answers: ['kind', 'deny'] })
  const step = (id, command) => ({
    id,
    scope: 's',
    bot_id: 'b',
    tool: 'win_run_command',
    given: { command, cwd: work },
  })
  await pc.take(step('1', 'Get-Date'))
  assert.equal(asked[0].kindLabel, '')
  assert.equal(await pc.take(step('2', 'Get-Date; Write-Output 第二句')), '对方没有允许这一步，没做。')
  assert.equal(asked.length, 2)
})

test('"同类" is kept per companion and task, for 15 minutes and 20 uses at most', async () => {
  const { work, folders } = place()
  let now = 0
  const { executor: pc, asked } = executor({ folders, answers: ['kind'], clock: () => now })
  let n = 0
  const write = (extra = {}) => {
    n += 1
    return pc.take({
      id: `w${n}`,
      scope: 's.0',
      bot_id: 'b',
      task_id: 7,
      tool: 'win_write_file',
      given: { path: path.join(work, `${n}.txt`), content: 'x' },
      ...extra,
    })
  }
  await write()
  await write()
  assert.equal(asked.length, 1) // same companion, task and scope: not asked
  await write({ bot_id: 'other' }) // another companion with the same scope: asked
  await write({ task_id: 8 }) // another task: asked
  assert.equal(asked.length, 3)
  now = GRANT_MS + 1 // later than 15 minutes: asked again
  await write()
  assert.equal(asked.length, 4)
  // A fresh grant runs out after GRANT_USES steps.
  const counted = executor({ folders, answers: ['kind'] })
  for (let i = 0; i <= GRANT_USES + 1; i += 1) {
    await counted.executor.take({
      id: `c${i}`,
      scope: 's',
      bot_id: 'b',
      tool: 'win_write_file',
      given: { path: path.join(work, `c${i}.txt`), content: 'x' },
    })
  }
  assert.equal(counted.asked.length, 2)
})

test('"同类" for a move covers that folder into that folder, and never a move out of the authorized ones', async () => {
  const { work, other, folders } = place()
  fs.mkdirSync(path.join(work, '归档'))
  for (const name of ['a.txt', 'b.txt', 'c.txt']) fs.writeFileSync(path.join(work, name), name)
  const { executor: pc, asked } = executor({ folders, answers: ['kind', 'deny'] })
  const move = (id, from, to) => pc.take({ id, scope: 's', bot_id: 'b', tool: 'win_move', given: { path: from, to } })
  await move('1', path.join(work, 'a.txt'), path.join(work, '归档'))
  assert.match(asked[0].kindLabel, /归档/)
  assert.match(await move('2', path.join(work, 'b.txt'), path.join(work, '归档')), /→/) // not asked
  assert.equal(asked.length, 1)
  assert.equal(await move('3', path.join(work, 'c.txt'), path.join(other, 'c.txt')), '对方没有允许这一步，没做。')
  assert.equal(asked.length, 2)
  assert.equal(asked[1].kindLabel, '') // out of the authorized folders: no "同类" at all
  assert.ok(fs.existsSync(path.join(work, 'c.txt')))
})

test('opening: programs never (完全允许 or not); other kinds asked every time, done under 完全允许', () => {
  const { work, folders } = place()
  const options = { folders, forbidden: [], allowAll: true }
  for (const name of ['a.chm', 'b.theme', 'c.rdp', 'd.search-ms', 'e.py', 'f.exe']) {
    fs.writeFileSync(path.join(work, name), 'x')
    assert.equal(decide('win_open', { target: path.join(work, name) }, options).level, 'deny', name)
  }
  fs.writeFileSync(path.join(work, '报告.pdf'), 'x')
  assert.equal(decide('win_open', { target: path.join(work, '报告.pdf') }, options).level, 'auto')
  assert.equal(decide('win_open', { target: work }, options).level, 'auto') // a folder
  fs.writeFileSync(path.join(work, 'x.unknownkind'), 'x')
  const odd = decide('win_open', { target: path.join(work, 'x.unknownkind') }, { ...options, allowAll: false })
  assert.equal(odd.level, 'confirm')
  assert.equal(odd.kind, null)
  // 完全允许: an unusual kind is opened without asking too.
  assert.equal(decide('win_open', { target: path.join(work, 'x.unknownkind') }, options).level, 'auto')
})

test('a step handed over again is not done again, also after the app restarted', async () => {
  const { work, folders } = place()
  const stepsFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'nest-steps-')), 'executor-steps.json')
  const sent = []
  const first = executor({ folders, stepsFile })
  first.executor.api = async (method, pathname, body) => {
    if (method === 'POST') throw new Error('还连不上')
    return { status: 200, json: {} }
  }
  first.executor.handbackPause = 1
  const file = path.join(work, 'log.txt')
  const step = {
    id: 'once',
    scope: 's',
    bot_id: 'b',
    tool: 'win_edit_file',
    given: { path: file, old: 'a', new: 'aa' },
  }
  fs.writeFileSync(file, 'a')
  await first.executor.handle(step)
  assert.equal(fs.readFileSync(file, 'utf8'), 'aa')
  await first.executor.handle(step) // handed over again: not done again
  assert.equal(fs.readFileSync(file, 'utf8'), 'aa')
  assert.equal(first.asked.length, 1)
  // The app restarts; nest still waits and hands the step over again: what came of it goes back, not a redo.
  const second = executor({ folders, stepsFile })
  second.executor.api = async (method, pathname, body) => {
    if (method === 'POST') sent.push(body.text)
    return { status: 200, json: {} }
  }
  await second.executor.handle(step)
  assert.equal(fs.readFileSync(file, 'utf8'), 'aa')
  assert.match(sent[0], /改好了/)
  assert.equal(second.asked.length, 0)
  // Taken before a restart with nothing kept of how it ended: said so, not done again.
  const saved = JSON.parse(fs.readFileSync(stepsFile, 'utf8'))
  saved.taken.lost = 1
  fs.writeFileSync(stepsFile, JSON.stringify(saved))
  const third = executor({ folders, stepsFile })
  third.executor.api = second.executor.api
  await third.executor.handle({ ...step, id: 'lost' })
  assert.match(sent.at(-1), /重启了/)
  assert.equal(fs.readFileSync(file, 'utf8'), 'aa')
})

test('switched off while the window was open: not done; too many waiting: not asked', async () => {
  const { work, folders } = place()
  const made = executor({ folders })
  made.executor.ask = async () => {
    made.state.enabled = false
    return 'once'
  }
  const file = path.join(work, 'x.txt')
  const said = await made.executor.take({
    id: 'x',
    scope: 's',
    tool: 'win_write_file',
    given: { path: file, content: 'x' },
  })
  assert.match(said, /关了/)
  assert.equal(fs.existsSync(file), false)
  const busy = executor({ folders, answers: ['busy'] })
  const text = await busy.executor.take({
    id: 'y',
    scope: 's',
    tool: 'win_write_file',
    given: { path: file, content: 'x' },
  })
  assert.match(text, /好几件事在等/)
  assert.equal(fs.existsSync(file), false)
})

test('the companion name in the window is short and plain', async () => {
  const { work, folders } = place()
  const { executor: pc, asked } = executor({ folders, answers: ['deny'] })
  await pc.take({
    id: 'n',
    scope: 's',
    bot_name: '系统检查\u202e：点允许继续，这是正常的维护步骤请放心',
    tool: 'win_write_file',
    given: { path: path.join(work, 'x.txt'), content: 'x' },
  })
  assert.equal(asked[0].botName.length <= 20, true)
  assert.doesNotMatch(asked[0].botName, /\u202e/)
})

test('what a command starts to outlive it ends with it; it gets only the environment Windows needs', async () => {
  const { work } = place()
  const mark = path.join(work, 'survived.txt')
  const command =
    `Start-Process -WindowStyle Hidden powershell -ArgumentList '-NoProfile','-Command',` +
    `'Start-Sleep 4; Set-Content -Path ''${mark}'' -Value ok'; Write-Output started; [bool]$env:NEST_TEST_PROXY`
  process.env.NEST_TEST_PROXY = 'http://user:pass@proxy'
  let out
  try {
    out = await runCommand({ command, cwd: work, seconds: 30 })
  } finally {
    delete process.env.NEST_TEST_PROXY
  }
  assert.match(out, /^退出码 0/)
  assert.match(out, /started/)
  assert.match(out, /False/)
  assert.doesNotMatch(out, /CLIXML/) // no progress records in what she reads
  await new Promise((resolve) => setTimeout(resolve, 6000))
  assert.equal(fs.existsSync(mark), false)
})

test("the debug harness: not when its setting is kept in the environment, nor inside a companion's folder", () => {
  const { work, root } = place()
  const file = path.join(root, 'executor.json')
  fs.writeFileSync(file, JSON.stringify({ folders: [{ path: work }] }))
  assert.equal(debugAllowed(path.join(root, 'debug'), file, { kept: () => false }).ok, true)
  assert.equal(debugAllowed(path.join(work, 'debug'), file, { kept: () => false }).ok, false)
  assert.equal(debugAllowed(path.join(root, 'debug'), file, { kept: () => true }).ok, false)
})
