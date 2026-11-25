'use strict'

// Reaching a nest by its home-network name (xxx.local) without waiting for Windows to look the name up.
//
// Windows asks every DNS server of every adapter for a .local name as well, and waits for the ones that never
// answer (virtual adapters, VPNs): about 11 seconds, again for every new connection once the name is forgotten.
// Asking the home network itself (multicast DNS) takes some tens of milliseconds. Chromium cannot be handed
// an address while it runs, so it connects through a small SOCKS5 door on this machine (127.0.0.1) that takes
// only .local names, and finds the address here.

const dgram = require('node:dgram')
const dns = require('node:dns')
const net = require('node:net')
const os = require('node:os')

const MDNS_PORT = 5353
const MDNS_GROUP = '224.0.0.251'
const ASK_AT_MS = [0, 250, 1000] // multicast gets lost on Wi-Fi: asked again
const ASK_MS = 3000
const SYSTEM_AFTER_MS = 1200 // Windows' own (slow) lookup joins in when the network said nothing yet
const KEEP_MS = 10 * 60_000 // an address is asked for again after this, or at once when it does not answer
const CONNECT_MS = 4000
const HANDSHAKE_MAX = 512

/** The multicast question "what is this name's IPv4 address", answered straight to the asker. */
function question(name) {
  const parts = [Buffer.from([0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0])]
  for (const label of name.split('.')) parts.push(Buffer.from([Buffer.byteLength(label)]), Buffer.from(label))
  parts.push(Buffer.from([0, 0, 1, 0x80, 1])) // end of name, type A, class IN + "unicast answer"
  return Buffer.concat(parts)
}

// A name in a DNS message: [name, where it ends]. Throws on a broken one.
function readName(message, at) {
  const labels = []
  let end = null
  for (let hops = 0; hops < 64; hops += 1) {
    const size = message.readUInt8(at)
    if (size === 0) return [labels.join('.'), end ?? at + 1]
    if ((size & 0xc0) === 0xc0) {
      end ??= at + 2
      at = message.readUInt16BE(at) & 0x3fff
      continue
    }
    if (at + 1 + size > message.length) break
    labels.push(message.toString('utf8', at + 1, at + 1 + size))
    at += 1 + size
  }
  throw new RangeError('bad name')
}

/** The IPv4 address an answer gives for `name`, or null (a question, another name, nothing usable). */
function answerFor(name, message) {
  try {
    if (!(message.readUInt16BE(2) & 0x8000)) return null
    const questions = message.readUInt16BE(4)
    const records = message.readUInt16BE(6) + message.readUInt16BE(8) + message.readUInt16BE(10)
    let at = 12
    for (let n = 0; n < questions; n += 1) at = readName(message, at)[1] + 4
    for (let n = 0; n < records; n += 1) {
      const [owner, next] = readName(message, at)
      const type = message.readUInt16BE(next)
      const size = message.readUInt16BE(next + 8)
      const data = next + 10
      at = data + size
      if (type !== 1 || size !== 4 || at > message.length || owner.toLowerCase() !== name.toLowerCase()) continue
      const address = [...message.subarray(data, at)].join('.')
      if (!/^(0|127|22[4-9]|23\d|2[45]\d)\./.test(address)) return address
    }
  } catch {
    // cut short or malformed: not an answer
  }
  return null
}

/** Ask the home network for `name`, on every adapter. Rejects when nobody answers in time. */
function askNetwork(name) {
  return new Promise((resolve, reject) => {
    const sockets = new Set()
    const timers = []
    const done = (address) => {
      timers.forEach(clearTimeout)
      for (const socket of sockets) {
        try {
          socket.close()
        } catch {
          // closed already
        }
      }
      sockets.clear()
      if (address) resolve(address)
      else reject(new Error(`${name} 没有应答`))
    }
    const packet = question(name)
    for (const own of Object.values(os.networkInterfaces()).flat()) {
      if (own.family !== 'IPv4' || own.internal) continue
      const socket = dgram.createSocket('udp4')
      sockets.add(socket)
      socket.on('error', () => {}) // an adapter without multicast: the others are asked all the same
      socket.on('message', (message, from) => {
        if (from.port !== MDNS_PORT || !sockets.has(socket)) return
        const address = answerFor(name, message)
        if (address) done(address)
      })
      socket.bind(0, own.address, () => {
        for (const wait of ASK_AT_MS) {
          timers.push(
            setTimeout(() => {
              if (!sockets.has(socket)) return
              try {
                socket.setMulticastInterface(own.address)
                socket.send(packet, MDNS_PORT, MDNS_GROUP, () => {})
              } catch {
                // as above
              }
            }, wait),
          )
        }
      })
    }
    timers.push(setTimeout(() => done(null), ASK_MS))
  })
}

function askSystem(name) {
  return new Promise((resolve, reject) => {
    dns.lookup(name, { family: 4 }, (err, address) => (err ? reject(err) : resolve(address)))
  })
}

