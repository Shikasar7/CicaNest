'use strict'

// A small append-only log next to nest's own logs (<home>\data\logs\desktop.log), so
// 「打开日志文件夹」 also shows what the desktop shell did. Never log tokens.

const fs = require('node:fs')
const path = require('node:path')

const MAX_BYTES = 1024 * 1024

let file = null
let extra = null

function init(logsDir, debugDir) {
  file = path.join(logsDir, 'desktop.log')
  extra = debugDir ? path.join(debugDir, 'desktop.log') : null
  try {
    fs.mkdirSync(logsDir, { recursive: true })
    if (fs.statSync(file, { throwIfNoEntry: false })?.size > MAX_BYTES) {
      fs.renameSync(file, `${file}.1`)
    }
  } catch {
    // Logging must never stop the app.
  }
}

function stamp() {
  const d = new Date()
  const pad = (n, w = 2) => String(n).padStart(w, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
  )
}

function write(level, parts) {
  const text = parts
    .map((p) => (p instanceof Error ? p.stack || p.message : typeof p === 'string' ? p : JSON.stringify(p)))
    .join(' ')
  const line = `${stamp()} ${level.padEnd(5)} ${text}\n`
  if (process.stdout && process.stdout.writable) {
    try {
      process.stdout.write(line)
    } catch {
      // no console attached
    }
  }
  for (const target of [file, extra]) {
    if (!target) continue
    try {
      fs.appendFileSync(target, line, 'utf8')
    } catch {
      // ignore
    }
  }
}

module.exports = {
  init,
  info: (...parts) => write('INFO', parts),
  warn: (...parts) => write('WARN', parts),
  error: (...parts) => write('ERROR', parts),
}
