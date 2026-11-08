/** How a bot looks across the console: one brush character and one colour, used wherever the bot appears. */
export type BotLook = { glyph: string; hue: string }

const KNOWN: { char: string; hue: string }[] = [
  { char: '鲸', hue: 'var(--bot-whale)' },
  { char: '马', hue: 'var(--bot-pony)' },
  { char: '龙', hue: 'var(--bot-dragon)' },
]
const SPARE = ['var(--bot-4)', 'var(--bot-5)', 'var(--bot-6)']
const CJK = /\p{Script=Han}/u

function hash(text: string): number {
  let h = 0
  for (const c of text) h = (h * 31 + (c.codePointAt(0) ?? 0)) >>> 0
  return h
}

export function botLook(name: string, id = name): BotLook {
  const known = KNOWN.find((k) => name.includes(k.char))
  if (known) return { glyph: known.char, hue: known.hue }
  const chars = [...name]
  const glyph = [...chars].reverse().find((c) => CJK.test(c)) ?? (chars[0] ?? '?').toUpperCase()
  return { glyph, hue: SPARE[hash(id) % SPARE.length] }
}
