'use strict'

// 电脑上做事 settings: on or off, and the folders a companion may look into without asking.

const STATUS = {
  off: '关着：伙伴在这台电脑上什么都做不了。',
  connecting: '正在连 nest……',
  online: '已连上 nest，伙伴可以请求在这台电脑上做事。',
  another: '另一台电脑已经连着 nest（同时只接一台）。',
  login: '还没登录：先在聊天窗口里输入远程访问密码。',
}

const api = window.nestExecutor

function render(state) {
  document.getElementById('enabled').checked = state.enabled
  document.getElementById('status').textContent = STATUS[state.status] || ''
  document.getElementById('allow-all').checked = state.allowAll
  const open = document.getElementById('open-forbidden')
  open.checked = state.openForbidden
  open.disabled = !state.allowAll
  document.getElementById('loose').hidden = !state.allowAll
  const list = document.getElementById('folders')
  list.replaceChildren()
  for (const folder of state.folders) {
    const item = document.createElement('li')
    const name = document.createElement('span')
    name.className = 'path'
    name.textContent = folder.path
    name.title = folder.path
    const readOnly = document.createElement('label')
    readOnly.className = 'ro'
    const box = document.createElement('input')
    box.type = 'checkbox'
    box.checked = folder.readOnly
    box.addEventListener('change', () => api.setReadOnly(folder.path, box.checked).then(refresh))
    readOnly.append(box, document.createTextNode('只读'))
    const remove = document.createElement('button')
    remove.className = 'btn ghost small'
    remove.type = 'button'
    remove.textContent = '移除'
    remove.addEventListener('click', () => api.remove(folder.path).then(refresh))
    item.append(name, readOnly, remove)
    list.append(item)
  }
  document.getElementById('empty').hidden = state.folders.length > 0
}

async function refresh() {
  render(await api.state())
}

document.getElementById('enabled').addEventListener('change', (e) => api.setEnabled(e.target.checked).then(refresh))
document.getElementById('add').addEventListener('click', () => api.add().then(refresh))
document.getElementById('allow-all').addEventListener('change', (e) => api.setAllowAll(e.target.checked).then(refresh))
document
  .getElementById('open-forbidden')
  .addEventListener('change', (e) => api.setOpenForbidden(e.target.checked).then(refresh))
api.onChange(refresh)
refresh()
