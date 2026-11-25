import * as THREE from 'three'

import type { PetSprite } from './desktop.ts'
import {
  canStand,
  CELL,
  cornerOf,
  FURNITURE,
  furnitureOf,
  middle,
  placesOf,
  roomFor,
  route,
  sizeOf,
  TALL,
  type Activity,
  type Cell,
  type Furnished,
  type Kind,
  type Piece,
  type Place,
  type RoomPlan,
  type Step,
  type Turn,
} from './dorm.ts'
import { furniture } from './dorm-furniture.ts'
import {
  canHang,
  hangingOf,
  HANGINGS,
  lengthOf,
  spotFor,
  type HangKind,
  type Hung,
  type Look,
  type Wall,
} from './dorm-look.ts'
import { hanging } from './dorm-paint.ts'
import { shell, skyTexture, WALL_H, type Shell } from './dorm-room.ts'
import { box, turned } from './dorm-shapes.ts'
import { seatOn, wanderOn, type Daylight, type Pose } from './home-stage.ts'

/** 3D 小窝: draws a room of lib/dorm.ts with the companions in it as paper figures (their
 * desktop pets' pictures, standing up and turned to the camera). Draws only while it can be seen. */

export type Guest = {
  id: string
  name: string
  pose: Pose
  /** What she is at. */
  for: Activity
  asleep: boolean
  sprite: PetSprite | null
}

/** What the page is told. `draw`: where her head is on the picture, in CSS pixels, each time it is drawn (NaN: she
 * is not there); `pick`: someone was clicked; `furnished`: the user changed the room; `chosen`: a piece, or
 * something on the wall, was taken up to be moved, or nothing is. */
export type Listener = {
  draw: (id: string, x: number, y: number) => void
  pick: (id: string) => void
  furnished: (room: Furnished) => void
  chosen: (thing: Piece | Hung | null) => void
}

/** Quarter metres, in which things hang. */
const Q = 0.25

const isHung = (thing: Piece | Hung): thing is Hung => 'wall' in thing

const SPEED = 0.85 // metres a second
const FRAME_MS = 1000 / 31
const WANDER_MS = [9_000, 22_000]
const ACT_MS = [3_500, 8_000]
const POKE_MS = 2200
const ELEVATION = THREE.MathUtils.degToRad(27)
// What gives light, and how high it is.
const LAMPS: Partial<Record<Kind, number>> = { lamp: 1.62, lantern: 0.5, nightstand: 0.85, desk: 1.05 }
const TURN = { home: Math.PI / 4, least: Math.PI / 4 - 0.5, most: Math.PI / 4 + 0.5 }
const ACTS: [string, number][] = [
  ['blink', 260],
  ['blink', 260],
  ['blink', 260],
  ['smile', 1800],
  ['think', 2400],
  ['stretch', 1400],
  ['yawn', 1400],
]

const LIGHTS: Record<Daylight, { sun: string; power: number; sky: string; ground: string; fill: number; tint: string; patch: string; patchOn: number; lamp: boolean }> = {
  morning: { sun: '#fff0d2', power: 1.5, sky: '#fff4e4', ground: '#e2cbb2', fill: 1.75, tint: '#fffaf4', patch: '#fff0c4', patchOn: 0.34, lamp: false },
  day: { sun: '#ffffff', power: 1.5, sky: '#f4f8ff', ground: '#e4d8ca', fill: 1.9, tint: '#ffffff', patch: '#fffbe8', patchOn: 0.26, lamp: false },
  evening: { sun: '#ffb98a', power: 1.25, sky: '#ffdcc6', ground: '#c5a49a', fill: 1.35, tint: '#ffeadb', patch: '#ffab73', patchOn: 0.34, lamp: true },
  night: { sun: '#98a8ee', power: 0.6, sky: '#a3abdf', ground: '#6a658c', fill: 1.0, tint: '#dcdcf2', patch: '#aab8ff', patchOn: 0.12, lamp: true },
}

const still = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
const between = ([least, most]: number[]) => least + Math.random() * (most - least)

type Action = { texture: THREE.Texture; frames: number; fps: number; loop: boolean }

type Figure = {
  guest: Guest
  props: THREE.Group
  holds: Activity
  root: THREE.Group
  card: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>
  hit: THREE.Mesh
  actions: Map<string, Action>
  /** Her place, or the one she is on her way to; null: she is leaving. */
  place: Place | null
  /** Where she set out from, for the next way. */
  came: { at: Cell; from: Cell; y: number }
  steps: Step[]
  action: string
  since: number
  act: string
  actUntil: number
  nextAct: number
  poked: number
  back: boolean
}

