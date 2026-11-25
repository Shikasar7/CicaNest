'use strict'

// The confirm window: what a companion wants to do on this PC, exactly, and the user's answer.
// A command shows each line numbered (a command of several lines is plain to see); the buttons wake up a
// moment after the window shows, so a click meant for something else does not land on them.

const ARM_MS = 900

async function main() {
  const data = await window.nestConfirm.data()
  document.title = `${data.botName} 想帮你做一件事`
  document.getElementById('title').textContent = `${data.botName} 想帮你做一件事`
  document.getElementById('action').textContent = data.action
  document.getElementById('target').textContent = data.target
  const detail = document.getElementById('detail')
  if (data.detail) {
    detail.hidden = false
    detail.classList.toggle('command', data.detailKind === 'command')
    const rows = data.detail.split('\n')
    if (data.detailKind === 'command' && rows.length > 1) {
      const count = document.createElement('span')
      count.className = 'line at'
      count.textContent = `（共 ${rows.length} 行，每一行都会运行）\n`
      detail.append(count)
    }
    // A diff: each line coloured by its mark ('+ ' added, '- ' removed, '@@' where it is). Text only.
    rows.forEach((line, n) => {
      const row = document.createElement('span')
      row.className =
        data.detailKind !== 'diff'
          ? 'line'
          : line.startsWith('+ ')
            ? 'line add'
            : line.startsWith('- ')
              ? 'line del'
              : line.startsWith('@@')
                ? 'line at'
                : 'line'
      const number = data.detailKind === 'command' && rows.length > 1 ? `${String(n + 1).padStart(2)}│ ` : ''
      row.textContent = `${number}${line}\n`
      detail.append(row)
    })
  }
  document.getElementById('why').textContent = data.why ? `来问你一声，是因为${data.why}。` : ''
  const kind = document.getElementById('kind')
  if (!data.kindLabel) kind.style.display = 'none'
  kind.title = data.kindLabel || ''
  const notes = []
  if (data.kindLabel) {
    notes.push(
      `点「这次同类不再问」，接下来 15 分钟里同样的事（${data.kindLabel}）她就不来打扰你了；` +
        '她读过外面的东西（网页、文件）以后还会再问。',
    )
  } else {
    notes.push('这一步每次都要问你。')
  }
  notes.push(`${data.waitMinutes} 分钟没等到你，她就当你没同意。`)
  if (data.waiting) notes.push(`后面还有 ${data.waiting} 件事等着问你。`)
  if (data.debug) notes.push('【调试模式开着：这个窗口可以被调试通道代为回答】')
  document.getElementById('hint').textContent = notes.join('')
  const buttons = [...document.querySelectorAll('button')]
  let armed = false
  for (const b of buttons) b.disabled = true
  setTimeout(() => {
    armed = true
    for (const b of buttons) b.disabled = false
    document.querySelector('[data-answer="deny"]').focus() // Enter by accident is a no
  }, ARM_MS)
  for (const button of document.querySelectorAll('[data-answer]')) {
    button.addEventListener('click', () => {
      if (!armed) return
      for (const b of buttons) b.disabled = true
      window.nestConfirm.answer(button.dataset.answer)
    })
  }
}

main()
