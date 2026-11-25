/** 逐字流式: an answer still being written. It is drawn the way it will be stored (nest.core.desk.bubbles): a few
 * short lines as messages of their own, anything else as one. */

const BUBBLES = 4
const BUBBLE_CHARS = 40
const LISTED = /^\s*(?:[-•·*]|\d+[.、)）])/

export function bubbles(text: string): string[] {
  const lines = text.split('\n')
  const short = lines.every((line) => [...line].length <= BUBBLE_CHARS && !LISTED.test(line))
  return lines.length <= BUBBLES && short ? lines : [text]
}
