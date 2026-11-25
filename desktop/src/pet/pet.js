'use strict'

// The 桌宠 overlay page: draws every pet, lets the mouse through except over a pet or its bubble, and runs
// each pet's little life.
//
// What she does by herself: stands, blinks, now and then tilts her head and smiles, walks along the ground,
// sits down and dozes off when you are away (yawn → sleep), wakes up rubbing her eyes and stretches when you
// come back, and greets you. Once in a while, when you have not talked to her for long, a small tsundere
// scene: arms crossed, cheeks puffed, 「哼」— then she peeks to see whether you noticed.
//
// What you can do: click — talk to her in a bubble; double-click — the chat window; right-click — her menu
// (戳一下 there: she pouts, turns away, then peeks; poke again while she peeks and she is caught, shy); rub the
// mouse over her head — 摸头 (she melts); drag — pick her up (she dangles and protests) and put her down.
//
// 贴边藏起: put down against the left, right or top edge of her screen, she hides behind it and only her two
// hands hold on to the edge (hands.js). The mouse over them: she peeks out (upside down from the top); a
// click, a drag, a reminder or 「出来吧」 in her menu: she comes out. The geometry is in edge.js.
//
// What nest says: a message to her → she thinks (…); her reply → she talks, then shows how she feels from
// the words (哼 → pout and peek; 摸摸 / 辛苦 → she pats your head; 开心 → happy; 害羞 → shy).
//
// A pack without some of these actions falls back to the ones it has (FALLBACK), or skips that step.
//
// Her work: a background task of hers going on, she is busy with it (a mark over her head); a
// step that waits for the user's nod, she puts her hand up; done, she is glad, not done, she is down. What she
// says in a 桌面群 shows on her too. Two pets near each other turn and wave now and then (social.js).
//
// A companion without pictures may be a little figure drawn here (placeholder.js).
//
// Skins and accessories: the main process sends the skin she wears (a whole pack) and the accessories on her
// (small pictures worn on the head). Where her head is, is found in every frame of the pack (the top of
// what is drawn), so a crown sits on her head whether she stands, walks, sits or is held up.

const bridge = window.nestPet
const edge = window.petEdge
const hands = window.petHands
const stage = document.getElementById('stage')

const DISPLAY_HEIGHT = 180 // a standing figure about this many CSS pixels tall; pixel packs use a whole multiple
const WALK_SPEED = 46 // CSS pixels per second
const DRAG_START = 4
const THINK_GIVE_UP_MS = 120_000
const SAY_SHOW_MS = 9000
const AWAY_SECONDS = 300 // she dozes off after this long without the mouse or keyboard
const NIGHT = (hour) => hour >= 23 || hour < 7
const LONELY_MS = 40 * 60_000 // no talk for this long: now and then a tsundere moment
const PAT_STROKES = 3 // direction changes over her head within PAT_WINDOW_MS make a pat
const PAT_WINDOW_MS = 1400
const PEEK_MS = 280 // hidden ↔ peeking
const PEEK_LINGER_MS = 800 // the mouse gone: she stays out this long
const PEEK_TILT = 0.08 // peeking from a side she leans into the screen a little (radians)

// The action to show when a pack does not have the one wanted ('' : leave the step out).
const FALLBACK = {
  smile: 'idle',
  talk: 'wave',
  think: 'idle',
  happy: 'wave',
  shy: 'idle',
  pout: 'idle',
  peek: 'idle',
  greet: 'wave',
  wave: 'idle',
  surprised: 'idle',
  work: 'think',
  raise: 'wave',
  sad: 'pout',
  sit: '',
  yawn: '',
  sleep: 'idle',
  wake: '',
  stretch: '',
  lifted: 'idle',
  headpat: 'wave',
  patted: 'shy',
  blink: 'idle',
  walk: 'idle',
}

// What she feels from her own words, first match wins: a little scene after she talks.
const MOODS = [
  { test: /哼|才不|才没|笨蛋|讨厌|不理你/, scene: 'pout' },
  { test: /摸摸|摸头|辛苦|乖[，。！~]?|别太累|抱抱/, scene: 'headpat' },
  { test: /害羞|脸红|不好意思|羞/, scene: 'shy' },
  { test: /哈哈|嘿嘿|好耶|开心|太好了|喜欢|[!！]{1,}$/, scene: 'happy' },
]
// Words of yours that make her want to pat your head once she has answered.
const TIRED = /累|困|难过|伤心|烦|好难|压力|失败|哭/

const pets = new Map() // bot id -> pet
// The screens' work areas in page pixels, the primary one first (from the main process).
let screens = [{ x: 0, y: 0, width: window.innerWidth, height: window.innerHeight }]
let idle = { seconds: 0, hour: new Date().getHours() }
let interactive = false
const pointer = new window.petPointer.Pointer()

const rand = (a, b) => a + Math.random() * (b - a)
const pick = (list) => (list && list.length ? list[Math.floor(Math.random() * list.length)] : '')
const now = () => performance.now()

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = reject
    image.src = src
  })
}

