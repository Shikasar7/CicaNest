'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')

const { fixElectronLink, linkFix } = require('../src/electron-link')

const options = { appId: 'nest', execPath: 'D:\\x\\electron.exe', icon: 'D:\\x\\nest.ico', appPath: 'D:\\my apps\\desktop' }
const made = { target: 'D:\\X\\Electron.exe', appUserModelId: 'nest', icon: '', args: '', toastActivatorClsid: '{A}' }

test("the shortcut Electron made gets nest's icon and keeps its toast id", () => {
  const fix = linkFix(made, options)
  assert.equal(fix.icon, options.icon)
  assert.equal(fix.args, '"D:\\my apps\\desktop"')
  assert.equal(fix.cwd, options.appPath)
  assert.equal(fix.toastActivatorClsid, '{A}')
})

test('any other shortcut, or one with an icon, is left alone', () => {
  assert.equal(linkFix({ ...made, icon: 'D:\\other.ico' }, options), null)
  assert.equal(linkFix({ ...made, appUserModelId: 'other' }, options), null)
  assert.equal(linkFix({ ...made, target: 'C:\\else\\electron.exe' }, options), null)
  assert.equal(linkFix(undefined, options), null)
})

test('no shortcut: nothing is written', () => {
  const shell = {
    readShortcutLink() {
      throw new Error('missing')
    },
    writeShortcutLink: () => assert.fail('written'),
  }
  assert.equal(fixElectronLink('x.lnk', shell, options), false)
})
