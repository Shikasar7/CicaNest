'use strict'

// 贴边藏起: the two little hands holding on to the screen edge while she hides behind it.
//
// Drawn here rather than shipped as pictures, so every pet has them: the skin and outline colours are taken
// from her own sprite (her face and her lines), and a pixel-art pet gets pixel hands. A skin can bring its
// own instead: hands.png beside pet.json, one hand with the fingers pointing down (as over the top edge).
//
// One hand is drawn in "edge space": u along the edge (0..width), v away from it into the screen (0..reach),
// the edge at v = 0. Each side maps that onto the canvas.

;(function (root) {
  const SKIN = [255, 228, 214]
  const LINE = [96, 70, 84]

  /** Her skin and outline colours, from a frame of her sprite (an <img>); defaults when none are found. */
  function colorsOf(image, frame) {
    const canvas = document.createElement('canvas')
    const side = Math.min(frame, 160)
    canvas.width = side
    canvas.height = side
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    ctx.drawImage(image, 0, 0, frame, frame, 0, 0, side, side)
    const data = ctx.getImageData(0, 0, side, side).data
    const skins = new Map()
    const darks = []
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 200) continue
      const [r, g, b] = [data[i], data[i + 1], data[i + 2]]
      const max = Math.max(r, g, b)
      const min = Math.min(r, g, b)
      const light = (max + min) / 510
      // Skin: warm (red highest, then green, then blue), light, a little saturated.
      if (r >= g && g >= b && light > 0.72 && light < 0.97 && max - min > 12 && max - min < 90) {
        const key = `${r >> 3},${g >> 3},${b >> 3}`
        const entry = skins.get(key) || { n: 0, r: 0, g: 0, b: 0 }
        entry.n += 1
        entry.r += r
        entry.g += g
        entry.b += b
        skins.set(key, entry)
      }
      darks.push([light, r, g, b])
    }
    let skin = SKIN
    let best = null
    for (const entry of skins.values()) if (!best || entry.n > best.n) best = entry
    if (best && best.n >= 12) skin = [best.r / best.n, best.g / best.n, best.b / best.n].map(Math.round)
    let line = LINE
    if (darks.length > 50) {
      darks.sort((a, b) => a[0] - b[0])
      const few = darks.slice(0, Math.max(10, Math.round(darks.length * 0.06)))
      const mean = (k) => Math.round(few.reduce((sum, d) => sum + d[k], 0) / few.length)
      line = [mean(1), mean(2), mean(3)]
      // Pure black reads harsh on small hands: a little lighter, as an outline in her colours.
      if (line[0] + line[1] + line[2] < 90) line = line.map((c) => c + 40)
    }
    return { skin: `rgb(${skin.join(',')})`, line: `rgb(${line.join(',')})`, tip: 'rgba(255,135,160,0.45)' }
  }

  /** The transform from edge space to the canvas for a side (in canvas units). */
  function mapping(side, width, reach) {
    if (side === 'left') return [0, 1, 1, 0, 0, 0] // u down, v right
    if (side === 'right') return [0, 1, -1, 0, reach, 0] // u down, v left
    return [1, 0, 0, 1, 0, 0] // top: u right, v down
  }

  /**
   * A small round hand curled over the edge: a soft mitten whose end is four plump fingertips (the middle
   * ones reach furthest), with little creases between them. `mirror`: the other hand.
   */
  function drawHand(ctx, width, reach, colors, mirror, unit) {
    const stroke = Math.max(1, unit)
    const inset = stroke / 2
    const w = width - stroke
    const r = w / 8 // a fingertip's radius
    const short = [0.16, 0.03, 0, 0.1] // how much shorter than the longest each finger is (of the reach)
    if (mirror) short.reverse()
    const tips = short.map((s, i) => ({ x: inset + r * (2 * i + 1), y: reach - inset - r - s * reach }))
    ctx.beginPath()
    ctx.moveTo(inset, -stroke)
    ctx.lineTo(inset, tips[0].y)
    for (const tip of tips) ctx.arc(tip.x, tip.y, r, Math.PI, 0, true)
    ctx.lineTo(inset + w, -stroke)
    ctx.closePath()
    ctx.fillStyle = colors.skin
    ctx.fill()
    ctx.lineWidth = stroke
    ctx.lineJoin = 'round'
    ctx.strokeStyle = colors.line
    ctx.stroke()
    // Creases between the fingers, only near the tips.
    ctx.lineWidth = stroke * 0.8
    ctx.lineCap = 'round'
    for (let i = 0; i < 3; i += 1) {
      const x = inset + r * 2 * (i + 1)
      const y = Math.min(tips[i].y, tips[i + 1].y)
      ctx.beginPath()
      ctx.moveTo(x, y - r * 0.9)
      ctx.lineTo(x, y + r * 0.55)
      ctx.stroke()
    }
    // Rosy fingertips.
    ctx.fillStyle = colors.tip
    for (const tip of tips) {
      ctx.beginPath()
      ctx.ellipse(tip.x, tip.y + r * 0.25, r * 0.5, r * 0.38, 0, 0, Math.PI * 2)
      ctx.fill()
    }
  }

  /** Pixel art: every pixel fully there or not, so the small hands stay crisp when scaled up. */
  function crisp(ctx, w, h) {
    const image = ctx.getImageData(0, 0, w, h)
    const d = image.data
    for (let i = 3; i < d.length; i += 4) d[i] = d[i] > 110 ? 255 : 0
    ctx.putImageData(image, 0, 0)
  }

  /**
   * One hand on a canvas for `side`. `width` and `reach` are CSS pixels; `art` the CSS pixels of one art
   * pixel for pixel-art pets (0 for smooth ones); `custom` her own hand picture, if she has one.
   */
  function handCanvas({ side, width, reach, colors, mirror, art, custom }) {
    const canvas = document.createElement('canvas')
    const dpr = window.devicePixelRatio || 1
    // Pixel art is drawn at its own resolution and scaled up without smoothing.
    const per = art ? 1 / art : dpr
    const w = Math.max(4, Math.round(width * per))
    const r = Math.max(3, Math.round(reach * per))
    const across = side === 'top' ? w : r
    const down = side === 'top' ? r : w
    canvas.width = across
    canvas.height = down
    canvas.style.width = `${(across / per).toFixed(2)}px`
    canvas.style.height = `${(down / per).toFixed(2)}px`
    canvas.className = art ? 'hand pixel' : 'hand'
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    const [a, b, c, d, e, f] = mapping(side, w, r)
    ctx.setTransform(a, b, c, d, e, f)
    if (custom) {
      if (mirror) {
        ctx.translate(w, 0)
        ctx.scale(-1, 1)
      }
      ctx.imageSmoothingEnabled = !art
      ctx.drawImage(custom, 0, 0, w, r)
    } else {
      drawHand(ctx, w, r, colors, mirror, art ? 1 : dpr * Math.max(1.1, width / 20))
      if (art) crisp(ctx, across, down)
    }
    return canvas
  }

  root.petHands = { colorsOf, handCanvas }
})(window)
