'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const { explain, isThisComputer, kindOf } = require('../src/why')

test('what went wrong, from the error or the answer', () => {
  const error = (message, code) => Object.assign(new Error(message), { code })
  assert.equal(kindOf({ error: error('net::ERR_CONNECTION_REFUSED') }), 'refused')
  assert.equal(kindOf({ error: error('connect', 'ECONNREFUSED') }), 'refused')
  assert.equal(kindOf({ error: error('net::ERR_NAME_NOT_RESOLVED') }), 'unknown-name')
  assert.equal(kindOf({ error: error('net::ERR_INTERNET_DISCONNECTED') }), 'no-network')
  assert.equal(kindOf({ error: error('请求超时', 'ETIMEDOUT') }), 'silent')
  assert.equal(kindOf({ error: error('net::ERR_ADDRESS_UNREACHABLE') }), 'silent')
  assert.equal(kindOf({ status: 403 }), 'not-allowed')
  assert.equal(kindOf({ status: 502 }), 'not-nest')
  assert.equal(kindOf({ status: 200 }), 'not-nest')
})

test('this computer is told apart, whatever its spelling', () => {
  for (const origin of ['http://127.0.0.1:18800', 'http://localhost:18800', 'http://[::1]:18800']) {
    assert.equal(isThisComputer(origin), true, origin)
    assert.match(explain('refused', origin).title, /这台电脑上没有/)
  }
  assert.equal(isThisComputer('http://my-mac.local:18800'), false)
  assert.equal(isThisComputer('not an address'), false)
})

test('every reason names the address and what to do about it', () => {
  for (const kind of ['refused', 'unknown-name', 'not-allowed', 'not-nest', 'silent', 'something new']) {
    const said = explain(kind, 'http://my-mac.local:18800')
    assert.ok(said.title && said.text.includes('my-mac.local'), kind)
    assert.ok(said.steps.length > 0, kind)
    assert.doesNotMatch(`${said.title}${said.text}${said.steps.join('')}`, /undefined|http:\/\//)
  }
  // The way to the switch, where that is what is missing.
  for (const kind of ['refused', 'not-allowed', 'silent']) {
    assert.match(explain(kind, 'http://10.0.0.2:18800').steps.join(''), /「系统」页的「远程访问」/)
  }
})
