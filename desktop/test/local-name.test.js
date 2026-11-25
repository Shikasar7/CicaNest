'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const net = require('node:net')

const { LocalName, answerFor, question, serveDoor } = require('../src/local-name')

/** An mDNS answer: `name` is at `address` (its name written once, then pointed at). */
function answer(name, address, { type = 1, flags = 0x8400 } = {}) {
  const asked = question(name).subarray(12)
  const head = Buffer.alloc(12)
  head.writeUInt16BE(flags, 2)
  head.writeUInt16BE(1, 4)
  head.writeUInt16BE(1, 6)
  const data = Buffer.from(address.split('.').map(Number))
  const record = Buffer.alloc(12)
  record.writeUInt16BE(0xc00c, 0) // the name above
  record.writeUInt16BE(type, 2)
  record.writeUInt16BE(0x8001, 4)
  record.writeUInt32BE(120, 6)
  record.writeUInt16BE(data.length, 10)
  return Buffer.concat([head, asked, record, data])
}

test('an answer is read only for the name that was asked', () => {
  const name = 'Demo-Mac.local'
  assert.equal(answerFor('demo-mac.local', answer(name, '192.168.0.9')), '192.168.0.9')
  assert.equal(answerFor('other.local', answer(name, '192.168.0.9')), null)
  assert.equal(answerFor('demo-mac.local', answer(name, '192.168.0.9', { type: 16 })), null)
  assert.equal(answerFor('demo-mac.local', answer(name, '192.168.0.9', { flags: 0 })), null) // a question
  for (const bad of ['0.0.0.0', '127.0.0.1', '224.0.0.251', '255.255.255.255']) {
    assert.equal(answerFor('demo-mac.local', answer(name, bad)), null)
  }
})

test('a broken answer is no answer', () => {
  const whole = answer('demo-mac.local', '192.168.0.9')
  for (let n = 0; n < whole.length; n += 1) assert.equal(answerFor('demo-mac.local', whole.subarray(0, n)), null)
  const loop = Buffer.from(whole)
  loop.writeUInt16BE(0xc00c, 12) // a name that points at itself
  assert.equal(answerFor('demo-mac.local', loop), null)
})

test('the network answers: Windows is not asked, and one question serves everyone', async () => {
  let asked = 0
  let system = 0
  const names = new LocalName({
    ask: async () => ((asked += 1), '192.168.0.9'),
    system: async () => ((system += 1), '192.168.0.8'),
    systemAfter: 20,
  })
  assert.deepEqual(await Promise.all([names.find('a.local'), names.find('a.local')]), ['192.168.0.9', '192.168.0.9'])
  assert.equal(await names.find('a.local'), '192.168.0.9')
  await new Promise((resolve) => setTimeout(resolve, 40))
  assert.equal(asked, 1)
  assert.equal(system, 0)
})

test('the network is silent: Windows is asked; neither knows: an error, and asked again next time', async () => {
  const silent = () => new Promise(() => {})
  const slow = new LocalName({ ask: silent, system: async () => '192.168.0.8', systemAfter: 5 })
  assert.equal(await slow.find('a.local'), '192.168.0.8')
  const refuse = async () => {
    throw new Error('no')
  }
  let tries = 0
  const none = new LocalName({ ask: () => ((tries += 1), refuse()), system: refuse, systemAfter: 5 })
  await assert.rejects(none.find('a.local'))
  await assert.rejects(none.find('a.local'))
  assert.equal(tries, 2)
})

const listen = (server) => new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)))

test('an address that stopped answering is asked for again', async () => {
  const closed = net.createServer()
  const port = await listen(closed)
  await new Promise((resolve) => closed.close(resolve))
  const nest = net.createServer((socket) => socket.end())
  await new Promise((resolve) => nest.listen(port, '127.0.0.2', resolve))
  const given = ['127.0.0.1', '127.0.0.2']
  const names = new LocalName({ ask: async () => given.shift() })
  const socket = await names.connect('a.local', port)
  assert.equal(socket.remoteAddress, '127.0.0.2')
  socket.destroy()
  nest.close()
})

function talk(port, host, to, body = '') {
  const name = Buffer.from(host)
  const target = Buffer.alloc(2)
  target.writeUInt16BE(to)
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1')
    const got = []
    socket.on('data', (chunk) => got.push(chunk))
    socket.on('close', () => resolve(Buffer.concat(got)))
    socket.on('error', reject)
    // Everything at once: the door must not lose what follows the handshake.
    socket.write(Buffer.concat([Buffer.from([5, 1, 0, 5, 1, 0, 3, name.length]), name, target, Buffer.from(body)]))
  })
}

test('the door passes a .local name on and refuses the rest', async () => {
  const nest = net.createServer((socket) => socket.on('data', (chunk) => socket.end(`heard ${chunk}`)))
  const at = await listen(nest)
  const door = await serveDoor(new LocalName({ ask: async () => '127.0.0.1' }))
  const { port } = door.address()
  const ok = await talk(port, 'Demo-Mac.local', at, 'hello')
  assert.deepEqual([...ok.subarray(0, 4)], [5, 0, 5, 0])
  assert.equal(ok.subarray(12).toString(), 'heard hello')
  for (const host of ['example.com', '192.168.0.1', 'a.b.local', 'evil.local.example.com']) {
    assert.equal((await talk(port, host, at))[3], 2)
  }
  door.close()
  nest.close()
})
