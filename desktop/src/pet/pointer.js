'use strict'

// Where the mouse is, for the 桌宠 overlay. The page only hears of moves: a game that takes the mouse, or a window
// that comes in front, can end them while the last one was on a pet, and she would go on peeking out for a mouse
// that is long gone. So a place not heard of for a while is asked for again, and a mouse that has not moved for
// long holds no one out.

;(function (root) {
  const ASK_MS = 1000 // not heard of for this long: ask where it really is
  const REST_MS = 15_000 // unmoved for this long: not looking at her any more

  class Pointer {
    constructor() {
      this.x = -1 // page pixels; -1: not over the overlay
      this.y = -1
      this.movedAt = 0
      this.heardAt = 0
    }

    get here() {
      return this.x >= 0
    }

    /** The mouse is at (x, y), or (-1, -1) elsewhere: whether that is another place than before. */
    at(x, y, t) {
      const moved = Math.abs(x - this.x) > 1 || Math.abs(y - this.y) > 1
      this.x = x
      this.y = y
      this.heardAt = t
      if (moved) this.movedAt = t
      return moved
    }

    /** Time to ask where the mouse really is (at most once in ASK_MS). */
    shouldAsk(t) {
      if (!this.here || t - this.heardAt < ASK_MS) return false
      this.heardAt = t
      return true
    }

    resting(t) {
      return t - this.movedAt > REST_MS
    }
  }

  const api = { Pointer, ASK_MS, REST_MS }
  if (typeof module === 'object' && module.exports) module.exports = api
  else root.petPointer = api
})(typeof window !== 'undefined' ? window : globalThis)
