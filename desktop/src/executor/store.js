'use strict'

// 电脑上做事 settings, kept on this PC (executor.json in the app's profile folder): on or off, and the
// folders the user authorized (each may be read-only), and 完全允许 (allowAll: no nod anywhere) with, only
// on top of it, the never-list opened (openForbidden). Only the settings window changes them — never nest,
// never a companion.
//
// The never-list opened is not kept across a restart of this app: a file written behind the settings
// window's back cannot open it without the user's own click.
// `key`: this PC's name for nest as an executor, made once.

const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

const FOLDERS_MAX = 50

class ExecutorStore {
  constructor(file) {
    this.file = file
    this.data = this.load()
    if (!this.data.key) {
      this.data.key = crypto.randomBytes(24).toString('hex')
      try {
        this.save()
      } catch {
        // kept in memory for this run
      }
    }
  }

  load() {
    try {
      const raw = JSON.parse(fs.readFileSync(this.file, 'utf8'))
      return {
        enabled: raw.enabled !== false,
        allowAll: raw.allowAll === true,
        openForbidden: false, // only by the user's click, every run
        key: typeof raw.key === 'string' && /^[0-9a-f]{32,128}$/.test(raw.key) ? raw.key : '',
        folders: (Array.isArray(raw.folders) ? raw.folders : [])
          .filter((f) => f && typeof f.path === 'string' && path.win32.isAbsolute(f.path))
          .slice(0, FOLDERS_MAX)
          .map((f) => ({ path: f.path, readOnly: Boolean(f.readOnly) })),
      }
    } catch {
      return { enabled: true, allowAll: false, openForbidden: false, key: '', folders: [] }
    }
  }

  save() {
    fs.mkdirSync(path.dirname(this.file), { recursive: true })
    const temporary = `${this.file}.new`
    fs.writeFileSync(temporary, JSON.stringify(this.data, null, 2), 'utf8')
    fs.renameSync(temporary, this.file)
  }

  get() {
    return {
      enabled: this.data.enabled,
      allowAll: this.data.allowAll,
      openForbidden: this.data.openForbidden,
      key: this.data.key,
      folders: this.data.folders.map((f) => ({ ...f })),
    }
  }

  /** 完全允许. Turned off, the never-list closes again too. */
  setAllowAll(on) {
    this.data.allowAll = Boolean(on)
    if (!this.data.allowAll) this.data.openForbidden = false
    this.save()
  }

  /** The never-list opened: only while 完全允许 is on. */
  setOpenForbidden(on) {
    this.data.openForbidden = Boolean(on) && this.data.allowAll
    this.save()
  }

  setEnabled(on) {
    this.data.enabled = Boolean(on)
    this.save()
  }

  add(folder) {
    const known = this.data.folders.some((f) => f.path.toLowerCase() === folder.toLowerCase())
    if (!known && this.data.folders.length < FOLDERS_MAX) {
      this.data.folders.push({ path: folder, readOnly: false })
      this.save()
    }
  }

  remove(folder) {
    this.data.folders = this.data.folders.filter((f) => f.path !== folder)
    this.save()
  }

  setReadOnly(folder, readOnly) {
    for (const f of this.data.folders) if (f.path === folder) f.readOnly = Boolean(readOnly)
    this.save()
  }
}

module.exports = { ExecutorStore }
