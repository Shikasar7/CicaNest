'use strict'

// A little figure for a companion without pictures of her own: a round blob in a colour made from her id. Drawn here, as a pack like any other (pet.js's prepare reads it).

;(function (root) {
  const SIZE = 192
  const GROUND = 180

  /** A soft colour of her own, always the same for the same id. */
  function hue(id) {
    let n = 0
    for (const c of String(id)) n = (n * 31 + c.codePointAt(0)) % 360
    return n
  }

  function draw(ctx, x0, { h, eyes = 'open', mouth = 'smile', squash = 0, lean = 0, arm = 0, blush = false }) {
    const body = `hsl(${h} 62% 78%)`
    const line = `hsl(${h} 38% 38%)`
    ctx.save()
    ctx.translate(x0 + SIZE / 2, GROUND)
    ctx.rotate(lean)
    ctx.scale(1 + squash, 1 - squash)
    ctx.lineWidth = 5
    ctx.lineJoin = 'round'
    ctx.lineCap = 'round'
    ctx.strokeStyle = line
    ctx.fillStyle = body
    // feet
    for (const side of [-1, 1]) {
      ctx.beginPath()
      ctx.ellipse(side * 26, -6, 18, 9, 0, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
    }
    // arms
    for (const side of [-1, 1]) {
      ctx.save()
      ctx.translate(side * 58, -62)
      ctx.rotate(side * (side > 0 ? 0.6 - arm : 0.6))
      ctx.beginPath()
      ctx.ellipse(side * 10, 8, 18, 11, side * 0.5, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.restore()
    }
    // body
    ctx.beginPath()
    ctx.moveTo(-60, -14)
    ctx.bezierCurveTo(-74, -70, -58, -146, 0, -146)
    ctx.bezierCurveTo(58, -146, 74, -70, 60, -14)
    ctx.bezierCurveTo(40, -2, -40, -2, -60, -14)
    ctx.fill()
    ctx.stroke()
    ctx.fillStyle = 'rgba(255,255,255,0.55)'
    ctx.beginPath()
    ctx.ellipse(0, -44, 32, 26, 0, 0, Math.PI * 2)
    ctx.fill()
    // A heart (a letter would be mirrored when she walks to the left).
    ctx.fillStyle = `hsl(${h} 70% 62%)`
    ctx.beginPath()
    ctx.moveTo(0, -28)
    ctx.bezierCurveTo(-26, -44, -16, -62, 0, -50)
    ctx.bezierCurveTo(16, -62, 26, -44, 0, -28)
    ctx.fill()
    ctx.fillStyle = line
    // face
    for (const side of [-1, 1]) {
      ctx.beginPath()
      if (eyes === 'open') {
        ctx.ellipse(side * 22, -98, 6, 9, 0, 0, Math.PI * 2)
        ctx.fill()
      } else {
        ctx.arc(
          side * 22,
          eyes === 'happy' ? -94 : -102,
          9,
          eyes === 'happy' ? Math.PI : 0,
          eyes === 'happy' ? Math.PI * 2 : Math.PI,
        )
        ctx.stroke()
      }
      if (blush) {
        ctx.save()
        ctx.fillStyle = 'rgba(255,120,150,0.45)'
        ctx.beginPath()
        ctx.ellipse(side * 38, -84, 10, 6, 0, 0, Math.PI * 2)
        ctx.fill()
        ctx.restore()
      }
    }
    ctx.beginPath()
    if (mouth === 'smile') ctx.arc(0, -86, 8, 0.15 * Math.PI, 0.85 * Math.PI)
    else if (mouth === 'open') ctx.ellipse(0, -80, 6, 8, 0, 0, Math.PI * 2)
    else if (mouth === 'pout') ctx.arc(0, -74, 7, 1.2 * Math.PI, 1.8 * Math.PI)
    else ctx.arc(0, -80, 4, 0, Math.PI * 2)
    ctx.stroke()
    ctx.restore()
  }

  const ACTIONS = {
    idle: { fps: 1.5, loop: true, poses: [{}, { squash: 0.02 }] },
    blink: { fps: 10, loop: false, poses: [{ eyes: 'shut' }] },
    wave: {
      fps: 5,
      loop: true,
      poses: [
        { arm: 1.6, mouth: 'open' },
        { arm: 2.1, mouth: 'open' },
      ],
    },
    walk: { fps: 5, loop: true, poses: [{ lean: -0.06 }, { squash: 0.05 }, { lean: 0.06 }, { squash: 0.05 }] },
    sleep: {
      fps: 0.8,
      loop: true,
      poses: [
        { eyes: 'shut', mouth: 'o', squash: 0.06 },
        { eyes: 'shut', mouth: 'o', squash: 0.1 },
      ],
    },
    happy: {
      fps: 6,
      loop: true,
      poses: [
        { eyes: 'happy', mouth: 'open', squash: -0.04, blush: true },
        { eyes: 'happy', mouth: 'open', squash: 0.05, blush: true },
      ],
    },
    shy: { fps: 2, loop: true, poses: [{ eyes: 'happy', blush: true, lean: 0.05 }] },
    pout: { fps: 2, loop: true, poses: [{ mouth: 'pout', lean: -0.04, blush: true }] },
    think: {
      fps: 2,
      loop: true,
      poses: [
        { mouth: 'o', lean: 0.04 },
        { mouth: 'o', lean: -0.02 },
      ],
    },
    talk: { fps: 6, loop: true, poses: [{ mouth: 'open' }, {}] },
    lifted: { fps: 6, loop: true, poses: [{ mouth: 'open', squash: -0.08, arm: 1 }] },
  }

  function pack(info) {
    const h = hue(info.botId)
    const actions = {}
    for (const [name, { fps, loop, poses }] of Object.entries(ACTIONS)) {
      const canvas = document.createElement('canvas')
      canvas.width = SIZE * poses.length
      canvas.height = SIZE
      const ctx = canvas.getContext('2d')
      poses.forEach((pose, n) => draw(ctx, n * SIZE, { h, ...pose }))
      actions[name] = { src: canvas.toDataURL('image/png'), frames: poses.length, fps, loop }
    }
    return {
      id: 'placeholder',
      name: info.name,
      label: '小人',
      rendering: 'smooth',
      frame: SIZE,
      baseline: GROUND,
      figure: 150,
      actions,
      hands: null,
      lines: {},
    }
  }

  const api = { pack, hue }
  if (typeof module !== 'undefined' && module.exports) module.exports = api
  else root.petPlaceholder = api
})(typeof window === 'undefined' ? globalThis : window)