class LocalName {
  /** The nest's address by its name: kept a while, asked for once however many wait for it. */
  constructor({ ask = askNetwork, system = askSystem, systemAfter = SYSTEM_AFTER_MS, now = Date.now } = {}) {
    this.ask = ask
    this.system = system
    this.systemAfter = systemAfter
    this.now = now
    this.known = new Map() // name -> { address, at }
    this.asking = new Map() // name -> the question under way
  }

  forget(name, address) {
    if (this.known.get(name)?.address === address) this.known.delete(name)
  }

  async find(name) {
    const known = this.known.get(name)
    if (known && this.now() - known.at < KEEP_MS) return known.address
    let asking = this.asking.get(name)
    if (!asking) {
      let timer
      const late = new Promise((resolve) => {
        timer = setTimeout(resolve, this.systemAfter)
      })
      const network = this.ask(name)
      // Not started at all when the network answered: it would hold a worker thread for its 11 seconds.
      const system = Promise.race([
        late,
        network.then(
          () => null,
          () => {},
        ),
      ]).then((found) => (found === null ? network : this.system(name)))
      asking = Promise.any([network, system])
        .then((address) => {
          this.known.set(name, { address, at: this.now() })
          return address
        })
        .finally(() => {
          clearTimeout(timer)
          this.asking.delete(name)
        })
      this.asking.set(name, asking)
    }
    return asking
  }

  /** A connection to `name`. An address that stopped working (a new one was given out) is asked for again. */
  async connect(name, port, timeout = CONNECT_MS) {
    const first = await this.find(name)
    try {
      return await dial(first, port, timeout)
    } catch (err) {
      this.forget(name, first)
      const second = await this.find(name)
      if (second === first) throw err
      return dial(second, port, timeout)
    }
  }
}

function dial(host, port, timeout) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host, port })
    const timer = setTimeout(() => socket.destroy(new Error('连接超时')), timeout)
    socket.once('error', (err) => {
      clearTimeout(timer)
      reject(err)
    })
    socket.once('connect', () => {
      clearTimeout(timer)
      resolve(socket)
    })
  })
}

/** Read a SOCKS5 CONNECT by name: { host, port, rest }, null while more is needed, false when it is not one. */
function readConnect(buffer) {
  if (buffer.length < 5) return null
  if (buffer[0] !== 5 || buffer[1] !== 1 || buffer[3] !== 3) return false
  const end = 5 + buffer[4]
  if (buffer.length < end + 2) return null
  return {
    host: buffer.toString('latin1', 5, end).toLowerCase(),
    port: buffer.readUInt16BE(end),
    rest: buffer.subarray(end + 2),
  }
}

const reply = (code) => Buffer.from([5, code, 0, 1, 0, 0, 0, 0, 0, 0])

const isLocal = (host) => /^[a-z0-9-]+\.local$/.test(host)

/** The door: one CONNECT per connection, to a .local name and nothing else. */
function serveDoor(names) {
  const server = net.createServer({ allowHalfOpen: true }, (client) => {
    let buffer = Buffer.alloc(0)
    let greeted = false
    client.on('error', () => client.destroy())
    const onData = (chunk) => {
      buffer = Buffer.concat([buffer, chunk])
      if (!greeted) {
        if (buffer.length < 2 || buffer.length < 2 + buffer[1]) return
        if (buffer[0] !== 5 || !buffer.subarray(2, 2 + buffer[1]).includes(0)) return client.destroy()
        buffer = buffer.subarray(2 + buffer[1])
        greeted = true
        client.write(Buffer.from([5, 0]))
      }
      const asked = readConnect(buffer)
      if (asked === null) return buffer.length > HANDSHAKE_MAX ? client.destroy() : undefined
      client.off('data', onData)
      client.pause()
      if (!asked || !isLocal(asked.host)) return client.end(reply(2))
      names.connect(asked.host, asked.port).then(
        (nest) => {
          nest.on('error', () => client.destroy())
          nest.on('close', () => client.destroy())
          client.on('close', () => nest.destroy())
          if (client.destroyed) return nest.destroy()
          nest.setNoDelay(true)
          client.setNoDelay(true)
          client.write(reply(0))
          if (asked.rest.length) nest.write(asked.rest)
          client.pipe(nest)
          nest.pipe(client)
        },
        () => client.end(reply(4)),
      )
    }
    client.on('data', onData)
  })
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.unref()
      resolve(server)
    })
  })
}

/** Pages and requests of `session` to a .local address go through the door, everything else straight out
 * (the session talks to nest and nothing else). */
async function useDoor(session, names = new LocalName()) {
  const server = await serveDoor(names)
  const pac =
    'function FindProxyForURL(url, host) {' +
    ` return /^[a-z0-9-]+\\.local$/i.test(host) ? "SOCKS5 127.0.0.1:${server.address().port}" : "DIRECT"; }`
  const pacScript = `data:application/x-ns-proxy-autoconfig;base64,${Buffer.from(pac).toString('base64')}`
  await session.setProxy({ mode: 'pac_script', pacScript })
  return server
}

module.exports = { LocalName, answerFor, question, serveDoor, useDoor }
