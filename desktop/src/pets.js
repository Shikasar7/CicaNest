'use strict'

// 桌宠 (伙伴模式): the companions living on the desktop.
//
// One transparent window covers the screens' work areas when they share a scale; with mixed scales there is one
// per screen that has a pet (a pet lives on one screen then; 「搬到另一块屏」 in her menu): see overlayAreas. The
// pets are drawn in it by pet/pet.js. It floats over other windows unless
// 「总在最前」 is turned off (tray or a pet's right-click menu; kept in pets.json as onTop). The window ignores the mouse except over a pet or its bubble (the page says which, as the
// pointer moves), so everything underneath stays clickable.
//
// A companion gets a pet when a sprite pack named after her is in the pets folder (<profile>\pets\<name>\
// pet.json, made by scripts/pet/make_pack.py; lines.json beside it for her little lines), and she is not
// turned off in the tray. hands.png beside a pet.json: her own hands for hiding at a screen edge (one hand,
// fingers pointing down as over the top edge; turned and mirrored for the rest). A skin without its own uses
// her default skin's when it is drawn in the same style; else the overlay draws a pair in her colours. An
// accessory can bring hands too (hands.png beside its acc.json: cat ears with cat paws, gloves): worn, they
// win over the skin's. The packs are the
// user's own pictures: they never go into the repository.

const fs = require('node:fs')
const path = require('node:path')
const { BrowserWindow, Menu, ipcMain, powerMonitor, screen } = require('electron')
const log = require('./log')
const { EventStream } = require('./events')
const { detailOf } = require('./nest')
const { FullscreenWatch } = require('./fullscreen')
const { pluginItems, syncPetPlugins } = require('./pet-plugins')

const PAGE = path.join(__dirname, 'pet', 'pet.html')
const REFRESH_MS = 60_000
const IDLE_MS = 10_000
// 主动搭话: a moment is when the user has paused (no mouse or keyboard for PAUSE_FROM s) but is still here
// (within PAUSE_UNTIL s). Then, at most every TRY_GAP_MS per companion and by chance, nest is asked whether
// she has something to say; nest keeps her limits (nest.core.chatter).
const CHATTER_MS = 60_000
const PAUSE_FROM = 20
const PAUSE_UNTIL = 120
const TRY_GAP_MS = 8 * 60_000
const TRY_CHANCE = 0.35
const NIGHT = (hour) => hour >= 23 || hour < 8
const SAY_MAX = 4000
const GOING = new Set(['queued', 'running', 'waiting_pc']) // a task's statuses while it is going on

/** The set of ids kept for a bot in a Map of sets. */
function idsOf(map, botId) {
  if (!map.has(botId)) map.set(botId, new Set())
  return map.get(botId)
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))
  } catch {
    return fallback
  }
}

const dataUrl = (file) => `data:image/png;base64,${fs.readFileSync(file).toString('base64')}`

function optionalDataUrl(file) {
  try {
    return dataUrl(file)
  } catch {
    return null
  }
}

/**
 * The overlay windows to make: [{ key, bounds, screens }]. One for all the screens when they share a scale
 * ("all"), else one per screen (its display id), the primary first.
 */
function overlayAreas(displays, primary) {
  if (displays.every((d) => d.scaleFactor === primary.scaleFactor)) {
    return [{ key: 'all', ...overlayArea(displays, primary) }]
  }
  return [primary, ...displays.filter((d) => d.id !== primary.id)].map((d) => ({
    key: String(d.id),
    label: String(d.label || ''),
    ...overlayArea([d], d),
  }))
}

/**
 * The overlay window's box and the screens in it (page coordinates: from the box's top left). All the
 * displays' work areas when they share one scale factor; with mixed scales Windows would draw one window
 * across them at a single scale, so then the primary display only.
 */
function overlayArea(displays, primary) {
  const same = displays.every((d) => d.scaleFactor === primary.scaleFactor)
  const list = same && displays.length ? displays : [primary]
  // The primary display first (where a new pet stands).
  const areas = [primary, ...list.filter((d) => d.id !== primary.id)].map((d) => d.workArea)
  const x = Math.min(...areas.map((a) => a.x))
  const y = Math.min(...areas.map((a) => a.y))
  const bounds = {
    x,
    y,
    width: Math.max(...areas.map((a) => a.x + a.width)) - x,
    height: Math.max(...areas.map((a) => a.y + a.height)) - y,
  }
  const screens = areas.map((a) => ({ x: a.x - x, y: a.y - y, width: a.width, height: a.height }))
  return { bounds, screens }
}

