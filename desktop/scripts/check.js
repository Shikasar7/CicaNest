'use strict'

// `npm run check`: node --check on every JavaScript file of the shell (there is no build step).

const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

const ROOT = path.resolve(__dirname, '..')

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : walk(full)
    return entry.name.endsWith('.js') ? [full] : []
  })
}

const files = [...walk(path.join(ROOT, 'src')), ...walk(path.join(ROOT, 'scripts'))]
let failed = 0
for (const file of files) {
  const result = spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' })
  const name = path.relative(ROOT, file)
  if (result.status === 0) {
    console.log(`ok    ${name}`)
  } else {
    failed += 1
    console.log(`FAIL  ${name}\n${result.stderr}`)
  }
}
console.log(`${files.length - failed}/${files.length} files pass node --check`)
process.exit(failed ? 1 : 0)