async function prepare(pack) {
  const actions = {}
  for (const [name, action] of Object.entries(pack.actions)) {
    try {
      actions[name] = { ...action, image: await loadImage(action.src) }
    } catch {
      // a sheet that does not load is an action she does not have
    }
  }
  // Every frame's head: the top of what is drawn, and the middle of the top part across.
  const heads = {}
  for (const [name, action] of Object.entries(actions)) heads[name] = headsOf(action.image, pack.frame, action.frames)
  const figure = pack.figure || pack.frame * 0.85
  // Pixel art: every art pixel a whole number of screen pixels (on a 125% screen, 3 rather than 2.5, which
  // would make some pixels wider than others).
  const dpr = window.devicePixelRatio || 1
  const device = pack.rendering === 'pixel' ? Math.max(1, Math.round((DISPLAY_HEIGHT * dpr) / figure)) : 0
  const scale = device ? device / dpr : DISPLAY_HEIGHT / figure
  const size = pack.frame * scale
  const idleImage = actions.idle?.image
  const box = idleImage ? figureBox(idleImage, pack.frame, scale) : { left: 0, top: 0, right: size, bottom: size }
  let custom = null
  try {
    if (pack.hands) custom = await loadImage(pack.hands)
  } catch {
    // a hands.png that does not load: the drawn ones
  }
  const colors = idleImage ? hands.colorsOf(idleImage, pack.frame) : null
  return { ...pack, actions, heads, scale, device, size, lines: pack.lines || {}, box, colors, customHands: custom }
}

/** Where her figure is in her canvas (CSS pixels): the opaque part of her first idle frame. */
function figureBox(image, frame, scale) {
  const canvas = document.createElement('canvas')
  canvas.width = frame
  canvas.height = frame
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(image, 0, 0, frame, frame, 0, 0, frame, frame)
  const data = ctx.getImageData(0, 0, frame, frame).data
  let left = frame
  let top = frame
  let right = -1
  let bottom = -1
  for (let y = 0; y < frame; y += 1) {
    for (let x = 0; x < frame; x += 1) {
      if (data[(y * frame + x) * 4 + 3] > 24) {
        left = Math.min(left, x)
        right = Math.max(right, x)
        top = Math.min(top, y)
        bottom = Math.max(bottom, y)
      }
    }
  }
  if (right < 0) return { left: 0, top: 0, right: frame * scale, bottom: frame * scale }
  return { left: left * scale, top: top * scale, right: (right + 1) * scale, bottom: (bottom + 1) * scale }
}

function headsOf(image, frame, count) {
  const canvas = document.createElement('canvas')
  canvas.width = image.width
  canvas.height = image.height
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(image, 0, 0)
  const heads = []
  for (let i = 0; i < count; i += 1) {
    const data = ctx.getImageData(i * frame, 0, frame, frame).data
    const opaque = (x, y) => data[(y * frame + x) * 4 + 3] > 24
    let top = -1
    for (let y = 0; y < frame && top < 0; y += 1) {
      for (let x = 0; x < frame; x += 1) {
        if (opaque(x, y)) {
          top = y
          break
        }
      }
    }
    if (top < 0) {
      heads.push({ x: frame / 2, y: frame / 2 })
      continue
    }
    // The head is about the top fifth of a standing figure: its middle across.
    const band = Math.max(4, Math.round(frame * 0.12))
    let left = frame
    let right = -1
    for (let y = top; y < Math.min(frame, top + band); y += 1) {
      for (let x = 0; x < frame; x += 1) {
        if (opaque(x, y)) {
          left = Math.min(left, x)
          right = Math.max(right, x)
        }
      }
    }
    heads.push({ x: right >= left ? (left + right + 1) / 2 : frame / 2, y: top })
  }
  return heads
}

async function prepareAccessories(list) {
  const worn = []
  for (const acc of list || []) {
    try {
      const handsImage = acc.hands ? await loadImage(acc.hands).catch(() => null) : null
      worn.push({ ...acc, image: await loadImage(acc.src), handsImage })
    } catch {
      // a picture that does not load is not worn
    }
  }
  return worn
}

// -- one pet ---------------------------------------------------------------------------------------------