// What the 小窝 shows of a pet.
const HOME_ACTIONS = ['idle', 'blink', 'smile', 'think', 'stretch', 'yawn', 'walk', 'wave', 'pout', 'sleep', 'happy', 'sit', 'work']
const DOCK_SIDES = new Set(['left', 'right', 'top'])

function cleanLines(lines) {
  return Object.fromEntries(
    Object.entries(lines || {})
      .filter(([, list]) => Array.isArray(list))
      .map(([key, list]) => [key, list.filter((l) => typeof l === 'string' && l).map((l) => l.slice(0, 80))]),
  )
}

/** One skin: pet.json plus each action's sheet as a data URL; null when it cannot be used. */
function loadSkin(folder, id, fallbackLabel) {
  const meta = readJson(path.join(folder, 'pet.json'), null)
  if (!meta || typeof meta.actions !== 'object') return null
  const actions = {}
  for (const [name, action] of Object.entries(meta.actions)) {
    try {
      actions[name] = { ...action, src: dataUrl(path.join(folder, path.basename(String(action.file || '')))) }
    } catch {
      log.warn(`pet skin ${folder}: missing ${action.file}`)
    }
  }
  if (!actions.idle) {
    log.warn(`pet skin ${folder}: no idle action, skipped`)
    return null
  }
  const frame = Number(meta.frame) || 96
  return {
    id,
    name: String(meta.name || ''),
    label: String(meta.label || fallbackLabel),
    rendering: meta.rendering === 'smooth' ? 'smooth' : 'pixel',
    frame,
    baseline: Number(meta.baseline) || frame,
    figure: Number(meta.figure) || Math.round(frame * 0.85),
    actions,
    // Her own hands for hiding at a screen edge (one hand, fingers down, as over the top edge); without
    // it the overlay draws a pair.
    hands: optionalDataUrl(path.join(folder, 'hands.png')),
    // A skin's own lines win; else the companion's (lines.json beside her default pet.json).
    lines: cleanLines(readJson(path.join(folder, 'lines.json'), null)),
  }
}

function subfolders(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true }).filter((e) => e.isDirectory() && !e.name.startsWith('.'))
  } catch {
    return []
  }
}

/**
 * Every companion's pet in `dir`, by her name. A companion's folder holds her default skin (pet.json), more
 * skins in skins\<id>\ (another rendering style, another outfit: each a whole pack), accessories in
 * accessories\<id>\ (acc.json + item.png: worn on the head over any skin of the same style; hands.png: the
 * hands that go with it) and lines.json.
 */
function loadPacks(dir, extras = []) {
  const companions = new Map()
  for (const entry of subfolders(dir)) {
    const folder = path.join(dir, entry.name)
    const base = loadSkin(folder, 'default', '原版')
    const skins = base ? [base] : []
    for (const sub of subfolders(path.join(folder, 'skins'))) {
      const skin = loadSkin(path.join(folder, 'skins', sub.name), sub.name, sub.name)
      if (skin) skins.push(skin)
    }
    if (!skins.length) continue
    const lines = cleanLines(readJson(path.join(folder, 'lines.json'), {}))
    for (const skin of skins) if (!Object.keys(skin.lines).length) skin.lines = lines
    for (const skin of skins) skin.hands ||= handsFrom(skins, skin)
    const accessories = []
    for (const sub of subfolders(path.join(folder, 'accessories'))) {
      const meta = readJson(path.join(folder, 'accessories', sub.name, 'acc.json'), null)
      if (!meta) continue
      try {
        accessories.push({
          id: sub.name,
          label: String(meta.label || sub.name),
          rendering: meta.rendering === 'smooth' ? 'smooth' : 'pixel',
          width: Number(meta.width) || 0.3,
          dx: Number(meta.dx) || 0,
          dy: Number(meta.dy) || 0,
          src: dataUrl(path.join(folder, 'accessories', sub.name, 'item.png')),
          hands: optionalDataUrl(path.join(folder, 'accessories', sub.name, 'hands.png')),
        })
      } catch {
        log.warn(`pet accessory ${sub.name}: missing item.png`)
      }
    }
    companions.set(base?.name || skins[0].name || entry.name, { skins, accessories })
  }
  // Accessories last: one for anyone goes to every pet, plugin pets included.
  const last = (item) => (item.kind === 'accessory' ? 1 : 0)
  for (const item of [...extras].sort((x, y) => last(x) - last(y))) addPluginItem(companions, item)
  return companions
}

