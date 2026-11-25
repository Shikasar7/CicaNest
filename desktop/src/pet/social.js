'use strict'

// Two pets near each other on the same ground turn to one another and wave; now and then one walks over first.
// Plain decisions here, what they do is pet.js's (feel). Tested through require().

;(function (root) {
  const LOOK_MS = 4000
  const AGAIN_MS = 10 * 60_000 // a pair that met leaves it for a while
  const NEAR = 1.25 // figures this many sizes apart are together
  const SEEK_CHANCE = 0.02 // each look, someone with nothing to do walks over to another
  const free = (p) => p.mode === 'idle' && !p.dock && !p.open && !p.waiting && !p.asking && !p.working

  let next = 0
  const met = new Map() // "a|b" -> when

  const pair = (a, b) => [a.botId, b.botId].sort().join('|')
  const middle = (p) => p.x + p.pack.size / 2
  const feet = (p) => p.y + p.pack.baseline * p.pack.scale

  /** Two pets who may greet each other now, or null: free, side by side, not lately. */
  function together(list, t) {
    for (const a of list) {
      for (const b of list) {
        if (a === b || !free(a) || !free(b)) continue
        if (Math.abs(feet(a) - feet(b)) > 40) continue
        const apart = Math.abs(middle(a) - middle(b))
        if (apart > ((a.pack.size + b.pack.size) / 2) * NEAR) continue
        if (t - (met.get(pair(a, b)) ?? -Infinity) < AGAIN_MS) continue
        return [a, b]
      }
    }
    return null
  }

  function look(t, list) {
    if (t < next || list.length < 2) return
    next = t + LOOK_MS
    const found = together(list, t)
    if (found) {
      const [a, b] = found
      met.set(pair(a, b), t)
      a.facing = middle(b) > middle(a) ? 1 : -1
      b.facing = -a.facing
      a.feel('meet')
      b.feel('met', { quiet: true })
      return
    }
    if (Math.random() >= SEEK_CHANCE) return
    const ready = list.filter((p) => free(p) && p.resolve('walk') === 'walk')
    const a = ready[Math.floor(Math.random() * ready.length)]
    const others = a ? list.filter((p) => p !== a && free(p) && Math.abs(feet(p) - feet(a)) < 40) : []
    const b = others[Math.floor(Math.random() * others.length)]
    if (!a || !b || t - (met.get(pair(a, b)) ?? -Infinity) < AGAIN_MS) return
    const side = middle(a) < middle(b) ? -1 : 1
    a.target = b.x + side * ((a.pack.size + b.pack.size) / 2) * 0.9
    a.facing = a.target > a.x ? 1 : -1
    a.mode = 'walk'
    a.since = t
  }

  const api = { look, together, AGAIN_MS, reset: () => met.clear() }
  if (typeof module !== 'undefined' && module.exports) module.exports = api
  else root.petSocial = api
})(typeof window === 'undefined' ? globalThis : window)