class Pet {
  constructor(info, pack, accessories) {
    this.botId = info.botId
    this.name = info.name
    this.skin = info.skin
    this.pack = pack
    this.accessories = accessories
    this.update(info)
    this.el = document.createElement('div')
    this.el.className = `pet ${pack.rendering}`
    this.canvas = document.createElement('canvas')
    // Drawn at the screen's own pixels (a 150% screen gets 1.5× as many), so a detailed skin stays sharp.
    this.dpr = window.devicePixelRatio || 1
    this.canvas.width = pack.device ? pack.frame * pack.device : Math.round(pack.size * this.dpr)
    this.canvas.height = this.canvas.width
    this.canvas.style.width = `${pack.size}px`
    this.canvas.style.height = `${pack.size}px`
    this.ctx = this.canvas.getContext('2d', { willReadFrequently: true })
    this.ctx.imageSmoothingEnabled = pack.rendering !== 'pixel'
    this.el.append(this.canvas)
    stage.append(this.el)

    const home = screens[0]
    const ground = home.y + home.height - pack.baseline * pack.scale
    const index = pets.size
    const start = info.position || { x: home.x + home.width - (index + 1) * (pack.size + 8), y: ground }
    this.x = start.x
    this.y = start.y
    this.dock = null // hidden at a screen edge: { side, along, show, overAt }
    this.handsEl = null
    this.clamp()
    this.facing = 1
    this.mode = 'idle' // idle | walk | asleep | drag | scene
    this.scene = [] // steps still to play: { action, ms, line, hop }
    this.step = null // the step playing: { action, until, since, hop }
    this.since = now()
    this.target = null
    const t = now()
    this.next = t + rand(4000, 9000)
    this.blinkAt = t + rand(2500, 6000)
    this.blinkUntil = 0
    this.smileUntil = 0
    this.waiting = 0 // when a message to her was seen and no answer has come yet
    this.talkUntil = 0
    this.lastTalk = t
    this.lonelyAt = t + LONELY_MS
    this.said = null // { text, until }
    this.open = false // the bubble with an input
    this.problem = ''
    this.bubble = null
    this.drawn = ''
    this.afterWalk = null // a scene to play once she has walked where she was going
    this.strokes = [] // over her head: when the mouse changed direction
    this.lastDx = 0

    this.canvas.addEventListener('mousedown', (e) => this.press(e))
    this.canvas.addEventListener('dblclick', () => {
      if (!this.dock) bridge.openChat(this.botId)
    })
    this.canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault()
      bridge.menu(this.botId, Boolean(this.dock))
    })
    // Back where she hid before a restart.
    if (info.position?.dock) this.hide(info.position.dock, { quietly: true })
  }

  update(info) {
    this.live = info.live
    this.channels = new Set(info.channels)
    this.groups = new Set(info.groups || [])
    this.setWork(info.work || {})
  }

  /** Working on something, or waiting for the user's nod: a mark over her head. */
  setWork({ working = 0, asking = 0 }) {
    this.working = working
    if (asking && !this.asking && this.said !== undefined) this.say('这一步要你点个头', 8000)
    this.asking = asking
  }

  /** The screen she is on (where her feet are). */
  screen() {
    return edge.screenAt(screens, { x: this.x + this.pack.size / 2, y: this.y + this.pack.baseline * this.pack.scale })
  }

  /** Her figure's box on the page. */
  figure() {
    const b = this.pack.box
    return { left: this.x + b.left, top: this.y + b.top, right: this.x + b.right, bottom: this.y + b.bottom }
  }

  clamp() {
    if (this.dock) return
    const size = this.pack.size
    const s = this.screen()
    this.x = Math.min(Math.max(this.x, s.x - size * 0.3), s.x + s.width - size * 0.7)
    this.y = Math.min(Math.max(this.y, s.y - size * 0.2), s.y + s.height - this.pack.baseline * this.pack.scale)
  }

  /** The action a pack can show for `want`, following FALLBACK; '' when it has nothing for it. */
  resolve(want) {
    let action = want
    for (let i = 0; i < 4 && action; i += 1) {
      if (this.pack.actions[action]) return action
      action = FALLBACK[action] ?? 'idle'
    }
    return this.pack.actions[action] ? action : ''
  }

  line(kind) {
    const text = pick(this.pack.lines[kind])
    if (text) this.say(text, 4500)
  }

  say(text, ms = SAY_SHOW_MS) {
    this.said = { text, until: now() + ms + text.length * 60 }
    this.bubbleKey = null
  }

  // -- scenes: a few actions in a row ------------------------------------------------------------------

  /** `quiet`: no preset lines (her real words are in the bubble). */
  play(steps, { quiet = false } = {}) {
    if (this.dock) return // hidden at an edge: only peeking
    this.target = null
    this.afterWalk = null
    this.fallAsleepAfterScene = false
    this.scene = steps.filter((s) => this.resolve(s.action)).map((s) => (quiet ? { ...s, line: '' } : s))
    this.step = null
    this.mode = this.scene.length ? 'scene' : 'idle'
  }

  advance(t) {
    if (this.step && t < this.step.until) return
    const next = this.scene.shift()
    if (!next) {
      this.step = null
      this.mode = 'idle'
      this.next = t + rand(5000, 11000)
      return
    }
    this.step = { action: this.resolve(next.action), until: t + next.ms, since: t, hop: next.hop }
    if (next.line) this.line(next.line)
  }

  feel(kind, options = {}) {
    const scenes = {
      pout: [
        { action: 'pout', ms: 1600, line: 'poke' },
        { action: 'peek', ms: 2600, line: 'peek' },
        { action: 'pout', ms: 900 },
      ],
      caught: [
        { action: 'surprised', ms: 500 },
        { action: 'shy', ms: 2600, line: 'caught' },
      ],
      headpat: [{ action: 'headpat', ms: 3200, line: 'headpat' }, { action: 'smile', ms: 1500 }],
      shy: [{ action: 'shy', ms: 2800 }],
      sad: [{ action: 'sad', ms: 2600 }],
      meet: [
        { action: 'wave', ms: 1600, line: 'meet' },
        { action: 'smile', ms: 1800 },
      ],
      met: [
        { action: 'smile', ms: 900 },
        { action: 'wave', ms: 1700 },
        { action: 'happy', ms: 900, hop: true },
      ],
      happy: [
        { action: 'happy', ms: 900, hop: true },
        { action: 'happy', ms: 1300 },
      ],
      greet: [{ action: 'greet', ms: 2600, line: 'welcome' }],
      patted: [
        { action: 'patted', ms: 2600, line: 'patted' },
        { action: 'shy', ms: 1400 },
      ],
      wake: [
        { action: 'wake', ms: 1500, line: 'wake' },
        { action: 'stretch', ms: 1300 },
      ],
      lonely: [
        { action: 'pout', ms: 1800, line: 'lonely' },
        { action: 'peek', ms: 2800 },
        { action: 'pout', ms: 900 },
      ],
    }
    this.play(scenes[kind] || [], options)
  }

  poke() {
    if (this.mode === 'drag') return
    // Poked again while she is peeking: caught looking.
    if (this.mode === 'scene' && this.step?.action === this.resolve('peek')) this.feel('caught')
    else this.feel('pout')
  }

  // -- her life -----------------------------------------------------------------------------------------

  life(t, dt) {
    if (this.mode === 'drag') return
    if (this.waiting && t - this.waiting > THINK_GIVE_UP_MS) this.waiting = 0
    const busy = this.waiting || this.open || t < this.talkUntil
    if (this.dock) {
      // Out while the mouse is on her (or a little after), or while she has something to say.
      const want = busy || t - this.dock.overAt < PEEK_LINGER_MS ? 1 : 0
      const step = (dt * 1000) / PEEK_MS
      this.dock.show = want ? Math.min(1, this.dock.show + step) : Math.max(0, this.dock.show - step)
      return
    }
    const away = (idle.seconds >= AWAY_SECONDS || NIGHT(idle.hour)) && !this.working && !this.asking

    if (this.mode === 'scene') {
      this.advance(t)
      return
    }
    if (this.mode === 'asleep') {
      if (busy || !away) {
        this.mode = 'idle'
        this.feel('wake')
        // Woken by your coming back (not by a message): she greets you too.
        if (!busy && this.resolve('greet')) {
          this.scene.push({ action: 'greet', ms: 2400, line: 'welcome' })
          this.mode = 'scene'
        }
      }
      return
    }
    if (busy) {
      // Talking stops a stroll, not a hurry to call you (afterWalk).
      if (this.mode === 'walk' && !this.afterWalk) {
        this.mode = 'idle'
        this.target = null
      }
    } else if (away) {
      this.play([
        { action: 'sit', ms: 2200 },
        { action: 'yawn', ms: 1600 },
      ])
      // The last step only marks the end: when the scene is over she is asleep (see draw).
      this.scene.push({ action: 'idle', ms: 1 })
      this.mode = 'scene'
      this.fallAsleepAfterScene = true
      return
    }

    if (this.mode === 'walk' && this.target !== null) {
      const speed = this.afterWalk ? WALK_SPEED * 2.5 : WALK_SPEED
      const step = speed * dt * Math.sign(this.target - this.x)
      if (Math.abs(this.target - this.x) <= Math.abs(step)) {
        this.x = this.target
        this.target = null
        this.mode = 'idle'
        this.next = t + rand(5000, 12000)
        if (this.afterWalk) {
          const scene = this.afterWalk
          this.afterWalk = null
          this.feel(scene, { quiet: true })
          return
        }
      } else {
        this.x += step
      }
    }
    if (this.mode !== 'idle' || busy) return
    if (t >= this.blinkAt) {
      this.blinkUntil = t + 220
      this.blinkAt = t + rand(2500, 6500)
    }
    if (t >= this.lonelyAt) {
      this.lonelyAt = t + LONELY_MS * rand(0.8, 1.5)
      if (t - this.lastTalk > LONELY_MS) {
        this.feel('lonely')
        return
      }
    }
    if (t >= this.next) {
      const roll = Math.random()
      if (roll < 0.45 && this.resolve('walk') === 'walk') {
        const size = this.pack.size
        const s = this.screen()
        const span = Math.max(80, Math.min(s.width - size, 600))
        const goal = Math.min(Math.max(this.x + rand(-span, span), s.x), s.x + s.width - size)
        if (Math.abs(goal - this.x) > 30) {
          this.target = goal
          this.facing = goal > this.x ? 1 : -1
          this.mode = 'walk'
          this.since = t
        }
      } else if (roll < 0.7) {
        this.smileUntil = t + rand(2500, 4500)
      } else if (roll < 0.8) {
        this.play([{ action: 'wave', ms: 1800 }])
      }
      this.next = t + rand(6000, 15000)
    }
  }

  /** The action on screen now, and when it started (for its frames). */
  showing(t) {
    if (this.mode === 'drag') return [this.resolve('lifted') || 'idle', this.since]
    if (this.dock && !this.waiting && t >= this.talkUntil) return [this.resolve('peek') || 'idle', this.since]
    if (this.mode === 'asleep') return [this.resolve('sleep') || 'idle', this.since]
    if (this.mode === 'scene' && this.step) return [this.step.action, this.step.since]
    if (this.mode === 'walk') return [this.resolve('walk') || 'idle', this.since]
    if (this.waiting) return [this.resolve('think') || 'idle', this.waiting]
    if (this.asking) return [this.resolve('raise') || 'idle', this.since]
    if (t < this.talkUntil) return [this.resolve('talk') || 'idle', this.talkSince]
    if (t < this.blinkUntil) return [this.resolve('blink') || 'idle', this.blinkUntil - 220]
    if (t < this.smileUntil) return [this.resolve('smile') || 'idle', this.smileUntil]
    // Busy with a task: only a pack that has the picture shows it (thinking all the while would look stuck).
    if (this.working && this.pack.actions.work) return ['work', this.since]
    return ['idle', this.since]
  }

  frame(t, action, since) {
    const a = this.pack.actions[action]
    if (!a || a.frames <= 1) return 0
    const n = Math.floor(((t - since) / 1000) * (a.fps || 6))
    return a.loop === false ? Math.min(n, a.frames - 1) : n % a.frames
  }

  draw(t) {
    // A scene that ends in sleep leaves her asleep.
    if (this.fallAsleepAfterScene && this.mode === 'idle') {
      this.fallAsleepAfterScene = false
      this.mode = 'asleep'
      this.since = t
    }
    const [action, since] = this.showing(t)
    const index = this.frame(t, action, since)
    const a = this.pack.actions[action]
    const size = this.pack.size
    const dragging = this.mode === 'drag'
    const sway = dragging ? Math.sin(t / 160) * (this.pack.actions.lifted ? 0.05 : 0.09) : 0
    const hop = this.step?.hop ? Math.sin(Math.min(1, (t - this.step.since) / 450) * Math.PI) * 14 : 0
    const bounce = t < this.talkUntil && !this.pack.actions.talk ? Math.abs(Math.sin(t / 140)) * 3 : 0
    const lift = hop + bounce
    const worn = this.accessories.map((a) => a.id).join(',')
    const dock = this.dock
    if (dock) this.placeDocked(t)
    // Hidden at the top she hangs upside down; at a side she leans in as she peeks.
    const flip = dock?.side === 'top'
    const tilt = dock && !flip ? (dock.side === 'left' ? 1 : -1) * PEEK_TILT * dock.show : 0
    const key = `${action}:${index}:${this.facing}:${sway.toFixed(2)}:${lift.toFixed(1)}:${worn}:${flip}:${tilt.toFixed(3)}`
    if (key !== this.drawn) {
      this.drawn = key
      const ctx = this.ctx
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
      ctx.imageSmoothingEnabled = this.pack.rendering !== 'pixel'
      ctx.imageSmoothingQuality = 'high'
      ctx.clearRect(0, 0, size, size)
      if (flip) {
        ctx.translate(size / 2, size / 2)
        ctx.rotate(Math.PI)
        ctx.translate(-size / 2, -size / 2)
      }
      // Picked up: she hangs from the point above her head and sways; standing: she turns about her feet.
      ctx.translate(size / 2, dragging ? 0 : size)
      ctx.rotate(sway + tilt)
      ctx.scale(this.facing, 1)
      const f = this.pack.frame
      const top = (dragging ? 0 : -size) - lift
      ctx.drawImage(a.image, index * f, 0, f, f, -size / 2, top, size, size)
      this.drawAccessories(ctx, action, index, top)
    }
    this.el.style.transform = `translate(${Math.round(this.x)}px, ${Math.round(this.y)}px)`
    this.drawBubble(t)
  }

  /** On her head, in the body's drawing space (already turned the way she faces). */
  drawAccessories(ctx, action, index, top) {
    if (!this.accessories.length) return
    const head = this.pack.heads[action]?.[index]
    if (!head) return
    const s = this.pack.scale
    const figure = (this.pack.figure || this.pack.frame * 0.85) * s
    for (const acc of this.accessories) {
      const width = this.pack.rendering === 'pixel' ? acc.image.width * s : acc.width * figure
      const height = (acc.image.height * width) / acc.image.width
      const x = (head.x - this.pack.frame / 2) * s + acc.dx * figure - width / 2
      const y = top + head.y * s + acc.dy * figure - height
      ctx.drawImage(acc.image, x, y, width, height)
    }
  }

  // -- the bubble ------------------------------------------------------------------------------------

  drawBubble(t) {
    const showing = this.open || (this.said && t < this.said.until) || this.waiting
    const mark = this.asking ? '✋' : this.working ? '⚙' : ''
    if (mark !== (this.mark ?? '')) {
      this.mark = mark
      this.markEl?.remove()
      this.markEl = null
      if (mark) {
        this.markEl = document.createElement('div')
        this.markEl.className = `mark ${this.asking ? 'asking' : 'working'}`
        this.markEl.textContent = mark
        this.markEl.title = this.asking ? '有一步要你点头' : '在后台做事'
        this.el.append(this.markEl)
      }
    }
    const zzz = this.mode === 'asleep'
    if (zzz !== Boolean(this.zzz)) {
      if (zzz) {
        this.zzz = document.createElement('div')
        this.zzz.className = 'zzz'
        this.zzz.textContent = 'z z'
        this.el.append(this.zzz)
      } else {
        this.zzz.remove()
        this.zzz = null
      }
    }
    const key = showing ? `${this.open}:${this.said?.text ?? ''}:${Boolean(this.waiting)}:${this.problem}` : ''
    if (key === this.bubbleKey) return
    this.bubbleKey = key
    if (!showing) {
      this.bubble?.remove()
      this.bubble = null
      return
    }
    const typed = this.bubble?.querySelector('textarea')?.value ?? ''
    this.bubble?.remove()
    const bubble = document.createElement('div')
    bubble.className = 'bubble'
    const name = document.createElement('span')
    name.className = 'name'
    name.textContent = this.name
    bubble.append(name)
    if (this.waiting) {
      const dots = document.createElement('span')
      dots.className = 'dots said'
      dots.innerHTML = '<span>·</span><span>·</span><span>·</span>'
      bubble.append(dots)
    } else if (this.said && t < this.said.until) {
      const said = document.createElement('div')
      said.className = 'said'
      said.textContent = this.said.text
      bubble.append(said)
    }
    if (this.open) {
      const input = document.createElement('textarea')
      input.rows = 2
      input.maxLength = 4000
      input.placeholder = this.live ? `跟${this.name}说…` : `${this.name}现在不会回话`
      input.value = typed
      input.addEventListener('keydown', (e) => this.key(e, input))
      bubble.append(input)
      const hint = document.createElement('span')
      hint.className = this.problem ? 'problem' : 'hint'
      hint.textContent = this.problem || 'Enter 发送 · Esc 收起 · 右键有菜单 · 在她头上来回摸摸'
      bubble.append(hint)
      setTimeout(() => input.focus(), 0)
    }
    this.el.append(bubble)
    this.bubble = bubble
  }

  key(event, input) {
    if (event.key === 'Escape') {
      this.close()
      return
    }
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
    event.preventDefault()
    const text = input.value.trim()
    if (!text) return
    input.disabled = true
    bridge.say(this.botId, text).then((result) => {
      input.disabled = false
      if (result?.ok) {
        input.value = ''
        this.problem = ''
      } else {
        this.problem = result?.error || '没发出去'
      }
      this.bubbleKey = null
    })
  }

  toggle() {
    if (this.open) this.close()
    else {
      this.open = true
      this.problem = ''
      if (this.mode === 'asleep') {
        this.mode = 'idle'
        this.feel('wake')
      }
      bridge.focus()
    }
  }

  close() {
    this.open = false
    this.problem = ''
    this.bubbleKey = null
  }

  // -- the mouse ------------------------------------------------------------------------------------

  press(event) {
    if (event.button !== 0) return
    event.preventDefault()
    const start = { x: event.clientX, y: event.clientY, px: this.x, py: this.y }
    let dragging = false
    const move = (e) => {
      if (this.gone) return
      const dx = e.clientX - start.x
      const dy = e.clientY - start.y
      if (!dragging && Math.hypot(dx, dy) < DRAG_START) return
      if (!dragging) {
        // Pulled away from the edge she hid at: she is out, held up where the mouse is.
        if (this.dock) {
          this.unhide()
          start.px = e.clientX - this.pack.size / 2 - dx
          start.py = e.clientY - this.pack.size * 0.15 - dy
        }
        dragging = true
        this.target = null
        this.scene = []
        this.step = null
        this.mode = 'drag'
        this.since = now()
        this.el.classList.add('dragging')
        this.line('lifted')
      }
      this.x = start.px + dx
      this.y = start.py + dy
      this.clamp()
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      if (this.gone) return
      if (dragging) {
        this.el.classList.remove('dragging')
        this.mode = 'idle'
        this.drawn = ''
        // Put down against an edge of her screen: she hides behind it.
        const side = edge.dockSide(this.figure(), screens)
        if (side) {
          this.hide(side)
          return
        }
        this.play([{ action: 'surprised', ms: 500 }])
        bridge.savePosition(this.botId, { x: this.x, y: this.y })
      } else if (this.dock) {
        this.comeOut()
      } else {
        this.toggle()
      }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  /** The mouse over her head going back and forth a few times is a pat. */
  rub(x, y, dx) {
    if (this.mode === 'drag' || !dx) return
    const lx = x - this.x
    const ly = y - this.y
    const s = this.pack.scale
    const feet = this.pack.baseline * s
    const tall = (this.pack.figure || this.pack.frame * 0.85) * s
    const onHead = ly >= feet - tall && ly <= feet - tall * 0.62 && lx >= this.pack.size * 0.2 && lx <= this.pack.size * 0.8
    if (!onHead) return
    const t = now()
    if (this.lastDx && Math.sign(dx) !== Math.sign(this.lastDx)) {
      this.strokes = this.strokes.filter((at) => t - at < PAT_WINDOW_MS)
      this.strokes.push(t)
      if (this.strokes.length >= PAT_STROKES) {
        this.strokes = []
        const patting = this.mode === 'scene' && this.step?.action === this.resolve('patted')
        if (patting) this.step.until = t + 1800
        else {
          if (this.mode === 'asleep') this.mode = 'idle'
          this.feel('patted')
        }
      }
    }
    this.lastDx = dx
  }

  /** Whether (x, y) on the page is on her (a drawn pixel) or her bubble. */
  hit(x, y) {
    if (this.bubble) {
      const r = this.bubble.getBoundingClientRect()
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return true
    }
    if (this.handsEl) {
      const r = this.handsEl.getBoundingClientRect()
      if (x >= r.left - 2 && x <= r.right + 2 && y >= r.top - 2 && y <= r.bottom + 2) return true
    }
    const lx = Math.floor(x - this.x)
    const ly = Math.floor(y - this.y)
    if (lx < 0 || ly < 0 || lx >= this.pack.size || ly >= this.pack.size) return false
    return this.ctx.getImageData(Math.floor(lx * this.dpr), Math.floor(ly * this.dpr), 1, 1).data[3] > 24
  }

  // -- what nest says ---------------------------------------------------------------------------------

  /** A reminder went off: she hurries to the middle of the screen and waves (her words are in the bubble). */
  callOut() {
    if (this.mode === 'drag') return
    if (this.dock) this.comeOut({ quiet: true })
    const t = now()
    if (this.mode === 'asleep') this.mode = 'idle'
    this.scene = []
    this.step = null
    const s = this.screen()
    const middle = s.x + s.width / 2 - this.pack.size / 2
    if (Math.abs(middle - this.x) > 40 && this.resolve('walk') === 'walk') {
      this.target = middle
      this.facing = middle > this.x ? 1 : -1
      this.mode = 'walk'
      this.since = t
      this.afterWalk = 'greet'
    } else {
      this.mode = 'idle'
      this.feel('greet', { quiet: true })
    }
  }

  heard(event) {
    if (event.kind === 'reminder') {
      if (event.bot === this.botId) this.callOut()
      return
    }
    if (event.kind === 'work') {
      if (event.bot !== this.botId) return
      this.setWork(event)
      this.bubbleKey = null
      if (event.ended && this.mode !== 'drag' && !this.waiting) {
        if (this.mode === 'asleep') this.mode = 'idle'
        this.feel(event.ended === 'done' ? 'happy' : event.ended === 'stopped' ? '' : 'sad', { quiet: true })
      }
      return
    }
    // In a 桌面群 only what she says herself: the others' words show on them.
    if (this.groups.has(event.channel)) {
      if (event.kind !== 'message' || !event.mine || !event.text) return
      this.say(event.text.length > 160 ? `${event.text.slice(0, 157)}…` : event.text)
      this.talkSince = now()
      this.talkUntil = this.talkSince + Math.min(4500, 1200 + event.text.length * 70)
      return
    }
    if (!this.channels.has(event.channel)) return
    const t = now()
    this.lastTalk = t
    if (event.kind === 'message' && !event.mine) {
      this.waiting = t
      this.target = null
      this.yours = event.text
      if (this.mode === 'walk') this.mode = 'idle'
      if (this.mode === 'asleep') {
        this.mode = 'idle'
        this.feel('wake')
      }
    } else if (event.kind === 'message' && event.mine) {
      this.waiting = 0
      const text = event.text.length > 160 ? `${event.text.slice(0, 157)}…` : event.text
      this.say(text)
      const talking = Math.min(4500, 1200 + text.length * 70)
      this.talkSince = t
      this.talkUntil = t + talking
      // After talking, how she feels shows: from her words, or a pat when you sounded tired.
      const mood = MOODS.find((m) => m.test.test(text))?.scene ?? (TIRED.test(this.yours || '') ? 'headpat' : '')
      this.yours = ''
      if (mood) {
        setTimeout(() => {
          if ((this.mode === 'idle' || this.mode === 'walk') && !this.afterWalk) this.feel(mood, { quiet: true })
        }, talking)
      }
    } else if (event.kind === 'decision' && event.outcome !== 'replied') {
      this.waiting = 0
    }
    this.bubbleKey = null
  }

  // -- 贴边藏起 -------------------------------------------------------------------------------------------

  /** Hide behind `side` of her screen, where she is along it; only her hands stay on the edge. */
  hide(side, { quietly = false } = {}) {
    const s = this.screen()
    this.close()
    this.scene = []
    this.step = null
    this.target = null
    this.afterWalk = null
    this.fallAsleepAfterScene = false
    this.mode = 'idle'
    this.facing = side === 'right' ? -1 : 1
    this.dock = { side, along: side === 'top' ? this.x : this.y, show: quietly ? 0 : 0.6, overAt: 0, screen: s }
    this.since = now()
    this.el.classList.add(`dock-${side}`)
    this.makeHands()
    this.placeDocked(now())
    this.drawn = ''
    if (!quietly) bridge.savePosition(this.botId, { x: this.x, y: this.y, dock: side, ...this.alongPlace() })
  }

  /** What is saved for a hidden pet: her place along the edge (x at the top, y at a side). */
  alongPlace() {
    return this.dock.side === 'top' ? { x: this.dock.along, y: this.y } : { x: this.x, y: this.dock.along }
  }

  /** No longer hidden (she is where she peeked out; the caller puts her somewhere). */
  unhide() {
    if (!this.dock) return
    this.el.classList.remove(`dock-${this.dock.side}`)
    this.handsEl?.remove()
    this.handsEl = null
    this.dock = null
    this.drawn = ''
  }

  /** Out from the edge onto the screen, glad to be back. */
  comeOut({ quiet = false } = {}) {
    if (!this.dock) return
    const { side, along } = this.dock
    const s = this.dock.screen || this.screen()
    const ground = s.y + s.height - this.pack.baseline * this.pack.scale
    this.unhide()
    const place = edge.comeOut(side, s, along, this.pack.box, this.pack.size, ground)
    this.x = place.x
    this.y = place.y
    this.clamp()
    this.facing = side === 'right' ? -1 : 1
    this.mode = 'idle'
    if (!quiet) {
      this.play([
        { action: 'happy', ms: 700, hop: true },
        { action: 'smile', ms: 1200 },
      ])
    }
    bridge.savePosition(this.botId, { x: this.x, y: this.y })
  }

  /** Her two hands for the edge she hides at. */
  makeHands() {
    this.handsEl?.remove()
    const { side } = this.dock
    const figure = (this.pack.figure || this.pack.frame * 0.85) * this.pack.scale
    // Her own hands (hands.png: from an accessory she wears, else her skin) keep their picture's shape and
    // are a little bigger than the drawn ones.
    const custom = this.accessories.find((a) => a.handsImage)?.handsImage || this.pack.customHands
    // A small picture for a pixel-art pet is pixel art itself: drawn at one art pixel per picture pixel.
    const native = custom && this.pack.rendering === 'pixel' && custom.width <= 48
    const width = native ? custom.width * this.pack.scale : Math.max(16, Math.round(figure * (custom ? 0.17 : 0.14)))
    const reach = native
      ? custom.height * this.pack.scale
      : Math.max(10, Math.round(custom ? (width * custom.height) / custom.width : width * 0.62))
    const gap = Math.round(width * (native ? 0.4 : 0.75))
    const art = this.pack.rendering === 'pixel' ? this.pack.scale : 0
    const colors = this.pack.colors || { skin: '#ffe4d6', line: '#604654', tip: 'rgba(255,135,160,0.45)' }
    const el = document.createElement('div')
    el.className = `hands ${side}`
    for (const mirror of [false, true]) {
      el.append(hands.handCanvas({ side, width, reach, colors, mirror, art, custom }))
    }
    el.style.gap = `${gap}px`
    el.addEventListener('mousedown', (e) => this.press(e))
    el.addEventListener('contextmenu', (e) => {
      e.preventDefault()
      bridge.menu(this.botId, true)
    })
    this.handsSize = { span: width * 2 + gap, reach }
    stage.append(el)
    this.handsEl = el
  }

  /** Where she and her hands are while hidden, `show` of the way out. */
  placeDocked() {
    const d = this.dock
    const s = d.screen || this.screen()
    const place = edge.dockPlace(d.side, s, d.along, this.pack.box, this.pack.size, d.show)
    this.x = place.x
    this.y = place.y
    d.along = place.along
    if (this.handsEl) {
      const h = edge.handsPlace(d.side, s, d.along, this.pack.box, this.pack.size, this.handsSize.span, this.handsSize.reach)
      this.handsEl.style.transform = `translate(${Math.round(h.x)}px, ${Math.round(h.y)}px)`
      this.handsEl.classList.toggle('away', d.show > 0.25)
    }
    // Her bubble beside what shows of her, not off the screen.
    const f = edge.figureFor(d.side, this.pack.box, this.pack.size)
    if (d.side === 'left') this.el.style.setProperty('--dock-b', `${Math.round(f.right + 6)}px`)
    else if (d.side === 'right') this.el.style.setProperty('--dock-b', `${Math.round(this.pack.size - f.left + 6)}px`)
    else this.el.style.setProperty('--dock-b', `${Math.round(f.bottom + 6)}px`)
  }

  /** The mouse is over her hands or what peeks out of her. */
  touched(t) {
    if (this.dock) this.dock.overAt = t
  }

  /** The screens changed: a hidden pet stays at her edge of the screen she was on, if it is still there. */
  rescreen() {
    if (!this.dock) {
      this.clamp()
      return
    }
    this.dock.screen = edge.screenAt(screens, { x: this.x + this.pack.size / 2, y: this.y + this.pack.size / 2 })
  }

  command(kind) {
    if (kind === 'talk') {
      if (this.dock) this.dock.overAt = now()
      if (!this.open) this.toggle()
    } else if (kind === 'poke') this.poke()
    else if (kind === 'come-out') this.comeOut()
    else if (kind === 'dock' && !this.dock) {
      const side = edge.nearestSide(this.figure(), screens)
      if (side) this.hide(side)
    }
  }

  /** You came back after being away: she greets you (unless asleep — then waking does). */
  welcome() {
    if (this.mode === 'idle' && !this.dock) this.feel('greet')
  }

  remove() {
    this.gone = true // a drag under way must not put her hands back
    this.el.remove()
    this.handsEl?.remove()
    this.handsEl = null
  }
}

// -- the page ----------------------------------------------------------------------------------------------

function setScreens(list) {
  if (!Array.isArray(list) || !list.length) return
  screens = list
  pets.forEach((pet) => pet.rescreen())
}

// One state at a time, the newest: pictures take a while to load, and two states taken up side by side would each
// make the same pet, one of them left on the page with nobody to move it (or the older state would win).
let wanted = null
let taking = false

async function setState(state) {
  wanted = state
  if (taking) return
  taking = true
  try {
    while (wanted) {
      const next = wanted
      wanted = null
      try {
        await takeState(next)
      } catch (err) {
        console.error('pet state', err)
      }
    }
  } finally {
    taking = false
  }
}

async function takeState(state) {
  setScreens(state.screens)
  const seen = new Set()
  for (let info of state.pets) {
    seen.add(info.botId)
    const known = pets.get(info.botId)
    const accessories = await prepareAccessories(info.accessories)
    if (known && known.skin === info.skin) {
      known.update(info)
      known.accessories = accessories
      known.drawn = ''
      // Hidden at an edge: her hands may have changed with what she wears.
      if (known.dock) known.makeHands()
      continue
    }
    const pack = await prepare(info.pack || window.petPlaceholder.pack(info))
    if (!pack.actions.idle) continue
    // Another skin: a new pet where she stood (still hidden, if she was).
    if (known) {
      const place = known.dock ? { ...known.alongPlace(), dock: known.dock.side } : { x: known.x, y: known.y }
      info = { ...info, position: place }
      known.remove()
    }
    pets.set(info.botId, new Pet(info, pack, accessories))
  }
  for (const [botId, pet] of pets) {
    if (!seen.has(botId)) {
      pet.remove()
      pets.delete(botId)
    }
  }
}

function hover(x, y) {
  const dx = pointer.here && x >= 0 ? x - pointer.x : 0
  pointer.at(x, y, now())
  if (dx) pets.forEach((pet) => pet.rub(x, y, dx))
  const over = touch(x, y)
  if (over !== interactive) {
    interactive = over
    bridge.setInteractive(over)
  }
}

// The window lets the mouse through but still reports where it is (forwarded moves).
window.addEventListener('mousemove', (e) => hover(e.clientX, e.clientY))
window.addEventListener('mouseleave', () => hover(-1, -1))
window.addEventListener('resize', () => pets.forEach((pet) => pet.clamp()))

/** Whether (x, y) is on a pet; a hidden one under the mouse peeks out. */
function touch(x, y) {
  let over = false
  const t = now()
  for (const pet of pets.values()) {
    if (pet.hit(x, y)) {
      over = true
      if (!pointer.resting(t)) pet.touched(t)
    }
  }
  return over
}
// Another window taken into use: an empty bubble closes (one with words typed stays).
window.addEventListener('blur', () =>
  pets.forEach((pet) => {
    if (pet.open && !pet.bubble?.querySelector('textarea')?.value.trim()) pet.close()
  }),
)
// A click on nothing (only possible while a bubble had the mouse) closes the bubbles.
stage.addEventListener('mousedown', (e) => {
  if (e.target === stage) pets.forEach((pet) => pet.close())
})

let last = now()
function tick(t) {
  const dt = Math.min(0.1, (t - last) / 1000)
  last = t
  for (const pet of pets.values()) {
    pet.life(t, dt)
    pet.draw(t)
  }
  window.petSocial.look(t, [...pets.values()])
  // A pet that walked under a still pointer takes the mouse too.
  if (pointer.shouldAsk(t) && ![...pets.values()].some((pet) => pet.mode === 'drag')) {
    bridge.pointer().then(
      (place) => place && hover(place.x, place.y),
      () => {},
    )
  }
  if (pointer.here) {
    const over = touch(pointer.x, pointer.y)
    if (over !== interactive) {
      interactive = over
      bridge.setInteractive(over)
    }
  }
  requestAnimationFrame(tick)
}
requestAnimationFrame(tick)

bridge.onState((state) => setState(state))
bridge.onScreens((list) => setScreens(list))
bridge.onCommand(({ botId, kind }) => pets.get(botId)?.command(kind))
// For the desktop app's debug harness (scripts drive the pets through it).
window.nestPets = pets
bridge.onEvent((event) => pets.forEach((pet) => pet.heard(event)))
bridge.onIdle((value) => {
  const back = idle.seconds >= AWAY_SECONDS && value.seconds < 30 && !NIGHT(value.hour)
  idle = value
  if (back) pets.forEach((pet) => pet.welcome())
})