/** Her hands for a skin that has none: those of her first skin drawn in the same style (pixel hands on a
 * smooth skin would look wrong), else null (the overlay draws them). */
function handsFrom(skins, skin) {
  return skins.find((s) => s !== skin && s.hands && s.rendering === skin.rendering)?.hands || null
}

/** One pet, skin or accessory from a plugin (pet-plugins.js) into the packs. `onlyFor`: the bot ids that may
 * use it, when the plugin is not for every companion. */
function addPluginItem(companions, item) {
  const onlyFor = item.bots
  if (item.kind === 'accessory') {
    const meta = readJson(path.join(item.folder, 'acc.json'), null)
    if (!meta) return
    let src
    try {
      src = dataUrl(path.join(item.folder, 'item.png'))
    } catch {
      return
    }
    const accessory = {
      id: item.id,
      label: String(meta.label || item.label),
      rendering: meta.rendering === 'smooth' ? 'smooth' : 'pixel',
      width: Number(meta.width) || 0.3,
      dx: Number(meta.dx) || 0,
      dy: Number(meta.dy) || 0,
      src,
      hands: optionalDataUrl(path.join(item.folder, 'hands.png')),
      onlyFor,
    }
    for (const [name, pet] of companions) if (!item.for || item.for === name) pet.accessories.push(accessory)
    return
  }
  const skin = loadSkin(item.folder, item.id, item.label)
  if (!skin) return
  skin.onlyFor = onlyFor
  const owner = item.for || skin.name
  const pet = companions.get(owner)
  if (pet) {
    if (!Object.keys(skin.lines).length) skin.lines = pet.skins[0]?.lines || {}
    skin.hands ||= handsFrom(pet.skins, skin)
    pet.skins.push(skin)
  } else if (item.kind === 'pet' && owner) {
    companions.set(owner, { skins: [skin], accessories: [] })
  }
}

/** A pet's right-click menu (a Menu template): what is most often wanted, without going to the tray. */
function petMenu({ name, docked, onTop, skins, accessories, screens = [], do: act }) {
  return [
    { label: name, enabled: false },
    { type: 'separator' },
    ...(docked ? [{ label: '出来吧', click: () => act.command('come-out') }] : []),
    { label: '跟她说句话', click: () => act.command('talk') },
    { label: '打开和她的聊天', click: () => act.openChat() },
    ...(docked
      ? []
      : [
          { label: '戳她一下', click: () => act.command('poke') },
          { label: '藏到屏幕边上', click: () => act.command('dock') },
        ]),
    { type: 'separator' },
    ...(skins.length > 1
      ? [
          {
            label: '皮肤',
            submenu: skins.map((s) => ({
              label: s.label,
              type: 'radio',
              checked: s.chosen,
              click: () => act.setSkin(s.id),
            })),
          },
        ]
      : []),
    ...(accessories.length
      ? [
          {
            label: '配饰',
            submenu: accessories.map((a) => ({
              label: a.label,
              type: 'checkbox',
              checked: a.on,
              click: (item) => act.setAccessory(a.id, item.checked),
            })),
          },
        ]
      : []),
    ...(screens.length
      ? [{ label: '搬到另一块屏', submenu: screens.map((s) => ({ label: s.label, click: () => act.moveTo(s.key) })) }]
      : []),
    { label: '总在最前', type: 'checkbox', checked: onTop, click: (item) => act.setOnTop(item.checked) },
    { type: 'separator' },
    { label: `收起${name}（托盘「桌宠」里能放回来）`, click: () => act.putAway() },
    { label: '全部收起来', click: () => act.hideAll() },
  ]
}

const usable = (thing, botId) => !thing.onlyFor || thing.onlyFor.includes(botId)

