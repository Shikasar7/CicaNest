'use strict'

// The little faces on the splash: one per account, asleep until it is online. Drawn like the console's
// (web/src/components/FaceGlyph.tsx); colours and motion are in splash.css.

;(function (root) {
  const NS = 'http://www.w3.org/2000/svg'
  const FACES = ['cat', 'bunny', 'bear', 'sprout']

  const TOPS = {
    cat: [
      ['path', 'head', { d: 'M11 22 L10.5 8.6 Q10.6 6.8 12.2 7.8 L22 14 Z M37 22 L37.5 8.6 Q37.4 6.8 35.8 7.8 L26 14 Z' }],
      ['path', 'inner', { d: 'M13.2 17 L13 11.6 L17.6 14.6 Z M34.8 17 L35 11.6 L30.4 14.6 Z' }],
    ],
    bear: [
      ['circle', 'head', { cx: 13, cy: 13, r: 6 }],
      ['circle', 'head', { cx: 35, cy: 13, r: 6 }],
      ['circle', 'inner', { cx: 13, cy: 13, r: 3 }],
      ['circle', 'inner', { cx: 35, cy: 13, r: 3 }],
    ],
    bunny: [
      ['ellipse', 'head', { cx: 16.5, cy: 9.5, rx: 4.6, ry: 9, transform: 'rotate(-12 16.5 9.5)' }],
      ['ellipse', 'head', { cx: 31.5, cy: 9.5, rx: 4.6, ry: 9, transform: 'rotate(12 31.5 9.5)' }],
      ['ellipse', 'inner', { cx: 16.5, cy: 10, rx: 2.1, ry: 6, transform: 'rotate(-12 16.5 10)' }],
      ['ellipse', 'inner', { cx: 31.5, cy: 10, rx: 2.1, ry: 6, transform: 'rotate(12 31.5 10)' }],
    ],
    sprout: [
      ['path', 'stem', { d: 'M24 13 V7.5' }],
      ['path', 'leaf', { d: 'M24 8 Q18 8.6 16.6 3.4 Q22.6 2.4 24 8 Z M24 8 Q30 8.6 31.4 3.4 Q25.4 2.4 24 8 Z' }],
    ],
  }

  const FACE = [
    ['path', 'head', { d: 'M24 12 C35 12 41 19 41 28 C41 36.5 34 41 24 41 C14 41 7 36.5 7 28 C7 19 13 12 24 12 Z' }],
    ['rect', 'open', { x: 16, y: 23, width: 3.6, height: 7.4, rx: 1.8 }],
    ['rect', 'open', { x: 28.4, y: 23, width: 3.6, height: 7.4, rx: 1.8 }],
    ['path', 'shut', { d: 'M14.6 27.4 Q17.8 30 21 27.4 M27 27.4 Q30.2 30 33.4 27.4' }],
    ['ellipse', 'cheek', { cx: 13.6, cy: 32.4, rx: 2.9, ry: 1.9 }],
    ['ellipse', 'cheek', { cx: 34.4, cy: 32.4, rx: 2.9, ry: 1.9 }],
    ['path', 'mouth', { d: 'M20.6 32.2 Q22.3 34.6 24 32.4 Q25.7 34.6 27.4 32.2' }],
  ]

  function draw(face) {
    const svg = document.createElementNS(NS, 'svg')
    svg.setAttribute('viewBox', '0 0 48 48')
    svg.setAttribute('aria-hidden', 'true')
    for (const [tag, kind, attributes] of [...(TOPS[face] || TOPS.cat), ...FACE]) {
      const node = document.createElementNS(NS, tag)
      node.setAttribute('class', `face-${kind}`)
      for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value))
      svg.append(node)
    }
    return svg
  }

  /** The face of an account, the same as in the console. */
  function faceOf(id) {
    let h = 0
    for (const c of String(id)) h = (h * 31 + c.codePointAt(0)) >>> 0
    return FACES[(h >>> 7) % FACES.length]
  }

  const api = { FACES, draw, faceOf }
  if (typeof module !== 'undefined') module.exports = api
  else root.nestFace = api
})(typeof window === 'undefined' ? globalThis : window)
