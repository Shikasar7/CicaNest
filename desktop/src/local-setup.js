'use strict'

// Choosing 「在这台电脑上运行」 in an installed app for the first time: a folder for nest's data, and a port nothing
// else uses. Once there is data the port is the user's business (NEST_PORT in <home>\\.env).

const fs = require('node:fs')
const path = require('node:path')
const { DEFAULT_PORT, portFromDotenv } = require('./config')
const { portTaken } = require('./nest')

const TRIES = 50

/** The port nest will use at `home`; written to its .env when it is not the usual one. */
async function prepareHome(home, env = process.env, taken = portTaken) {
  fs.mkdirSync(home, { recursive: true })
  const set = env.NEST_PORT || portFromDotenv(home)
  if (set) return Number(set)
  if (fs.existsSync(path.join(home, 'data', 'nest.db'))) return DEFAULT_PORT
  for (let port = DEFAULT_PORT; port < DEFAULT_PORT + TRIES; port += 1) {
    if (await taken(port)) continue
    if (port !== DEFAULT_PORT) {
      const file = path.join(home, '.env')
      const before = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : ''
      const gap = before && !before.endsWith('\n') ? '\n' : ''
      fs.writeFileSync(file, `${before}${gap}NEST_PORT=${port}\n`, 'utf8')
    }
    return port
  }
  throw new Error(`端口 ${DEFAULT_PORT}–${DEFAULT_PORT + TRIES - 1} 都被占用了`)
}

module.exports = { prepareHome }