export class DormScene {
  private readonly renderer: THREE.WebGLRenderer
  private readonly scene = new THREE.Scene()
  private readonly camera = new THREE.OrthographicCamera()
  private readonly sun = new THREE.DirectionalLight()
  private readonly fill = new THREE.HemisphereLight()
  private readonly bulbs = [0, 1, 2].map(() => new THREE.PointLight('#ffc877', 0, 6.5, 1))
  private readonly glow = new THREE.PointLight('#9fc4ff', 0, 3.2, 1.6)
  private readonly figures = new Map<string, Figure>()
  private readonly loader = new THREE.TextureLoader()
  private readonly watch: ResizeObserver
  private readonly seen: IntersectionObserver
  private readonly off: (() => void)[] = []
  private readonly grid = new THREE.Group()
  private readonly mark = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.4, depthWrite: false }),
  )
  private readonly canvas: HTMLCanvasElement
  private readonly on: Listener
  private room: RoomPlan
  private built: Shell | null = null
  /** One for each piece of the room. */
  private models: THREE.Group[] = []
  /** One for each thing on the walls. */
  private hung: THREE.Group[] = []
  private places: Place[] = []
  private turn = TURN.home
  private zoom = 1
  private visible = true
  private light: Daylight = 'day'
  private lampOn = false
  private lampSet: boolean | null = null // by a click, until the light changes
  private tvOn = false
  private tvSet: boolean | null = null
  private settled = false
  private editing = false
  private chosen: Piece | Hung | null = null
  private nextWander = 0
  private last = 0
  private raf = 0

  constructor(canvas: HTMLCanvasElement, room: RoomPlan, on: Listener) {
    this.canvas = canvas
    this.on = on
    this.room = room
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' })
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio))
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFShadowMap
    // Only furniture throws shadows here: they are drawn anew when it moves.
    this.renderer.shadowMap.autoUpdate = false
    this.sun.castShadow = true
    this.sun.shadow.mapSize.set(2048, 2048)
    this.sun.shadow.radius = 3
    this.sun.shadow.bias = -0.0004
    this.sun.shadow.normalBias = 0.03
    Object.assign(this.sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 22 })
    this.mark.visible = false
    this.scene.add(this.sun, this.sun.target, this.fill, this.glow, ...this.bulbs, this.grid, this.mark)
    this.enter(room)

    this.watch = new ResizeObserver(() => this.resize())
    this.watch.observe(canvas)
    this.seen = new IntersectionObserver((entries) => (this.visible = entries.at(-1)!.isIntersecting))
    this.seen.observe(canvas)
    this.resize()
    this.listen()
    this.raf = requestAnimationFrame(this.tick)
  }

  // -- the room ------------------------------------------------------------------------------------------------

  /** Another room, or the same with its furniture as it is now kept. */
  enter(room: RoomPlan): void {
    for (const figure of this.figures.values()) this.remove(figure)
    if (this.built) this.drop(this.built.group)
    this.room = room
    this.settled = false
    this.lampSet = this.tvSet = null
    this.choose(null)
    this.built = shell(room)
    this.scene.add(this.built.group)
    const [w, d] = [room.w * CELL, room.d * CELL]
    this.sun.position.set(w / 2 + 5, 9, d / 2 + 2.6)
    this.sun.target.position.set(w / 2, 0, d / 2)
    this.drop(this.grid, true)
    const points: THREE.Vector3[] = []
    for (let x = 0; x <= room.w; x++) points.push(new THREE.Vector3(x * CELL, 0, 0), new THREE.Vector3(x * CELL, 0, d))
    for (let z = 0; z <= room.d; z++) points.push(new THREE.Vector3(0, 0, z * CELL), new THREE.Vector3(w, 0, z * CELL))
    const lines = new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color: '#6a5a86', transparent: true, opacity: 0.3 }),
    )
    lines.position.y = 0.045
    this.grid.add(lines)
    this.grid.visible = this.editing
    this.furnish(room)
    this.setLight(this.light)
    this.look()
  }

  private furnish({ pieces, look }: Furnished): void {
    for (const model of [...this.models, ...this.hung]) this.drop(model)
    this.room = { ...this.room, pieces, look }
    this.built?.dress(look)
    const all = <T>(things: T[], make: (thing: T) => THREE.Group) =>
      things.map((thing) => {
        const model = make(thing)
        this.scene.add(model)
        return model
      })
    this.models = all(pieces, (piece) => furniture(piece.kind))
    this.hung = all(look.hung, (hung) => hanging(hung.kind))
    this.arrange()
  }

  /** Everything where the room says it is. */
  private arrange(): void {
    for (const [n, piece] of this.room.pieces.entries()) {
      const corner = cornerOf(piece)
      this.models[n].position.set(corner.x * CELL, 0, corner.z * CELL)
      this.models[n].rotation.y = (piece.turn * Math.PI) / 2
    }
    for (const [n, hung] of this.room.look.hung.entries()) this.pin(this.hung[n], hung)
    this.places = placesOf(this.room)
    this.renderer.shadowMap.needsUpdate = true
    this.switches()
  }

  private pin(model: THREE.Group, hung: Hung, off = 0.005): void {
    const { w } = hangingOf(hung.kind)
    const left = hung.wall === 'left'
    model.rotation.y = left ? Math.PI / 2 : 0
    if (left) model.position.set(off, hung.y * Q, (hung.at + w) * Q)
    else model.position.set(hung.at * Q, hung.y * Q, off)
  }

  private drop(thing: THREE.Object3D, inside = false): void {
    thing.traverse((part) => {
      if (part instanceof THREE.Sprite) part.material.dispose()
      if (!(part instanceof THREE.Mesh || part instanceof THREE.LineSegments)) return
      part.geometry.dispose()
      for (const material of [part.material].flat() as THREE.MeshBasicMaterial[]) {
        material.map?.dispose()
        material.dispose()
      }
    })
    if (inside) thing.clear()
    else thing.removeFromParent()
  }

  // -- furnishing ----------------------------------------------------------------------------------------------

  /** While the user furnishes the room, nobody is in it. */
  setEditing(on: boolean): void {
    this.editing = on
    this.grid.visible = on
    if (on) for (const figure of this.figures.values()) this.remove(figure)
    this.settled = this.settled && !on
    this.choose(null)
  }

  setFurnished(room: Furnished): void {
    this.choose(null)
    this.furnish(room)
  }

  /** Other paper or another floor. */
  decorate(look: Partial<Pick<Look, 'wall' | 'floor'>>): void {
    this.room = { ...this.room, look: { ...this.room.look, ...look } }
    this.built?.dress(this.room.look)
    this.tell()
  }

  private tell(): void {
    this.on.furnished({ pieces: this.room.pieces, look: this.room.look })
  }

  /** A new piece, where there is room; false: there is none. */
  add(kind: Kind): boolean {
    const piece = roomFor(this.room, kind)
    if (!piece) return false
    this.furnish({ ...this.room, pieces: [...this.room.pieces, piece] })
    this.choose(piece)
    this.tell()
    return true
  }

  /** Something new on a wall. */
  hang(kind: HangKind): boolean {
    const { look } = this.room
    const hung = spotFor(this.room, look.hung, kind)
    if (!hung) return false
    this.furnish({ ...this.room, look: { ...look, hung: [...look.hung, hung] } })
    this.choose(hung)
    this.tell()
    return true
  }

  stow(): void {
    const { chosen } = this
    if (!chosen) return
    this.choose(null)
    const { pieces, look } = this.room
    this.furnish({
      pieces: pieces.filter((piece) => piece !== chosen),
      look: { ...look, hung: look.hung.filter((hung) => hung !== chosen) },
    })
    this.tell()
  }

  /** A quarter round, about its middle; a little aside when it would stand in something. */
  rotate(): void {
    const old = this.chosen
    if (!old || isHung(old)) return
    const { w, d } = sizeOf(old)
    for (const by of [1, 2, 3]) {
      const turn = ((old.turn + by) % 4) as Turn
      const size = sizeOf({ kind: old.kind, turn })
      const [x, z] = [old.x + Math.floor((w - size.w) / 2), old.z + Math.floor((d - size.d) / 2)]
      for (let far = 0; far <= 3; far++) {
        for (let dx = -far; dx <= far; dx++) {
          for (let dz = -far; dz <= far; dz++) {
            if (Math.max(Math.abs(dx), Math.abs(dz)) !== far) continue
            const piece: Piece = { kind: old.kind, turn, x: x + dx, z: z + dz }
            if (!canStand(this.room, piece, old)) continue
            this.put(old, piece)
            this.tell()
            return
          }
        }
      }
    }
  }

  private put<T extends Piece | Hung>(old: T, thing: T): void {
    const swap = <U>(all: U[]) => all.map((other) => ((other as unknown) === old ? (thing as unknown as U) : other))
    const { pieces, look } = this.room
    this.room = { ...this.room, pieces: swap(pieces), look: { ...look, hung: swap(look.hung) } }
    if (this.chosen === old) this.chosen = thing
    this.arrange()
    this.show(thing, true)
    this.on.chosen(this.chosen)
  }

  private choose(thing: Piece | Hung | null): void {
    if (thing !== this.chosen) {
      this.chosen = thing
      this.on.chosen(thing)
    }
    this.mark.visible = Boolean(thing)
    if (thing) this.show(thing, true)
  }

  /** The floor under a piece, or the wall behind what hangs there: green, or red where it may not be. */
  private show(thing: Piece | Hung, may: boolean): void {
    this.mark.material.color.set(may ? '#5fb487' : '#e0685c')
    if (isHung(thing)) {
      const { w, h } = hangingOf(thing.kind)
      this.mark.scale.set(w * Q, h * Q, 1)
      const [at, y] = [(thing.at + w / 2) * Q, (thing.y + h / 2) * Q]
      this.mark.rotation.set(0, thing.wall === 'left' ? Math.PI / 2 : 0, 0)
      if (thing.wall === 'left') this.mark.position.set(0.03, y, at)
      else this.mark.position.set(at, y, 0.03)
      return
    }
    const { w, d } = sizeOf(thing)
    this.mark.scale.set(w * CELL, d * CELL, 1)
    this.mark.rotation.set(-Math.PI / 2, 0, 0)
    this.mark.position.set((thing.x + w / 2) * CELL, 0.05, (thing.z + d / 2) * CELL)
  }

  dispose(): void {
    cancelAnimationFrame(this.raf)
    this.watch.disconnect()
    this.seen.disconnect()
    for (const off of this.off) off()
    for (const thing of [...this.scene.children]) this.drop(thing)
    for (const figure of this.figures.values()) for (const action of figure.actions.values()) action.texture.dispose()
    this.renderer.dispose()
  }

  // -- what happens there --------------------------------------------------------------------------------------

  setLight(light: Daylight): void {
    const l = LIGHTS[light]
    if (light !== this.light) this.lampSet = null
    this.light = light
    this.sun.color.set(l.sun)
    this.sun.intensity = l.power
    this.fill.color.set(l.sky)
    this.fill.groundColor.set(l.ground)
    this.fill.intensity = l.fill
    const sky = this.built?.sky?.material as THREE.MeshBasicMaterial | undefined
    if (sky) {
      sky.map?.dispose()
      sky.map = skyTexture(light)
      sky.needsUpdate = true
    }
    const patch = this.built?.patch?.material as THREE.MeshBasicMaterial | undefined
    if (patch) {
      patch.color.set(l.patch)
      patch.opacity = l.patchOn
    }
    for (const figure of this.figures.values()) figure.card.material.color.set(l.tint)
    this.switches()
  }

  private switches(): void {
    const here = [...this.figures.values()].filter((f) => f.place)
    const watching = here.some((f) => f.guest.for === 'tv' && f.place?.pose === 'sit')
    const dark = LIGHTS[this.light].lamp
    this.lampOn = this.lampSet ?? (dark && (this.editing || here.some((f) => !f.guest.asleep)))
    this.tvOn = this.tvSet ?? watching
    const power = this.lampOn ? (this.light === 'night' ? 2.6 : 1.5) : 0
    const lamps = this.room.pieces.filter((piece) => LAMPS[piece.kind])
    for (const [n, bulb] of this.bulbs.entries()) {
      const piece = lamps[n]
      bulb.intensity = piece ? power * (piece.kind === 'lamp' ? 1 : 0.5) : 0
      if (!piece) continue
      const { w, d } = sizeOf(piece)
      // A little out of the corner it stands in: not all of it on the wall.
      const at = [piece.x + w / 2 + (piece.x < 1 ? 1 : 0), piece.z + d / 2 + (piece.z < 1 ? 1 : 0)]
      bulb.position.set(at[0] * CELL, LAMPS[piece.kind]!, at[1] * CELL)
    }
    this.glow.intensity = 0
    for (const model of [...this.models, ...this.hung]) {
      model.traverse((part) => {
        if (!(part instanceof THREE.Mesh)) return
        if (part.name === 'shade') (part.material as THREE.MeshToonMaterial).emissiveIntensity = this.lampOn ? 0.9 : 0
        if (part.name !== 'screen') return
        ;(part.material as THREE.MeshBasicMaterial).color.set(this.tvOn ? '#ffffff' : '#2a2833')
        if (!this.tvOn) return
        this.glow.intensity = 0.9
        this.glow.position.copy(model.localToWorld(new THREE.Vector3((FURNITURE.tv.w * CELL) / 2, 1, 1.2)))
      })
    }
  }

  setGuests(guests: Guest[]): void {
    if (this.editing) return
    const here = new Set(guests.map((g) => g.id))
    for (const [id, figure] of this.figures) if (!here.has(id)) this.leave(figure)
    const before: Record<string, Place> = {}
    for (const [id, figure] of this.figures) if (figure.place && here.has(id)) before[id] = figure.place
    const places = seatOn(this.places, guests, before)
    for (const guest of guests) {
      const place = places[guest.id]
      if (!place) continue // more of them than there are places
      let figure = this.figures.get(guest.id)
      if (!figure) {
        figure = this.arrive(guest, place)
      } else {
        if (figure.guest.sprite !== guest.sprite) this.dress(figure, guest)
        figure.guest = guest
      }
      this.go(figure, place)
    }
    this.settled = true
    this.switches()
  }

  poke(id: string): void {
    const figure = this.figures.get(id)
    if (figure) figure.poked = performance.now() + POKE_MS
  }

  private arrive(guest: Guest, place: Place): Figure {
    const root = new THREE.Group()
    const card = new THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ transparent: true, alphaTest: 0.2, color: LIGHTS[this.light].tint }),
    )
    const shadow = new THREE.Mesh(
      new THREE.CircleGeometry(0.3, 24).rotateX(-Math.PI / 2).scale(1, 1, 0.7),
      new THREE.MeshBasicMaterial({ color: '#5a3f4a', transparent: true, opacity: 0.22, depthWrite: false }),
    )
    shadow.position.y = 0.045
    const hit = new THREE.Mesh(new THREE.BoxGeometry(0.75, TALL, 0.4).translate(0, TALL / 2, 0))
    hit.visible = false
    hit.userData.id = guest.id
    const props = new THREE.Group()
    root.add(shadow, card, props, hit)
    this.scene.add(root)
    const inside = !this.settled || still() || guest.asleep
    const door = { at: middle(this.room.door), from: this.room.door, y: 0 }
    const came = inside ? place : door
    root.position.set(came.at.x * CELL, came.y, came.at.z * CELL)
    const now = performance.now()
    const figure: Figure = {
      guest,
      root,
      card,
      hit,
      props,
      holds: '',
      actions: new Map(),
      place: inside ? place : null,
      came,
      steps: [],
      action: '',
      since: now,
      act: '',
      actUntil: 0,
      nextAct: now + between(ACT_MS),
      poked: 0,
      back: false,
    }
    this.figures.set(guest.id, figure)
    this.dress(figure, guest)
    return figure
  }

  private leave(figure: Figure): void {
    if (!figure.place && figure.steps.length) return // on her way out already
    figure.place = null
    const door: Place = { id: 'door', pose: 'stand', at: middle(this.room.door), y: 0, from: this.room.door, for: [], piece: -1 }
    figure.steps = still() ? [{ at: door.at, y: 0 }] : route(this.room, figure.came, door)
    figure.came = door
  }

  private remove(figure: Figure): void {
    this.figures.delete(figure.guest.id)
    for (const action of figure.actions.values()) action.texture.dispose()
    this.drop(figure.root)
    this.on.draw(figure.guest.id, NaN, NaN)
  }

  private go(figure: Figure, place: Place): void {
    if (figure.place === place) return
    figure.steps = still() ? [] : route(this.room, figure.came, place)
    if (still()) figure.root.position.set(place.at.x * CELL, place.y, place.at.z * CELL)
    figure.place = place
    figure.came = place
  }

  private dress(figure: Figure, guest: Guest): void {
    for (const action of figure.actions.values()) action.texture.dispose()
    figure.actions.clear()
    const sprite = guest.sprite
    if (!sprite) {
      figure.actions.set('idle', { texture: badge(guest.name), frames: 1, fps: 1, loop: true })
      figure.card.geometry.dispose()
      figure.card.geometry = new THREE.PlaneGeometry(0.8, 0.8).translate(0, 0.55, 0)
    } else {
      for (const [name, action] of Object.entries(sprite.actions)) {
        const texture = this.loader.load(action.src)
        texture.colorSpace = THREE.SRGBColorSpace
        texture.anisotropy = 4
        if (sprite.rendering === 'pixel') texture.magFilter = THREE.NearestFilter
        const frames = Math.max(1, action.frames)
        texture.repeat.x = 1 / frames
        figure.actions.set(name, { texture, frames, fps: action.fps || 6, loop: action.loop !== false })
      }
      // As tall on the picture as in the room, though the card stands upright and is looked at from above.
      const size = (TALL / Math.cos(ELEVATION)) * (sprite.frame / sprite.figure)
      const under = ((sprite.frame - sprite.baseline) / sprite.frame) * size
      figure.card.geometry.dispose()
      figure.card.geometry = new THREE.PlaneGeometry(size, size).translate(0, size / 2 - under, 0)
    }
    figure.action = ''
  }

  // -- drawing -------------------------------------------------------------------------------------------------

  private readonly tick = (now: number): void => {
    this.raf = requestAnimationFrame(this.tick)
    if (!this.visible || document.hidden || now - this.last < FRAME_MS) return
    const passed = Math.min(0.1, (now - this.last) / 1000)
    this.last = now
    this.wander(now)
    for (const figure of this.figures.values()) {
      if (this.move(figure, passed)) this.remove(figure)
      else this.play(figure, now)
    }
    if (this.tvOn && this.glow.intensity) {
      const flicker = 0.85 + 0.15 * Math.sin(now / 330) * Math.sin(now / 90)
      this.glow.intensity = 0.9 * flicker
    }
    this.renderer.render(this.scene, this.camera)
    for (const [id, figure] of this.figures) this.on.draw(id, ...this.head(figure))
  }

  private wander(now: number): void {
    if (!this.nextWander) this.nextWander = now + between(WANDER_MS)
    if (now < this.nextWander || still()) return
    this.nextWander = now + between(WANDER_MS)
    const places: Record<string, Place> = {}
    for (const [id, figure] of this.figures) if (figure.place) places[id] = figure.place
    const ids = Object.keys(places)
    const id = ids[Math.floor(Math.random() * ids.length)]
    const floor = this.places.filter((place) => place.piece < 0)
    const busy = id ? places[id].piece >= 0 : true
    const to = busy ? null : wanderOn(floor, places, id, Math.random)
    if (to && !this.figures.get(id)!.steps.length) this.go(this.figures.get(id)!, to)
  }

  /** A step on her way; true: she has gone out of the door. */
  private move(figure: Figure, passed: number): boolean {
    let left = SPEED * passed
    const at = figure.root.position
    while (left > 0 && figure.steps.length) {
      const step = figure.steps[0]
      const [x, z] = [step.at.x * CELL, step.at.z * CELL]
      const far = Math.hypot(x - at.x, z - at.z)
      if (far > 0.001) {
        // To the left or the right as it is seen.
        const side = (x - at.x) * Math.cos(this.turn) - (z - at.z) * Math.sin(this.turn)
        if (Math.abs(side) > far * 0.12) figure.back = side < 0
      }
      const part = far > left ? left / far : 1
      at.set(at.x + (x - at.x) * part, at.y + (step.y - at.y) * part, at.z + (z - at.z) * part)
      left -= far * part
      if (part < 1) return false
      figure.steps.shift()
    }
    return !figure.place && !figure.steps.length
  }

  private play(figure: Figure, now: number): void {
    const { guest, actions } = figure
    const walking = figure.steps.length > 0
    const pose = figure.place?.pose ?? 'stand'
    const poked = figure.poked > now && !guest.asleep
    const idle = !walking && !poked && !guest.asleep && pose === 'stand'
    if (!idle) figure.act = ''
    else if (figure.act && now > figure.actUntil) {
      figure.act = ''
      figure.nextAct = now + between(ACT_MS)
    } else if (!figure.act && now > figure.nextAct) {
      const [name, ms] = ACTS[Math.floor(Math.random() * ACTS.length)]
      figure.nextAct = now + between(ACT_MS)
      if (actions.has(name) && !still()) {
        figure.act = name
        figure.actUntil = now + ms
      }
    }
    const at: Activity = !walking && guest.for && figure.place?.for.includes(guest.for) ? guest.for : ''
    this.hold(figure, at)
    const want = poked
      ? actions.has('pout') ? 'pout' : 'wave'
      : walking
        ? 'walk'
        : guest.asleep || pose === 'lie'
          ? 'sleep'
          : at === 'desk' && actions.has('work')
            ? 'work'
            : pose === 'sit'
              ? 'sit'
              : figure.act || 'idle'
    const name = actions.has(want) ? want : 'idle'
    const action = actions.get(name)
    if (!action) return
    if (figure.action !== name) {
      figure.action = name
      figure.since = now
      figure.card.material.map = action.texture
      figure.card.material.needsUpdate = true
    }
    const frame = Math.floor(((now - figure.since) / 1000) * action.fps)
    action.texture.offset.x = (action.loop ? frame % action.frames : Math.min(frame, action.frames - 1)) / action.frames
    const calm = walking || still() ? 0 : Math.sin(now / 540 + guest.id.length)
    figure.card.rotation.y = this.turn
    figure.card.scale.set(walking && figure.back ? -1 : 1, 1 + calm * 0.009, 1)
    figure.card.position.y = poked ? Math.abs(Math.sin(now / 130)) * 0.08 : 0
    figure.hit.rotation.y = figure.props.rotation.y = this.turn
    this.stir(figure, now)
  }

  /** What she has in her hand, or what is going on where she is. */
  private hold(figure: Figure, what: Activity): void {
    if (figure.holds === what) return
    figure.holds = what
    this.drop(figure.props, true)
    const up = figure.place?.pose === 'stand' ? 0.62 : 0.42
    if (what === 'read') {
      const book = new THREE.Group()
      for (const side of [-1, 1]) {
        const leaf = box(0.17, 0.22, 0.02, '#d98f8a', side * 0.08, 0, 0, 0.008)
        const page = box(0.15, 0.2, 0.02, '#fffaf2', side * 0.08, 0.01, 0.012, 0.006, { line: false })
        leaf.rotation.y = page.rotation.y = -side * 0.35
        book.add(leaf, page)
      }
      book.rotation.x = -0.5
      book.position.set(0, up - 0.06, 0.3)
      figure.props.add(book)
    } else if (what === 'tea' || what === 'eat') {
      const cup = turned([[0, 0], [0.04, 0.004], [0.06, 0.09], [0.054, 0.09], [0.035, 0.02], [0, 0.02]], '#fffaf2', 0.02, up, 0.3)
      figure.props.add(cup, ...puffs('#ffffff', 0.02, up + 0.1, 0.3, 0.3))
    } else if (what === 'cook') {
      figure.props.add(...puffs('#ffffff', 0, 1.15, -0.45, 0.55))
    } else if (what === 'water') {
      figure.props.add(...puffs('#8fc4ea', 0.25, 0.75, 0.1, -0.5))
    }
  }

  /** Steam rises, water falls. */
  private stir(figure: Figure, now: number): void {
    for (const part of figure.props.children) {
      if (!(part instanceof THREE.Sprite)) continue
      const { y, x, rise, n } = part.userData as { y: number; x: number; rise: number; n: number }
      const at = still() ? 0.4 : (now / 1900 + n / 4) % 1
      part.position.y = y + rise * at
      part.position.x = x + Math.sin(at * 5 + n) * 0.03
      part.material.opacity = Math.sin(at * Math.PI) * 0.55
      const size = rise > 0 ? 0.07 + at * 0.12 : 0.05
      part.scale.set(size, size, 1)
    }
  }

  private head(figure: Figure): [number, number] {
    const tall = figure.place && figure.place.pose !== 'stand' && !figure.steps.length ? TALL * 0.92 : TALL * 1.06
    const at = figure.root.position.clone()
    at.y += tall
    at.project(this.camera)
    return [((at.x + 1) / 2) * this.canvas.clientWidth, ((1 - at.y) / 2) * this.canvas.clientHeight]
  }

  // -- looking -------------------------------------------------------------------------------------------------

  private resize(): void {
    const { clientWidth: w, clientHeight: h } = this.canvas
    if (!w || !h) return
    this.renderer.setSize(w, h, false)
    this.look()
  }

  /** The whole room in the picture, as large as it goes. */
  private look(): void {
    const { clientWidth, clientHeight } = this.canvas
    if (!clientWidth || !clientHeight) return
    const [w, d] = [this.room.w * CELL, this.room.d * CELL]
    const to = new THREE.Vector3(w / 2, WALL_H * 0.36, d / 2)
    this.camera.position.set(
      to.x + Math.sin(this.turn) * Math.cos(ELEVATION) * 20,
      to.y + Math.sin(ELEVATION) * 20,
      to.z + Math.cos(this.turn) * Math.cos(ELEVATION) * 20,
    )
    this.camera.near = 1
    this.camera.far = 60
    this.camera.lookAt(to)
    this.camera.updateMatrixWorld()
    const view = this.camera.matrixWorldInverse
    const box = new THREE.Box2()
    for (const x of [-0.2, w]) {
      for (const z of [-0.2, d]) {
        for (const y of [-0.24, x < 0 || z < 0 ? WALL_H + 0.1 : 0]) {
          const p = new THREE.Vector3(x, y, z).applyMatrix4(view)
          box.expandByPoint(new THREE.Vector2(p.x, p.y))
        }
      }
    }
    const size = box.getSize(new THREE.Vector2())
    const centre = box.getCenter(new THREE.Vector2())
    const across = Math.max(size.x * 1.04, (size.y * 1.06 * clientWidth) / clientHeight) / this.zoom
    const tall = (across * clientHeight) / clientWidth
    this.camera.left = centre.x - across / 2
    this.camera.right = centre.x + across / 2
    this.camera.top = centre.y + tall / 2
    this.camera.bottom = centre.y - tall / 2
    this.camera.updateProjectionMatrix()
  }

  private listen(): void {
    const { canvas } = this
    const listen = <K extends keyof HTMLElementEventMap>(
      type: K,
      does: (e: HTMLElementEventMap[K]) => void,
      options?: AddEventListenerOptions,
    ) => {
      canvas.addEventListener(type, does, options)
      this.off.push(() => canvas.removeEventListener(type, does, options))
    }
    let from: { x: number; turn: number; moved: boolean } | null = null
    let drag: { old: Piece; grip: Cell; now: Piece; may: boolean } | null = null
    let slide: { old: Hung; grip: Cell; now: Hung; may: boolean } | null = null
    listen('pointerdown', (e) => {
      if (e.button !== 0) return
      canvas.setPointerCapture(e.pointerId)
      if (this.editing) {
        const thing = this.thing(e)
        this.choose(thing)
        if (thing && isHung(thing)) {
          const on = this.onWall(e, thing.wall)
          if (on) slide = { old: thing, grip: { x: on.at - thing.at, z: on.y - thing.y }, now: thing, may: true }
          return
        }
        const floor = thing && this.floor(e)
        if (thing && floor) {
          drag = { old: thing, grip: { x: floor.x - thing.x, z: floor.z - thing.z }, now: thing, may: true }
          return
        }
      }
      from = { x: e.clientX, turn: this.turn, moved: false }
    })
    listen('pointermove', (e) => {
      if (slide) {
        const now = this.slid(e, slide.old, slide.grip)
        if (!now || (now.at === slide.now.at && now.y === slide.now.y && now.wall === slide.now.wall)) return
        slide.now = now
        slide.may = canHang(this.room, this.room.look.hung, now, slide.old)
        this.pin(this.hung[this.room.look.hung.indexOf(slide.old)], now, 0.05)
        this.renderer.shadowMap.needsUpdate = true
        this.show(now, slide.may)
        return
      }
      if (drag) {
        const floor = this.floor(e)
        if (!floor) return
        const { w, d } = sizeOf(drag.old)
        const x = THREE.MathUtils.clamp(Math.round(floor.x - drag.grip.x), 0, this.room.w - w)
        const z = THREE.MathUtils.clamp(Math.round(floor.z - drag.grip.z), 0, this.room.d - d)
        if (x === drag.now.x && z === drag.now.z) return
        drag.now = { ...drag.old, x, z }
        drag.may = canStand(this.room, drag.now, drag.old)
        const corner = cornerOf(drag.now)
        const model = this.models[this.room.pieces.indexOf(drag.old)]
        model.position.set(corner.x * CELL, 0.06, corner.z * CELL)
        this.renderer.shadowMap.needsUpdate = true
        this.show(drag.now, drag.may)
        return
      }
      if (!from) {
        canvas.style.cursor = (this.editing ? this.thing(e) : this.under(e)) ? 'pointer' : 'grab'
        return
      }
      const by = e.clientX - from.x
      if (Math.abs(by) > 5) from.moved = true
      if (!from.moved) return
      this.turn = THREE.MathUtils.clamp(from.turn - by * 0.004, TURN.least, TURN.most)
      this.look()
    })
    const letGo = () => {
      const held = drag ?? slide
      drag = slide = null
      if (!held) return
      const { old, now, may } = held
      if (now === old || !may) {
        this.arrange() // back where it was
        this.show(old, true)
        return
      }
      this.put<Piece | Hung>(old, now)
      this.tell()
    }
    listen('pointerup', (e) => {
      if (drag || slide) return letGo()
      const clicked = from && !from.moved && !this.editing
      from = null
      if (!clicked) return
      const what = this.under(e)
      if (what === 'lamp') this.lampSet = !this.lampOn
      else if (what === 'tv') this.tvSet = !this.tvOn
      else if (what) this.on.pick(what)
      this.switches()
    })
    listen('pointercancel', () => {
      from = null
      letGo()
    })
    listen('dblclick', () => {
      this.turn = TURN.home
      this.zoom = 1
      this.look()
    })
    listen(
      'wheel',
      (e) => {
        e.preventDefault()
        this.zoom = THREE.MathUtils.clamp(this.zoom * Math.exp(-e.deltaY * 0.0012), 1, 2.4)
        this.look()
      },
      { passive: false },
    )
  }

  private ray(e: PointerEvent): THREE.Raycaster {
    const area = this.canvas.getBoundingClientRect()
    const ray = new THREE.Raycaster()
    ray.setFromCamera(
      new THREE.Vector2(((e.clientX - area.left) / area.width) * 2 - 1, 1 - ((e.clientY - area.top) / area.height) * 2),
      this.camera,
    )
    return ray
  }

  /** The piece the pointer is on; a rug only where nothing else is. */
  private piece(e: PointerEvent): Piece | null {
    const ray = this.ray(e)
    const found = this.models
      .map((model, n) => ({ n, at: ray.intersectObject(model, true)[0]?.distance ?? Infinity }))
      .filter(({ at }) => at < Infinity)
      .sort((a, b) => a.at - b.at)
      .map(({ n }) => this.room.pieces[n])
    return found.find((piece) => !furnitureOf(piece.kind).flat) ?? found[0] ?? null
  }

  /** What the pointer is on that can be moved: what hangs behind a piece is not on it. */
  private thing(e: PointerEvent): Piece | Hung | null {
    const ray = this.ray(e)
    const piece = this.piece(e)
    const far = (model: THREE.Object3D) => ray.intersectObject(model, true)[0]?.distance ?? Infinity
    const before = piece && !furnitureOf(piece.kind).flat ? far(this.models[this.room.pieces.indexOf(piece)]) : Infinity
    const nearest = this.hung.map((model, n) => ({ n, at: far(model) })).sort((a, b) => a.at - b.at)[0]
    if (nearest && nearest.at < before) return this.room.look.hung[nearest.n]
    return piece
  }

  /** Where on a wall the pointer is, in quarter metres. */
  private onWall(e: PointerEvent, wall: Wall): { at: number; y: number } | null {
    const normal = wall === 'left' ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1)
    const at = this.ray(e).ray.intersectPlane(new THREE.Plane(normal, 0), new THREE.Vector3())
    return at && { at: (wall === 'left' ? at.z : at.x) / Q, y: at.y / Q }
  }

  /** It hangs where the pointer takes it: round the corner onto the other wall, too. */
  private slid(e: PointerEvent, old: Hung, grip: Cell): Hung | null {
    const { w, h } = hangingOf(old.kind)
    const other: Wall = old.wall === 'left' ? 'right' : 'left'
    const mine = this.onWall(e, old.wall)
    const wall = mine && mine.at < 0 && !this.room.open ? other : old.wall
    const on = wall === old.wall ? mine : this.onWall(e, wall)
    if (!on) return null
    const at = wall === old.wall ? on.at - grip.x : on.at - w / 2
    return {
      kind: old.kind,
      wall,
      at: THREE.MathUtils.clamp(Math.round(at), 0, lengthOf(this.room, wall) - w),
      y: THREE.MathUtils.clamp(Math.round(on.y - grip.z), 0, 11 - h),
    }
  }

  /** Where on the floor the pointer is, in cells. */
  private floor(e: PointerEvent): Cell | null {
    const at = this.ray(e).ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3())
    return at && { x: at.x / CELL, z: at.z / CELL }
  }

  /** What the pointer is on: someone's id, 'lamp', 'tv', or ''. */
  private under(e: PointerEvent): string {
    const hits = [...this.figures.values()].map((f) => f.hit)
    for (const hit of hits) hit.visible = true
    const found = this.ray(e).intersectObjects(hits, false)[0]
    for (const hit of hits) hit.visible = false
    if (found) return String(found.object.userData.id)
    const piece = this.piece(e)
    return piece?.kind === 'tv' ? 'tv' : piece && LAMPS[piece.kind] && piece.kind !== 'desk' ? 'lamp' : ''
  }
}

