/** How a bot without a picture looks across the console: one colour and one kind of little face, used wherever
 * the bot appears. */
export type BotLook = { face: 'cat' | 'bear' | 'bunny' | 'sprout'; hue: string }

const HUES = ['var(--bot-1)', 'var(--bot-2)', 'var(--bot-3)', 'var(--bot-4)', 'var(--bot-5)', 'var(--bot-6)']
const FACES: BotLook['face'][] = ['cat', 'bunny', 'bear', 'sprout']

function hash(text: string): number {
  let h = 0
  for (const c of text) h = (h * 31 + (c.codePointAt(0) ?? 0)) >>> 0
  return h
}

export function botLook(name: string, id = name): BotLook {
  const h = hash(id)
  // The colour as it always was; the face from another part of the hash, so the two do not go together.
  return { face: FACES[(h >>> 7) % FACES.length], hue: HUES[h % HUES.length] }
}