class Pets {
  constructor({ config, nest, petsDir, stateFile, openChat, onChange = () => {}, onMessage = () => {} }) {
    this.config = config
    this.nest = nest
    this.petsDir = petsDir
    this.stateFile = stateFile
    this.openChat = openChat
    this.onChange = onChange // the tray redraws its 桌宠 menu
    this.onMessage = onMessage // every "message" event, pets or not (the tray's unread count)
    this.overlays = new Map() // key -> { key, win, area, loaded }
    this.full = false // a full-screen app is in front: the pets step aside
    this.fullscreen = new FullscreenWatch((full) => this.setFull(full))
    this.groups = new Map() // bot id -> her channels in 桌面群 (what she says there shows on her)
    this.tasks = new Map() // bot id -> ids of her tasks going on
    this.asking = new Map() // bot id -> ids of the steps that wait for the user's nod
    this.packs = new Map()
    this.companions = [] // from /api/desk
    this.saved = readJson(stateFile, {}) || {}
    this.saved.off = this.saved.off || {}
    this.saved.positions = this.saved.positions || {}
    this.saved.skins = this.saved.skins || {} // bot id -> skin id
    this.saved.accessories = this.saved.accessories || {} // bot id -> accessory ids worn
    this.saved.placeholders = this.saved.placeholders || {} // bot id -> shown as a little figure nest draws
    this.saved.screens = this.saved.screens || {} // bot id -> display id, with mixed scales
    if (typeof this.saved.stepAside !== 'boolean') this.saved.stepAside = true // 全屏时自动藏起来
    if (typeof this.saved.onTop !== 'boolean') this.saved.onTop = true // 总在最前
    this.hidden = Boolean(this.saved.hidden)
    this.tried = {} // bot id -> when nest was last asked for a line of hers
    this.stream = new EventStream({ config, nest, onEvent: (kind, data) => this.event(kind, data) })
    this.timers = []
    this.refit = () => this.fit()
    screen.on('display-metrics-changed', this.refit)
    screen.on('display-added', this.refit)
    screen.on('display-removed', this.refit)
    this.ipc()
  }

  start() {
    fs.mkdirSync(this.petsDir, { recursive: true })
    this.packs = loadPacks(this.petsDir, pluginItems(this.petsDir))
    log.info(`pets: ${this.packs.size} companion(s) with a pet in ${this.petsDir}`)
    this.refresh()
    this.syncPlugins()
    this.stream.start()
    this.timers.push(setInterval(() => this.refresh(), REFRESH_MS))
    this.timers.push(setInterval(() => this.sendIdle(), IDLE_MS))
    this.timers.push(setInterval(() => this.chatter(), CHATTER_MS))
  }

  stop() {
    this.stream.stop()
    this.timers.forEach(clearInterval)
    this.timers = []
    this.fullscreen.stop()
    for (const { win } of this.overlays.values()) if (!win.isDestroyed()) win.destroy()
    this.overlays.clear()
    screen.removeListener('display-metrics-changed', this.refit)
    screen.removeListener('display-added', this.refit)
    screen.removeListener('display-removed', this.refit)
  }

  /** The overlay windows there are. */
  live() {
    return [...this.overlays.values()].filter((o) => !o.win.isDestroyed())
  }

  send(channel, value) {
    for (const o of this.live()) o.win.webContents.send(channel, value)
  }

  /** A full-screen app came to the front, or went. */
  setFull(full) {
    this.full = full
    log.info(`pets: ${full ? 'a full-screen app is in front, stepping aside' : 'back'}`)
    this.render()
  }

  /** 全屏时自动藏起来. */
  setStepAside(on) {
    this.saved.stepAside = Boolean(on)
    this.save()
    if (!on) this.fullscreen.stop()
    this.render()
  }

  save() {
    try {
      fs.writeFileSync(this.stateFile, JSON.stringify({ ...this.saved, hidden: this.hidden }, null, 2), 'utf8')
    } catch (err) {
      log.warn('could not save pets state:', err.message)
    }
  }

  /** Her skin now: the chosen one if it still exists, else the first. */
  skinOf(botId, pet) {
    return pet.skins.find((s) => s.id === this.saved.skins[botId]) || pet.skins[0]
  }

  /** The accessories she wears, in her skin's style. Worn by label: the same crown drawn in another
   * style is the same crown. */
  wornBy(botId, pet, skin) {
    const worn = new Set(this.saved.accessories[botId] || [])
    return pet.accessories.filter((a) => worn.has(a.label) && a.rendering === skin.rendering)
  }