let soft: THREE.Texture | null = null

/** A few soft dots, to rise from something hot (`rise` in metres) or to fall. */
function puffs(color: string, x: number, y: number, z: number, rise: number): THREE.Sprite[] {
  if (!soft) {
    const el = document.createElement('canvas')
    el.width = el.height = 64
    const g = el.getContext('2d')!
    const fill = g.createRadialGradient(32, 32, 2, 32, 32, 30)
    fill.addColorStop(0, 'rgba(255,255,255,1)')
    fill.addColorStop(1, 'rgba(255,255,255,0)')
    g.fillStyle = fill
    g.fillRect(0, 0, 64, 64)
    soft = new THREE.CanvasTexture(el)
  }
  const map = soft
  return [0, 1, 2, 3].map((n) => {
    const dot = new THREE.Sprite(new THREE.SpriteMaterial({ map, color, transparent: true, opacity: 0, depthWrite: false }))
    dot.position.set(x, y, z)
    dot.userData = { x, y, rise, n }
    return dot
  })
}

/** Small pictures of the furniture and of what hangs, for the cupboard they are taken from. */
export function thumbnails(size = 96): { pieces: Record<Kind, string>; hung: Record<HangKind, string> } {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })
  renderer.setSize(size, size, false)
  renderer.setPixelRatio(1)
  const taken = (model: THREE.Group, front: boolean): string => {
    const scene = new THREE.Scene()
    const sun = new THREE.DirectionalLight('#ffffff', 1.6)
    sun.position.set(4, 7, 2)
    scene.add(model, sun, new THREE.HemisphereLight('#f4f8ff', '#e4d8ca', 1.9))
    const around = new THREE.Box3().setFromObject(model)
    const centre = around.getCenter(new THREE.Vector3())
    const r = (around.getSize(new THREE.Vector3()).length() / 2) * (front ? 0.95 : 1)
    const camera = new THREE.OrthographicCamera(-r, r, r, -r, 0.1, 40)
    camera.position.set(centre.x + (front ? 2 : 8), centre.y + (front ? 1.5 : 6.4), centre.z + 8)
    camera.lookAt(centre)
    renderer.render(scene, camera)
    model.traverse((part) => part instanceof THREE.Mesh && part.geometry.dispose())
    return renderer.domElement.toDataURL('image/png')
  }
  const each = <K extends string>(all: Record<K, unknown>, make: (kind: K) => string) =>
    Object.fromEntries((Object.keys(all) as K[]).map((kind) => [kind, make(kind)])) as Record<K, string>
  const out = {
    pieces: each(FURNITURE, (kind) => taken(furniture(kind), false)),
    hung: each(HANGINGS, (kind) => taken(hanging(kind), true)),
  }
  renderer.dispose()
  renderer.forceContextLoss()
  return out
}

/** Who has no pet: the first character of her name, on a disc. */
function badge(name: string): THREE.Texture {
  const el = document.createElement('canvas')
  el.width = el.height = 256
  const g = el.getContext('2d')!
  g.fillStyle = '#fffaf2'
  g.beginPath()
  g.arc(128, 128, 118, 0, 7)
  g.fill()
  g.lineWidth = 8
  g.strokeStyle = '#b9a8dd'
  g.stroke()
  g.fillStyle = '#6a5a86'
  g.font = '600 120px sans-serif'
  g.textAlign = 'center'
  g.textBaseline = 'middle'
  g.fillText([...name][0] ?? '', 128, 136)
  const texture = new THREE.CanvasTexture(el)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}
