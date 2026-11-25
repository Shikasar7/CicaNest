'use strict'

// Updates as the user meets them (the work is in update.js): looked for a little after the start and every 12 hours
// (can be switched off), a notification once per version, the tray item, and the two questions: whether to download,
// and, once it is there, whether to install now. Nothing is downloaded or installed unasked.

const { dialog, shell } = require('electron')
const { allowed, newer } = require('./update')
const { howTo, runInstaller } = require('./update-install')

const FIRST_MS = 60_000
const TICK_MS = 30 * 60_000 // a timer of 12 hours would not run on over the computer's sleep
const EVERY_MS = 12 * 60 * 60_000

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const mb = (bytes) => `${Math.max(1, Math.round(bytes / 1024 / 1024))} MB`

function alive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (err) {
    return err.code === 'EPERM'
  }
}

class UpdateApp {
  /**
   * @param {{ updater: import('./update').Updater, remote: boolean, icon: Electron.NativeImage, title: string,
   *   prefs: () => object, savePrefs: (patch: object) => void, notify: Function,
   *   parent: () => Electron.BrowserWindow | undefined, healthVersion: () => Promise<string>,
   *   stopNest: () => Promise<boolean>, startNest: () => void, quit: () => void, onChange: () => void, log: object }} options
   * `healthVersion`: of the nest this app is connected to; `stopNest`: whether one had to be stopped.
   */
  constructor(options) {
    Object.assign(this, options)
    this.how = 'page'
    this.asking = false
    this.lastTry = 0
    this.updater.on('change', () => this.onChange())
  }

  /** Before anything else is started. False: an installation is under way, this app is to quit again. */
  async begin() {
    let outcome = this.updater.outcome(alive)
    for (let n = 0; outcome?.running && n < 15; n += 1) {
      await sleep(200) // the one that started us after a failure is just about to end
      outcome = this.updater.outcome(alive)
    }
    if (outcome?.running) {
      this.log.info('update: the installer is still running')
      await this.box({ message: `正在更新到 ${outcome.to}`, detail: '装好后 nest 会自己打开，再等一下。', buttons: ['知道了'] })
      return false
    }
    this.result = outcome
    this.updater.tidy()
    return true
  }

  async start() {
    this.how = await howTo(process.execPath)
    this.log.info(`update: ${this.how}`)
    if (this.result?.ok) this.notify('已经更新', `现在是 nest ${this.updater.version}。`)
    else if (this.result) this.failed(this.result.to)
    setTimeout(() => this.tick(), FIRST_MS)
    setInterval(() => this.tick(), TICK_MS)
  }

  async failed(to) {
    const error = `上次更新到 ${to} 没有装上，现在还是 ${this.updater.version}。`
    this.log.warn(`update: ${to} was not installed`)
    this.updater.set({ error })
    const again = await this.box({
      type: 'warning',
      message: `nest ${to} 没有装上`,
      detail: `现在还是 ${this.updater.version}，你的数据没有变。可以再试一次；还不行的话，到发布页下载安装包自己装。`,
      buttons: ['再试一次', '以后再说'],
      cancelId: 1,
    })
    if (again === 0) await this.ask()
  }

  get auto() {
    return this.prefs().updateCheck !== false
  }

  setAuto(on) {
    this.savePrefs({ updateCheck: Boolean(on) })
    this.log.info(`update: automatic check ${on ? 'on' : 'off'}`)
    this.onChange()
    if (on) this.tick()
  }

  async tick() {
    if (!this.auto || Date.now() - this.lastTry < EVERY_MS) return
    this.lastTry = Date.now()
    this.updater.tidy()
    if (this.updater.state === 'ready' || this.updater.state === 'downloading') return
    const { error } = this.updater
    await this.updater.check()
    if (this.updater.error) return this.updater.set({ error }) // not asked for: not reported either
    const { state, found } = this.updater
    if (state !== 'available' || this.prefs().updateNotified === found.version) return
    this.savePrefs({ updateNotified: found.version })
    this.notify(`nest ${found.version} 出来了`, '点这里看看有什么新的。', () => this.ask())
  }

  // ---- for the console -----------------------------------------------------------------------------

  async state() {
    return {
      ...this.updater.snapshot(),
      auto: this.auto,
      how: this.how,
      otherVersion: this.remote ? await this.otherVersion() : '',
    }
  }

  async otherVersion() {
    if (!this.other || Date.now() - this.other.at > 60_000) {
      this.other = { at: Date.now(), version: this.healthVersion().catch(() => '') }
    }
    return this.other.version
  }

  async check() {
    this.lastTry = Date.now()
    await this.updater.check()
    return this.state()
  }

  async download() {
    if (this.how === 'page') this.openPage()
    else await this.updater.download()
    if (this.updater.state === 'ready') this.install() // asks first
    return this.state()
  }

  openPage() {
    const { found, source } = this.updater
    if (found?.page && allowed(source, found.page)) shell.openExternal(found.page)
  }

  // ---- the tray ------------------------------------------------------------------------------------

