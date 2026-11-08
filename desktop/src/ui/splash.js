'use strict'

// Splash renderer: draws the state pushed by the main process (src/splash.js).
// Elements are keyed and updated in place so CSS transitions carry the motion.

const $ = (id) => document.getElementById(id)
const card = $('card')
const subtitle = $('subtitle')
const glyphBox = $('glyphs')
const stepList = $('steps')
const message = $('message')
const actionBox = $('actions')
const footnote = $('footnote')
const tag = $('tag')

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')

const ICONS = {
  ok: 'M2.2 5.3 4.2 7.3 7.9 3',
  warn: 'M5 2.4v3.3M5 7.6v.05',
  bad: 'M3 3l4 4M7 3 3 7',
}

function svgPath(d) {
  const ns = 'http://www.w3.org/2000/svg'
  const svg = document.createElementNS(ns, 'svg')
  svg.setAttribute('viewBox', '0 0 10 10')
  svg.setAttribute('aria-hidden', 'true')
  const path = document.createElementNS(ns, 'path')
  path.setAttribute('d', d)
  svg.append(path)
  return svg
}

function el(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function refresh(node) {
  node.classList.remove('is-fresh')
  void node.offsetWidth // restart the animation
  node.classList.add('is-fresh')
}

function swapText(node, text) {
  if (node.textContent === text) return
  const animate = node.textContent && !reducedMotion.matches && card.classList.contains('is-in')
  node.textContent = text
  if (animate) {
    node.animate([{ opacity: 0, transform: 'translateY(3px)' }, { opacity: 1, transform: 'none' }], {
      duration: 260,
      easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
    })
  }
}

// ---- pieces -------------------------------------------------------------------------------

const STATE_WORDS = { done: '完成', warn: '需要处理', failed: '失败', skipped: '跳过', running: '进行中', pending: '等待' }

function renderSteps(steps) {
  const existing = new Map([...stepList.children].map((li) => [li.dataset.id, li]))
  steps.forEach((step, index) => {
    let li = existing.get(step.id)
    if (!li) {
      li = el('li', 'step')
      li.dataset.id = step.id
      li.style.setProperty('--i', String(index))
      const dot = el('span', 'dot')
      for (const kind of ['ok', 'warn', 'bad']) {
        const badge = el('span', `badge ${kind}`)
        badge.append(svgPath(ICONS[kind]))
        dot.append(badge)
      }
      li.append(dot, el('span', 'label'), el('span', 'detail'))
      stepList.append(li)
    }
    existing.delete(step.id)
    const previous = li.dataset.state
    li.className = `step is-${step.state}`
    li.dataset.state = step.state
    li.querySelector('.label').textContent = step.label
    li.setAttribute('aria-label', `${step.label}：${step.detail || STATE_WORDS[step.state] || ''}`)
    const detail = li.querySelector('.detail')
    if (detail.textContent !== step.detail) {
      detail.textContent = step.detail
      if (previous !== step.state && step.detail) refresh(detail)
    }
  })
  for (const li of existing.values()) li.remove()
}

function renderGlyphs(glyphs, waiting) {
  glyphBox.classList.toggle('is-waiting', Boolean(waiting))
  const existing = new Map([...glyphBox.children].filter((g) => !g.classList.contains('is-gone')).map((g) => [g.dataset.key, g]))
  glyphs.forEach((glyph, index) => {
    let node = existing.get(glyph.key)
    // A placeholder glyph is replaced by the real account with the same character, without a flicker.
    if (!node) {
      node = [...existing.values()].find((g) => g.dataset.char === glyph.char && g.dataset.placeholder === '1')
      if (node) existing.delete(node.dataset.key)
    } else {
      existing.delete(glyph.key)
    }
    if (!node) {
      node = el('span', 'glyph')
      node.append(el('span', '', glyph.char))
      glyphBox.append(node)
    }
    node.dataset.key = glyph.key
    node.dataset.char = glyph.char
    node.dataset.placeholder = glyph.name ? '0' : '1'
    node.style.setProperty('--hue', glyph.hue)
    node.style.setProperty('--i', String(index))
    node.firstChild.textContent = glyph.char
    node.classList.toggle('is-on', Boolean(glyph.online))
    node.title = glyph.name ? `${glyph.name} · ${glyph.online ? '在线' : '还没上线'}` : ''
    if (glyphBox.children[index] !== node) glyphBox.insertBefore(node, glyphBox.children[index] || null)
  })
  for (const node of existing.values()) {
    node.classList.add('is-gone')
    setTimeout(() => node.remove(), 240)
  }
}

let lastMessage = ''

function renderMessage(msg) {
  const key = msg ? `${msg.tone}|${msg.text}|${msg.link?.id || ''}` : ''
  if (key === lastMessage) return
  const sameTone = msg && lastMessage.startsWith(`${msg.tone}|`)
  lastMessage = key
  message.replaceChildren()
  message.className = 'message'
  if (!msg) return
  message.classList.add(`tone-${msg.tone}`)
  message.append(document.createTextNode(msg.text))
  if (msg.link) {
    const link = el('button', 'link', msg.link.label)
    link.type = 'button'
    link.addEventListener('click', () => window.splash.act(msg.link.id))
    message.append(link)
  }
  // A count ticking down ("还有 2 个号…" → "还有 1 个号…") should not re-animate.
  if (!sameTone) refresh(message)
}

let lastActions = ''

function renderActions(actions) {
  const key = JSON.stringify(actions || [])
  if (key === lastActions) return
  lastActions = key
  actionBox.replaceChildren()
  ;(actions || []).forEach((action, index) => {
    const button = el('button', `btn ${action.kind === 'primary' ? 'primary' : 'ghost'}`, action.label)
    button.type = 'button'
    button.dataset.action = action.id
    button.style.setProperty('--i', String(index))
    button.addEventListener('click', () => {
      button.disabled = true
      window.splash.act(action.id)
      setTimeout(() => {
        button.disabled = false
      }, 800)
    })
    actionBox.append(button)
  })
}

function render(state) {
  document.documentElement.dataset.phase = state.phase
  swapText(subtitle, state.title)
  renderGlyphs(state.glyphs || [], state.qqWaiting)
  renderSteps(state.steps || [])
  renderMessage(state.message)
  renderActions(state.actions)
  if (footnote.textContent !== (state.footnote || '')) footnote.textContent = state.footnote || ''
  footnote.parentElement.title = state.footnoteTitle || ''
  tag.hidden = !state.testCopy
}

// ---- wiring -------------------------------------------------------------------------------

window.splash.onState(render)

window.splash.onEnter(() => {
  card.classList.remove('is-out')
  card.classList.add('is-in')
})

window.splash.onLeave(() => {
  card.classList.add('is-out')
})

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    window.splash.act('dismiss')
  } else if (event.key === 'Enter' && !(event.target instanceof HTMLButtonElement)) {
    actionBox.querySelector('.btn.primary')?.click()
  }
})

// Show the card only once its fonts are in, so nothing reflows while it fades in.
Promise.allSettled([document.fonts.load('400 13px Geist'), document.fonts.load('620 17px Geist')])
  .then(() => document.fonts.ready)
  .finally(() => window.splash.ready())
