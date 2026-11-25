'use strict'

// A line diff for the confirm window (写文件、改文件 show what changes). Not a full Myers
// diff: the common start and end are trimmed, and the middle is matched by longest common subsequence while
// it is small enough; past that the whole middle shows as removed, then added. Pure, tested with node --test.

const CONTEXT = 3
const SHOWN_MAX = 400 // lines in the window
const MATCH_CELLS = 4_000_000 // LCS table size at most (old lines × new lines in the middle)

function lines(text) {
  if (text === '') return []
  const split = text.split(/\r?\n/)
  if (split[split.length - 1] === '') split.pop()
  return split
}

/** The middle's edit script: [{ op: ' ' | '-' | '+', line }]. */
function middle(a, b) {
  const n = a.length
  const m = b.length
  if (!n || !m || n * m > MATCH_CELLS) {
    return [...a.map((line) => ({ op: '-', line })), ...b.map((line) => ({ op: '+', line }))]
  }
  // lengths[i][j]: LCS of a[i..] and b[j..], one flat array.
  const width = m + 1
  const lengths = new Uint32Array((n + 1) * width)
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lengths[i * width + j] =
        a[i] === b[j]
          ? lengths[(i + 1) * width + j + 1] + 1
          : Math.max(lengths[(i + 1) * width + j], lengths[i * width + j + 1])
    }
  }
  const script = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      script.push({ op: ' ', line: a[i] })
      i += 1
      j += 1
    } else if (lengths[(i + 1) * width + j] >= lengths[i * width + j + 1]) {
      script.push({ op: '-', line: a[i] })
      i += 1
    } else {
      script.push({ op: '+', line: b[j] })
      j += 1
    }
  }
  while (i < n) script.push({ op: '-', line: a[i++] })
  while (j < m) script.push({ op: '+', line: b[j++] })
  return script
}

/**
 * The changes from `before` to `after` as text: hunks headed 「第 N 行」, lines marked '- ' (removed),
 * '+ ' (added) or '  ' (unchanged, a few around each change). { text, added, removed }.
 */
function diff(before, after) {
  const a = lines(before)
  const b = lines(after)
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1
  let end = 0
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end += 1
  const script = [
    ...a.slice(0, start).map((line) => ({ op: ' ', line })),
    ...middle(a.slice(start, a.length - end), b.slice(start, b.length - end)),
    ...a.slice(a.length - end).map((line) => ({ op: ' ', line })),
  ]
  const added = script.filter((s) => s.op === '+').length
  const removed = script.filter((s) => s.op === '-').length
  if (!added && !removed) return { text: '（内容没有变化）', added, removed }
  // Which entries to show: every change, and CONTEXT unchanged lines around it.
  const show = new Uint8Array(script.length)
  script.forEach((s, k) => {
    if (s.op === ' ') return
    for (let x = Math.max(0, k - CONTEXT); x <= Math.min(script.length - 1, k + CONTEXT); x += 1) show[x] = 1
  })
  const out = []
  let oldLine = 1
  let shown = 0
  let hidden = 0
  for (let k = 0; k < script.length; k += 1) {
    const s = script[k]
    if (show[k]) {
      if (k === 0 || !show[k - 1]) out.push(`@@ 第 ${oldLine} 行`)
      if (shown < SHOWN_MAX) out.push(`${s.op === ' ' ? ' ' : s.op} ${s.line}`)
      else if (s.op !== ' ') hidden += 1
      shown += 1
    }
    if (s.op !== '+') oldLine += 1
  }
  if (hidden) out.push(`……还有 ${hidden} 行改动没显示`)
  return { text: out.join('\n'), added, removed }
}

module.exports = { diff, lines }
