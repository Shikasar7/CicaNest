'use strict'

// Why a nest on another computer cannot be reached, in words the user can do something with: for the offline page
// and 「试一下」 in 连接设置.

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]'])

/** Whether an origin is this very computer. */
function isThisComputer(origin) {
  try {
    const { hostname } = new URL(origin)
    return LOOPBACK.has(hostname) || hostname.endsWith('.localhost')
  } catch {
    return false
  }
}

/** What an attempt came to: its error, or the answer that was not a nest's. */
function kindOf({ error = null, status = 0 } = {}) {
  if (error) {
    const text = `${error.code || ''} ${error.message || ''}`
    if (/REFUSED/i.test(text)) return 'refused'
    if (/NAME_NOT_RESOLVED|ENOTFOUND|EAI_AGAIN/i.test(text)) return 'unknown-name'
    if (/INTERNET_DISCONNECTED|NETWORK_CHANGED|ENETUNREACH/i.test(text)) return 'no-network'
    return 'silent' // timed out, unreachable, reset
  }
  return status === 403 ? 'not-allowed' : 'not-nest'
}

/** { title, text, steps } for the user. `host`: the address without http://. */
function explain(kind, origin) {
  const host = String(origin).replace(/^https?:\/\//, '')
  if (isThisComputer(origin)) {
    return {
      title: '这台电脑上没有在运行的 nest',
      text: `这里设的地址是 ${host}，就是这台电脑自己，但没有 nest 在上面运行。`,
      steps: [],
    }
  }
  const name = host.replace(/:\d+$/, '')
  const remoteAccess = '在那台电脑打开 nest 控制台，到「系统」页的「远程访问」：打开开关，设一个密码，重启 nest'
  switch (kind) {
    case 'refused':
      return {
        title: '那台电脑在，但连不进 nest',
        text: `${host} 有回应，只是 nest 没在运行，或者没有打开远程访问。`,
        steps: ['确认那台电脑上的 nest 开着', remoteAccess],
      }
    case 'unknown-name':
      return {
        title: '找不到那台电脑',
        text: `没有找到叫「${name}」的电脑。`,
        steps: ['检查地址有没有写错（可以换成那台的 IP）', '两台电脑要在同一个 Wi‑Fi 或路由器下，或者都登录了 Tailscale'],
      }
    case 'no-network':
      return { title: '这台电脑没有连上网络', text: '连上 Wi‑Fi 或网线后，这个窗口会自动恢复。', steps: [] }
    case 'not-allowed':
      return { title: '那台 nest 不让别的电脑连', text: `${host} 上的 nest 现在只允许它自己用。`, steps: [remoteAccess] }
    case 'not-nest':
      return {
        title: '那个地址上不是 nest',
        text: `${host} 有回应，但不是 nest。`,
        steps: ['检查地址和端口（nest 一般是 18800）', '开着代理软件的话，让它不要代理局域网地址'],
      }
    default:
      return {
        title: '那台电脑没有回应',
        text: `连不上 ${host}。`,
        steps: ['那台电脑开着、没有睡眠', '两台电脑在同一个 Wi‑Fi 或路由器下，或者都登录了 Tailscale', remoteAccess],
      }
  }
}

module.exports = { isThisComputer, kindOf, explain }