  /** Companions that have a pet, with whether they are out, their skins and accessories. For the tray. */
  menuItems() {
    return this.companions
      .filter((c) => c.channel_id !== null)
      .map((c) => {
        const pet = this.petFor(c)
        // No pictures of hers: the little figure nest draws, when the user wants it.
        if (!pet) return { botId: c.bot_id, name: c.name, on: Boolean(this.saved.placeholders[c.bot_id]), placeholder: true, skins: [], accessories: [] }
        const skin = this.skinOf(c.bot_id, pet)
        const worn = new Set(this.saved.accessories[c.bot_id] || [])
        return {
          botId: c.bot_id,
          name: c.name,
          on: !this.saved.off[c.bot_id],
          skins: pet.skins.map((s) => ({ id: s.id, label: s.label, chosen: s.id === skin.id })),
          // Only what can be worn with this skin's style.
          accessories: pet.accessories
            .filter((a) => a.rendering === skin.rendering)
            .map((a) => ({ id: a.label, label: a.label, on: worn.has(a.label), fits: true })),
        }
      })
  }

  setSkin(botId, skinId) {
    this.saved.skins[botId] = skinId
    this.save()
    this.render()
  }

  setAccessory(botId, label, on) {
    const accessoryId = label
    const worn = new Set(this.saved.accessories[botId] || [])
    if (on) worn.add(accessoryId)
    else worn.delete(accessoryId)
    this.saved.accessories[botId] = [...worn]
    this.save()
    this.render()
  }

  setOn(botId, on) {
    const c = this.companions.find((x) => x.bot_id === botId)
    if (c && !this.petFor(c)) {
      if (on) this.saved.placeholders[botId] = true
      else delete this.saved.placeholders[botId]
      this.save()
      this.render()
      return
    }
    if (on) delete this.saved.off[botId]
    else this.saved.off[botId] = true
    this.save()
    this.render()
  }

  /** 总在最前: the pets float over every window, or sit among them like any window. */
  setOnTop(on) {
    this.saved.onTop = Boolean(on)
    this.save()
    for (const o of this.live()) this.applyOnTop(o.win)
    this.onChange()
  }

  applyOnTop(win) {
    // Not 'floating' (nor alwaysOnTop: true, which means it): on Windows Electron puts those levels just
    // behind the taskbar, and where the taskbar is not itself topmost the window ends up not on top at all.
    if (this.saved.onTop) win.setAlwaysOnTop(true, 'pop-up-menu')
    else win.setAlwaysOnTop(false)
  }

  /** 安静一会儿: no lines of their own until then (0: talk again). Reminders still come. */
  quietFor(ms) {
    this.saved.quietUntil = ms ? Date.now() + ms : 0
    this.save()
    this.onChange()
  }

  quiet() {
    return (this.saved.quietUntil || 0) > Date.now()
  }

  async chatter() {
    if (this.hidden || this.quiet()) return
    const idle = powerMonitor.getSystemIdleTime()
    if (idle < PAUSE_FROM || idle > PAUSE_UNTIL || NIGHT(new Date().getHours())) return
    const now = Date.now()
    const ready = this.out().filter((c) => c.mode === 'live' && now - (this.tried[c.bot_id] || 0) > TRY_GAP_MS)
    if (!ready.length || Math.random() > TRY_CHANCE) return
    const pick = ready[Math.floor(Math.random() * ready.length)]
    this.tried[pick.bot_id] = now
    try {
      const res = await this.nest.api('POST', `/api/desk/${encodeURIComponent(pick.bot_id)}/chatter`)
      if (res.status === 200) log.info(`chatter ${pick.name}: ${res.json?.said ? 'said something' : res.json?.reason}`)
    } catch {
      // nest away or not signed in: the next moment will do
    }
  }

  setHidden(hidden) {
    this.hidden = Boolean(hidden)
    this.save()
    this.render()
  }

  reloadPacks() {
    this.packs = loadPacks(this.petsDir, pluginItems(this.petsDir))
    this.render()
  }

  /** Pets from plugins installed in nest: fetch what is new, then load everything again. */
  async syncPlugins() {
    try {
      const plugins = await syncPetPlugins(this.nest, this.petsDir)
      this.packs = loadPacks(this.petsDir, pluginItems(this.petsDir, plugins))
      this.render()
    } catch (err) {
      log.warn('pet plugins:', err.message)
    }
  }

  /** Her pet as she may have it: the skins and accessories from plugins not for her left out. */
  petFor(c) {
    const pet = this.packs.get(c.name)
    if (!pet) return null
    const skins = pet.skins.filter((s) => usable(s, c.bot_id))
    return skins.length ? { skins, accessories: pet.accessories.filter((a) => usable(a, c.bot_id)) } : null
  }

