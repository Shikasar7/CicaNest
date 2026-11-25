/** How a bot looks across the console: one brush character and one colour, used wherever the bot appears. */
export type BotLook = { glyph: string; hue: string }

const HUES = ['var(--bot-1)', 'var(--bot-2)', 'var(--bot-3)', 'var(--bot-4)', 'var(--bot-5)', 'var(--bot-6)']
const CJK = /\p{Script=Han}/u

function hash(text: string): number {
  let h = 0
  for (const c of text) h = (h * 31 + (c.codePointAt(0) ?? 0)) >>> 0
  return h
}

export function botLook(name: string, id = name): BotLook {
  const chars = [...name]
  const glyph = [...chars].reverse().find((c) => CJK.test(c)) ?? (chars[0] ?? '?').toUpperCase()
  return { glyph, hue: HUES[hash(id) % HUES.length] }
}
