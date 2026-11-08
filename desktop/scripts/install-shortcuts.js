'use strict'

// Creates 「nest」 shortcuts on the Desktop and in the Start Menu that open this app with its icon.
// The Start Menu shortcut also carries the AppUserModelID "nest", which Windows needs for toasts.
//
//   npm run shortcuts                         (or: node scripts/install-shortcuts.js)
//   node scripts/install-shortcuts.js --dest <folder>   write both into <folder>\desktop and <folder>\start-menu
//   node scripts/install-shortcuts.js --remove          delete them again
//
// Under plain Node the script re-runs itself with Electron, which has shell.writeShortcutLink.

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const APP_DIR = path.resolve(__dirname, '..')
const args = process.argv.slice(2)

function option(name) {
  const index = args.indexOf(name)
  return index >= 0 ? args[index + 1] : undefined
}

if (!process.versions.electron) {
  let electron
  try {
    electron = require(path.join(APP_DIR, 'node_modules', 'electron'))
  } catch {
    console.error('找不到 Electron：先在 desktop 文件夹里运行 npm install。')
    process.exit(1)
  }
  const { spawnSync } = require('node:child_process')
  const env = { ...process.env }
  delete env.ELECTRON_RUN_AS_NODE
  const result = spawnSync(electron, [__filename, ...args], { stdio: 'inherit', env })
  process.exit(result.status ?? 1)
}

const { app, shell } = require('electron')

// Keep Electron from creating a profile folder named after this script.
app.setPath('userData', path.join(os.tmpdir(), 'nest-desktop-shortcuts'))

function destinations() {
  const dest = option('--dest')
  if (dest) {
    const root = path.resolve(dest)
    return [path.join(root, 'desktop', 'nest.lnk'), path.join(root, 'start-menu', 'nest.lnk')]
  }
  return [
    path.join(app.getPath('desktop'), 'nest.lnk'),
    path.join(app.getPath('appData'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'nest.lnk'),
  ]
}

function main() {
  const links = destinations()
  if (args.includes('--remove')) {
    for (const link of links) {
      if (fs.existsSync(link)) {
        fs.rmSync(link)
        console.log(`已删除 ${link}`)
      }
    }
    return 0
  }
  const shortcut = {
    target: process.execPath,
    args: `"${APP_DIR}"`,
    cwd: APP_DIR,
    description: 'nest：一键启动 QQ bot 和控制台',
    icon: path.join(APP_DIR, 'assets', 'icons', 'nest.ico'),
    iconIndex: 0,
    appUserModelId: 'nest',
  }
  let failed = 0
  for (const link of links) {
    fs.mkdirSync(path.dirname(link), { recursive: true })
    const ok = shell.writeShortcutLink(link, fs.existsSync(link) ? 'replace' : 'create', shortcut)
    if (!ok) {
      failed += 1
      console.error(`没能写入 ${link}`)
      continue
    }
    const back = shell.readShortcutLink(link)
    console.log(`已创建 ${link}`)
    console.log(`  → ${back.target} ${back.args}`)
    console.log(`  图标 ${back.icon} · AppUserModelID ${back.appUserModelId || '（无）'}`)
  }
  return failed ? 1 : 0
}

app.whenReady().then(() => {
  let code = 1
  try {
    code = main()
  } catch (err) {
    console.error(err)
  }
  app.exit(code)
})