  /** The pet of one of them as it stands in the 小窝 (the actions shown there, and what she says when poked). */
  spriteFor(botId) {
    const companion = this.companions.find((c) => c.bot_id === botId)
    const pet = companion && this.petFor(companion)
    if (!pet) return null
    const skin = this.skinOf(botId, pet)
    const actions = {}
    for (const name of HOME_ACTIONS) {
      const a = skin.actions[name]
      if (a) actions[name] = { src: a.src, frames: Number(a.frames) || 1, fps: Number(a.fps) || 6, loop: a.loop !== false }
    }
    return {
      rendering: skin.rendering,
      frame: skin.frame,
      baseline: skin.baseline,
      figure: skin.figure,
      actions,
      poked: skin.lines?.poke ?? [],
    }
  }

  /** Soon after signing in (remote mode), the companions are not known yet. */
  refreshIfEmpty() {
    if (!this.companions.length) this.refresh()
  }

  async refresh() {
    try {
      const res = await this.nest.api('GET', '/api/desk')
      if (res.status !== 200 || !Array.isArray(res.json)) return
      this.companions = res.json
      await this.readMore()
      this.render()
    } catch {
      // not signed in yet, or nest is away: try again at the next refresh
    }
  }

  /** Her channels in the 桌面群, and what they are working on: for what shows on the pets. */
  async readMore() {
    const groups = await this.nest.api('GET', '/api/desk/groups').catch(() => null)
    if (groups?.status === 200 && Array.isArray(groups.json)) {
      this.groups = new Map()
      for (const group of groups.json) {
        for (const m of group.members || []) {
          this.groups.set(m.bot_id, [...(this.groups.get(m.bot_id) || []), m.channel_id])
        }
      }
    }
    const tasks = await this.nest.api('GET', '/api/tasks').catch(() => null)
    if (tasks?.status === 200 && Array.isArray(tasks.json?.tasks)) {
      this.tasks = new Map()
      for (const t of tasks.json.tasks) if (GOING.has(t.status)) idsOf(this.tasks, t.bot_id).add(t.id)
    }
    const asked = await this.nest.api('GET', '/api/approvals').catch(() => null)
    if (asked?.status === 200 && Array.isArray(asked.json?.approvals)) {
      this.asking = new Map()
      for (const a of asked.json.approvals) idsOf(this.asking, a.bot_id).add(a.id)
    }
  }

  /** What she is up to, for the overlay: working on something, waiting for a nod. */
  workOf(botId) {
    return { working: idsOf(this.tasks, botId).size, asking: idsOf(this.asking, botId).size }
  }

  out() {
    if (this.hidden) return []
    return this.companions.filter((c) => {
      if (c.channel_id === null) return false
      return this.petFor(c) ? !this.saved.off[c.bot_id] : Boolean(this.saved.placeholders[c.bot_id])
    })
  }

  /** The overlay a pet is in: the one for all the screens, or her own screen's (mixed scales). */
  areaOf(botId, areas) {
    return areas.find((a) => a.key === this.saved.screens[botId]) || areas[0]
  }

  render() {
    this.onChange()
    const out = this.out()
    if (out.length && this.saved.stepAside) this.fullscreen.start()
    else if (!out.length) this.fullscreen.stop()
    const areas = overlayAreas(screen.getAllDisplays(), screen.getPrimaryDisplay())
    for (const o of this.live()) {
      // A screen that is gone (or all of one scale now): its window goes.
      if (!areas.some((area) => area.key === o.key)) {
        o.win.destroy()
        this.overlays.delete(o.key)
      }
    }
    const aside = this.full && this.saved.stepAside
    for (const area of areas) {
      const here = aside ? [] : out.filter((c) => this.areaOf(c.bot_id, areas) === area)
      const known = this.overlays.get(area.key)
      if (!here.length) {
        if (known && !known.win.isDestroyed()) known.win.hide()
        continue
      }
      const overlay = this.ensure(area)
      const state = { pets: here.map((c) => this.petState(c, area.key)), screens: overlay.area.screens }
      const send = () => {
        if (overlay.win.isDestroyed()) return
        overlay.win.webContents.send('pet:state', state)
        this.sendIdle()
        if (!overlay.win.isVisible()) {
          overlay.win.showInactive()
          this.applyOnTop(overlay.win) // a full-screen app was above: above it again
        }
      }
      if (overlay.loaded) send()
      else overlay.win.webContents.once('did-finish-load', send)
    }
  }