  menuItem() {
    const { state, found, percent } = this.updater
    if (state === 'checking') return { label: '正在检查更新…', enabled: false }
    // By tens: the menu is made anew with every change.
    if (state === 'downloading') return { label: `正在下载 ${found.version}（${Math.floor(percent / 10) * 10}%）`, enabled: false }
    if (state === 'installing') return { label: '正在更新…', enabled: false }
    if (state === 'ready') return { label: `安装 ${found.version}…`, enabled: true }
    if (state === 'available') return { label: `有新版本 ${found.version}…`, enabled: true }
    return { label: '检查更新…', enabled: true }
  }

  /** The tray item and the notification. */
  async ask() {
    if (this.asking) return
    this.asking = true
    try {
      if (this.updater.state === 'ready') return void (await this.install())
      if (this.updater.state !== 'available') await this.check()
      const { state, found, error } = this.updater
      if (state === 'available') return void (await this.offer())
      if (state === 'ready') return void (await this.install())
      if (error) {
        const at = await this.box({
          type: 'warning',
          message: found ? error : '没能检查更新',
          detail: found ? '' : error,
          buttons: found?.page ? ['打开发布页', '知道了'] : ['知道了'],
          cancelId: found?.page ? 1 : 0,
        })
        if (found?.page && at === 0) this.openPage()
      } else if (state === 'latest') {
        await this.box({ message: '已经是最新的', detail: `nest ${this.updater.version}${await this.elsewhere()}`, buttons: ['知道了'] })
      }
    } finally {
      this.asking = false
    }
  }

  async offer() {
    const { found, version, error } = this.updater
    const page = this.how === 'page'
    const notes = found.notes.length > 700 ? `${found.notes.slice(0, 700)}…` : found.notes
    const at = await this.box({
      message: `nest ${found.version} 出来了`,
      detail: `现在是 ${version}，安装包 ${mb(found.installer.size)}。${error ? `\n${error}` : ''}${await this.elsewhere()}${notes ? `\n\n${notes}` : ''}`,
      buttons: page ? ['打开发布页', '以后再说'] : ['下载', '看发布页', '以后再说'],
      cancelId: page ? 1 : 2,
    })
    if (at === (page ? 0 : 1)) return this.openPage()
    if (page || at !== 0) return
    await this.updater.download()
    if (this.updater.state === 'ready') return this.install()
    if (this.updater.error) this.notify('更新没有下载好', this.updater.error, () => this.ask())
  }

  /** Connected to a nest elsewhere: that one is not updated from here. */
  async elsewhere() {
    if (!this.remote) return ''
    const other = await this.otherVersion()
    const line = '\n这里更新的只是这台电脑上的桌面端。'
    if (!other) return `${line}那台电脑上的 nest 要在那台上更新。`
    return newer(this.updater.version, other) || newer(this.updater.found?.version, other)
      ? `${line}那台电脑上的 nest 是 ${other}，要在那台上更新。`
      : `${line}那台电脑上的 nest 是 ${other}。`
  }

  // ---- installing ----------------------------------------------------------------------------------

  install() {
    this.installing ??= this.doInstall().finally(() => {
      this.installing = null
    })
    return this.installing
  }

  async doInstall() {
    const { updater } = this
    if (updater.state !== 'ready' || this.how === 'page') return
    const { found } = updater
    const stops = this.remote ? '聊天窗口和桌宠会关一两分钟' : '所有 bot 会停一两分钟'
    const then =
      this.how === 'silent'
        ? `${stops}，装好后 nest 自己再打开。`
        : `${stops}。接下来是安装界面，可能要管理员同意；照着点完就好。`
    const at = await this.box({
      message: `现在安装 nest ${found.version}？`,
      detail: `${then}装在原来的位置，数据不变。${await this.elsewhere()}`,
      buttons: ['现在安装', '以后再说'],
      cancelId: 1,
    })
    if (at !== 0 || updater.state !== 'ready') return
    updater.set({ state: 'installing', error: '' })
    const file = await updater.verified()
    if (!file) return void (await this.trouble(updater.error))
    let stopped = false
    try {
      stopped = await this.stopNest()
      updater.begin()
      updater.begin(await runInstaller(file, this.how, process.execPath))
    } catch (err) {
      updater.forget()
      updater.set({ state: 'ready', error: `没能开始安装：${err.message}` })
      if (stopped) this.startNest()
      return void (await this.trouble(updater.error))
    }
    this.log.info(`update: installing ${found.version} (${this.how})`)
    this.quit()
  }

  trouble(text) {
    return this.box({ type: 'warning', message: '没有更新', detail: text, buttons: ['知道了'] })
  }

  async box({ type = 'info', message, detail = '', buttons, cancelId = 0 }) {
    const options = { type, title: this.title, message, detail, buttons, defaultId: 0, cancelId, noLink: true, icon: this.icon }
    const parent = this.parent()
    const { response } = parent ? await dialog.showMessageBox(parent, options) : await dialog.showMessageBox(options)
    return response
  }
}

module.exports = { UpdateApp }
