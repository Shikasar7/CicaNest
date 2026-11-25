'use strict'

// 贴边藏起: where a pet put down at a screen edge hides, and where she peeks out from.
//
// Plain geometry, no DOM: the overlay page (pet.js) uses it through window.petEdge, the tests through
// require(). Everything is in the overlay page's CSS pixels; a screen is a display's work area in those
// pixels ({ x, y, width, height }), the first one the primary display.
//
// A pet's figure is the box of what is drawn of her (the opaque part of her frame), not her whole canvas.
// Only the left, right and top edges are used: the bottom is the ground she stands on. An edge with another
// screen right behind it is not an edge to hide at (she would show up on the other screen).

;(function (root) {
  const SNAP = 14 // the figure within this many pixels of an edge (or past it) when put down: she hides there
  const PEEK_SIDE = 0.58 // peeking from the left or right: this much of her width shows
  const PEEK_TOP = 0.5 // peeking down from the top (upside down): this much of her height shows
  const HIDE_GAP = 4 // hidden: this far past the edge, so nothing of her shows

  const right = (s) => s.x + s.width
  const bottom = (s) => s.y + s.height

  /** The screen containing the point, else the nearest one. */
  function screenAt(screens, point) {
    let best = screens[0] || null
    let bestDistance = Infinity
    for (const s of screens) {
      const dx = Math.max(s.x - point.x, 0, point.x - right(s))
      const dy = Math.max(s.y - point.y, 0, point.y - bottom(s))
      const distance = Math.hypot(dx, dy)
      if (distance === 0) return s
      if (distance < bestDistance) {
        best = s
        bestDistance = distance
      }
    }
    return best
  }

  /** Whether another screen continues past `side` of `screen` where the figure is. */
  function screenBeyond(screens, screen, side, figure) {
    return screens.some((s) => {
      if (s === screen) return false
      const across = (a1, a2, b1, b2) => Math.min(a2, b2) - Math.max(a1, b1) > 0
      if (side === 'left') return Math.abs(right(s) - screen.x) <= 2 && across(s.y, bottom(s), figure.top, figure.bottom)
      if (side === 'right') return Math.abs(s.x - right(screen)) <= 2 && across(s.y, bottom(s), figure.top, figure.bottom)
      return Math.abs(bottom(s) - screen.y) <= 2 && across(s.x, right(s), figure.left, figure.right)
    })
  }

  /**
   * The edge to hide at when her figure was put down here: 'left', 'right', 'top' or null. The screen is
   * the one she is on (the middle of her figure); the edge she is pushed furthest against wins.
   */
  function dockSide(figure, screens, snap = SNAP) {
    if (!screens || !screens.length) return null
    const middle = { x: (figure.left + figure.right) / 2, y: (figure.top + figure.bottom) / 2 }
    const screen = screenAt(screens, middle)
    const pushed = {
      left: screen.x + snap - figure.left,
      right: figure.right - (right(screen) - snap),
      top: screen.y + snap - figure.top,
    }
    let best = null
    for (const side of ['left', 'right', 'top']) {
      if (pushed[side] < 0 || screenBeyond(screens, screen, side, figure)) continue
      if (!best || pushed[side] > pushed[best]) best = side
    }
    return best
  }

  /** 藏到屏幕边上 from her menu: the nearer of the left and right edges of her screen (one with no screen
   * behind it); null when both have one. */
  function nearestSide(figure, screens) {
    if (!screens || !screens.length) return null
    const middle = { x: (figure.left + figure.right) / 2, y: (figure.top + figure.bottom) / 2 }
    const screen = screenAt(screens, middle)
    const sides = middle.x - screen.x <= right(screen) - middle.x ? ['left', 'right'] : ['right', 'left']
    return sides.find((side) => !screenBeyond(screens, screen, side, figure)) || null
  }

  /** The figure box in her own canvas, as it is drawn for `side` (upside down at the top). */
  function figureFor(side, box, size) {
    if (side !== 'top') return box
    return { left: size - box.right, right: size - box.left, top: size - box.bottom, bottom: size - box.top }
  }

  const ease = (v) => 1 - (1 - v) ** 3
  const clampTo = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi))

  /**
   * Where her canvas goes while hidden at `side` of `screen`: `show` 0 is all hidden, 1 peeking out.
   * `along` is her canvas's place along the edge (y at the left or right, x at the top); it is kept so
   * that all of her figure is beside the screen. `box` is her figure in her canvas, `size` the canvas.
   */
  function dockPlace(side, screen, along, box, size, show) {
    const f = figureFor(side, box, size)
    const w = f.right - f.left
    const h = f.bottom - f.top
    const k = ease(clampTo(show, 0, 1))
    if (side === 'top') {
      const x = clampTo(along, screen.x - f.left, right(screen) - f.right)
      const hidden = screen.y - f.bottom - HIDE_GAP
      const peek = screen.y - f.bottom + PEEK_TOP * h
      return { x, y: hidden + (peek - hidden) * k, along: x }
    }
    const y = clampTo(along, screen.y - f.top, bottom(screen) - f.bottom)
    if (side === 'left') {
      const hidden = screen.x - f.right - HIDE_GAP
      const peek = screen.x - f.right + PEEK_SIDE * w
      return { x: hidden + (peek - hidden) * k, y, along: y }
    }
    const hidden = right(screen) - f.left + HIDE_GAP
    const peek = right(screen) - f.left - PEEK_SIDE * w
    return { x: hidden + (peek - hidden) * k, y, along: y }
  }

  /**
   * Her two hands holding on to the edge: the box they take on the page (the fingers come `reach` pixels
   * over the edge; the hands are `span` pixels long along it), around where her head is.
   */
  function handsPlace(side, screen, along, box, size, span, reach) {
    const f = figureFor(side, box, size)
    if (side === 'top') {
      const x = clampTo(along, screen.x - f.left, right(screen) - f.right)
      const middle = x + (f.left + f.right) / 2
      return { x: clampTo(middle - span / 2, screen.x, right(screen) - span), y: screen.y, width: span, height: reach }
    }
    const y = clampTo(along, screen.y - f.top, bottom(screen) - f.bottom)
    // About where her shoulders would be: a third down her figure.
    const middle = y + f.top + (f.bottom - f.top) * 0.36
    const top = clampTo(middle - span / 2, screen.y, bottom(screen) - span)
    const x = side === 'left' ? screen.x : right(screen) - reach
    return { x, y: top, width: reach, height: span }
  }

  /** Coming out from the edge: a place on the screen just inside it, standing the right way up. */
  function comeOut(side, screen, along, box, size, ground) {
    const margin = 12
    if (side === 'top') {
      const x = clampTo(along, screen.x - box.left + margin, right(screen) - box.right - margin)
      return { x, y: clampTo(screen.y - box.top + margin, screen.y - box.top, ground) }
    }
    const y = clampTo(along, screen.y - box.top, ground)
    const x = side === 'left' ? screen.x - box.left + margin : right(screen) - box.right - margin
    return { x, y }
  }

  const api = { SNAP, screenAt, dockSide, nearestSide, dockPlace, handsPlace, comeOut, figureFor }
  if (typeof module === 'object' && module.exports) module.exports = api
  else root.petEdge = api
})(typeof window !== 'undefined' ? window : globalThis)