  petState(c, key) {
    const pet = this.petFor(c)
    const skin = pet ? this.skinOf(c.bot_id, pet) : null
    const place = this.saved.positions[c.bot_id]
    return {
      botId: c.bot_id,
      name: c.name,
      live: c.mode === 'live',
      chatId: c.channel_id,
      channels: c.chats.map((chat) => chat.channel_id),
      groups: this.groups.get(c.bot_id) || [],
      work: this.workOf(c.bot_id),
      skin: skin ? skin.id : 'placeholder',
      pack: skin,
      accessories: pet ? this.wornBy(c.bot_id, pet, skin) : [],
      // A place on another screen's overlay (or on all of them) says nothing about this one.
      position: place && (place.screen || 'all') === key ? place : null,
    }
  }

  ensure(area) {
    const known = this.overlays.get(area.key)
    if (known && !known.win.isDestroyed()) return known
    const overlay = { key: area.key, area, loaded: false, win: null }
    const win = new BrowserWindow({
      ...area.bounds,
      transparent: true,
      frame: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      hasShadow: false,
      show: false,
      backgroundColor: '#00000000',
      webPreferences: {
        preload: path.join(__dirname, 'pet-preload.js'),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        spellcheck: false,
        backgroundThrottling: false,
      },
    })
    overlay.win = win
    this.applyOnTop(win)
    win.setIgnoreMouseEvents(true, { forward: true })
    win.webContents.on('will-navigate', (event) => event.preventDefault())
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    win.webContents.on('did-finish-load', () => {
      overlay.loaded = true
    })
    win.on('closed', () => {
      if (this.overlays.get(area.key) === overlay) this.overlays.delete(area.key)
    })
    win.loadFile(PAGE)
    this.overlays.set(area.key, overlay)
    return overlay
  }

  /** The screens changed (the taskbar moved, a display added, another scale): follow them. */
  fit() {
    const areas = overlayAreas(screen.getAllDisplays(), screen.getPrimaryDisplay())
    for (const o of this.live()) {
      const area = areas.find((x) => x.key === o.key)
      if (!area) continue // render() lets it go
      o.area = area
      o.win.setBounds(area.bounds)
      o.win.webContents.send('pet:screens', area.screens)
    }
    this.render()
  }

  sendIdle() {
    this.send('pet:idle', { seconds: powerMonitor.getSystemIdleTime(), hour: new Date().getHours() })
  }

  event(kind, data) {
    if (kind === 'message') this.onMessage(data)
    if (kind === 'task' || kind === 'approval') this.onWork(kind, data)
    if (kind === 'bots' && typeof data?.was === 'string' && typeof data.bot_id === 'string') this.moved(data.was, data.bot_id)
    if (!this.live().length) return
    if (kind === 'message' && data && typeof data.channel_id === 'number') {
      this.send('pet:event', {
        kind: 'message',
        channel: data.channel_id,
        mine: Boolean(data.from_self),
        text: String(data.text || ''),
      })
    } else if (kind === 'decision' && data && typeof data.channel_id === 'number') {
      this.send('pet:event', { kind: 'decision', channel: data.channel_id, outcome: String(data.outcome || '') })
    } else if (kind === 'reminder' && data && typeof data.bot_id === 'string') {
      this.send('pet:event', { kind: 'reminder', bot: data.bot_id })
    } else if (kind === 'bots') {
      this.refresh()
    } else if (kind === 'plugins') {
      this.syncPlugins()
    }
  }

  /** She moved to a platform account (接上平台账号): what is kept under her old id is hers under the new one. */
  moved(was, now) {
    for (const kept of ['off', 'positions', 'skins', 'accessories', 'placeholders', 'screens']) {
      if (!Object.hasOwn(this.saved[kept], was)) continue
      this.saved[kept][now] = this.saved[kept][was]
      delete this.saved[kept][was]
    }
    this.save()
  }

  /** Her tasks and the steps that wait for a nod, as they change: it shows on her. */
  onWork(kind, data) {
    if (!data || typeof data.bot_id !== 'string') return
    const ids = idsOf(kind === 'task' ? this.tasks : this.asking, data.bot_id)
    const going = kind === 'task' ? GOING.has(data.status) : data.status === 'pending'
    const was = ids.has(data.id)
    if (going) ids.add(data.id)
    else ids.delete(data.id)
    const ended = kind === 'task' && was && !going ? String(data.status) : ''
    this.send('pet:event', { kind: 'work', bot: data.bot_id, ...this.workOf(data.bot_id), ended })
  }

  /** Tell the overlay to do something with one pet (from her right-click menu). */
  command(botId, kind) {
    this.send('pet:command', { botId, kind })
  }

  /** 「搬到另一块屏」 (mixed scales): her new screen, where she starts at the ground. */
  moveTo(botId, key) {
    this.saved.screens[botId] = key
    delete this.saved.positions[botId]
    this.save()
    this.render()
  }

  /** The other screens she could live on ([] when one overlay covers them all). */
  otherScreens(botId) {
    const areas = overlayAreas(screen.getAllDisplays(), screen.getPrimaryDisplay())
    if (areas.length < 2) return []
    const here = this.areaOf(botId, areas)
    return areas
      .map((area, n) => ({ key: area.key, label: n === 0 ? '主屏' : area.label || `第 ${n + 1} 块屏` }))
      .filter((s) => s.key !== here.key)
  }

  /** Her right-click menu: the things most often wanted, without going to the tray. */
  popupMenu(botId, docked, win) {
    const c = this.companions.find((x) => x.bot_id === botId)
    if (!c || !win || win.isDestroyed()) return
    const pet = this.petFor(c) || { skins: [], accessories: [] }
    Menu.buildFromTemplate(petMenu({
      name: c.name,
      docked,
      onTop: this.saved.onTop,
      skins: pet.skins.map((s) => ({ id: s.id, label: s.label, chosen: s.id === this.skinOf(botId, pet).id })),
      screens: this.otherScreens(botId),
      accessories: this.menuItems().find((m) => m.botId === botId)?.accessories || [],
      do: {
        command: (kind) => this.command(botId, kind),
        openChat: () => this.openChat(botId),
        setSkin: (id) => this.setSkin(botId, id),
        setAccessory: (label, on) => this.setAccessory(botId, label, on),
        setOnTop: (on) => this.setOnTop(on),
        moveTo: (key) => this.moveTo(botId, key),
        putAway: () => this.setOn(botId, false),
        hideAll: () => this.setHidden(true),
      },
    })).popup({ window: win })
  }

  /** The overlay an IPC call came from, or null. */
  fromPet(event) {
    return this.live().find((o) => event.sender === o.win.webContents) || null
  }

  ipc() {
    const on = (channel, fn) =>
      ipcMain.handle(channel, (event, ...args) => {
        const from = this.fromPet(event)
        if (!from) throw new Error('not allowed')
        return fn(from, ...args)
      })
    on('pet:interactive', (from, yes) => {
      from.win.setIgnoreMouseEvents(!yes, { forward: true })
      return true
    })
    on('pet:pointer', (from) => {
      const at = screen.getCursorScreenPoint()
      const area = from.win.getBounds()
      const x = at.x - area.x
      const y = at.y - area.y
      return x >= 0 && y >= 0 && x < area.width && y < area.height ? { x, y } : { x: -1, y: -1 }
    })
    on('pet:focus', (from) => {
      from.win.focus()
      return true
    })
    on('pet:say', async (_from, botId, text) => {
      const words = String(text || '').trim().slice(0, SAY_MAX)
      if (!words) return { ok: false, error: '说点什么吧' }
      try {
        const res = await this.nest.api('POST', `/api/desk/${encodeURIComponent(String(botId))}/messages`, { text: words })
        return res.status === 202 ? { ok: true } : { ok: false, error: detailOf(res) }
      } catch (err) {
        return { ok: false, error: err.message || String(err) }
      }
    })
    on('pet:open-chat', (_from, botId) => {
      this.openChat(String(botId || ''))
      return true
    })
    on('pet:position', (from, botId, position) => {
      const x = Number(position?.x)
      const y = Number(position?.y)
      if (!Number.isFinite(x) || !Number.isFinite(y)) return false
      const place = { x: Math.round(x), y: Math.round(y), screen: from.key }
      // Hidden at a screen edge: she is there again after a restart.
      if (DOCK_SIDES.has(position?.dock)) place.dock = position.dock
      this.saved.positions[String(botId)] = place
      this.save()
      return true
    })
    on('pet:menu', (from, botId, docked) => {
      this.popupMenu(String(botId || ''), Boolean(docked), from.win)
      return true
    })
  }
}

module.exports = { Pets, loadPacks, addPluginItem, overlayArea, overlayAreas, petMenu }
