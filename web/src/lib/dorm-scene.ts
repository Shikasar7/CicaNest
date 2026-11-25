import * as THREE from 'three'

import type { PetSprite } from './desktop.ts'
import { CELL, middle, placesOf, route, TALL, type Place, type RoomPlan } from './dorm.ts'
import { furniture, shell, skyTexture, WALL_H } from './dorm-models.ts'
import { seatOn, wanderOn, type Daylight, type Pose } from './home-stage.ts'

/** 3D 小窝: draws a room of lib/dorm.ts with the companions in it as paper figures (their
 * desktop pets' pictures, standing up and turned to the camera). Draws only while it can be seen. */

export type Guest = { id: string; name: string; pose: Pose; asleep: boolean; sprite: PetSprite | null }

/** Where her head is on the picture, in CSS pixels, each time it is drawn. */
export type OnDraw = (id: string, x: number, y: number) => void

const SPEED = 0.85 // metres a second
const FRAME_MS = 1000 / 31
const WANDER_MS = [9_000, 22_000]
const ACT_MS = [3_500, 8_000]
const POKE_MS = 2200
const ELEVATION = THREE.MathUtils.degToRad(27)
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
  root: THREE.Group
  card: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>
  hit: THREE.Mesh
  actions: Map<string, Action>
  /** Her place, or the one she is on her way to; null: she is leaving. */
  place: Place | null
  /** Where she set out from, for the next way. */
  came: { at: { x: number; z: number }; from: { x: number; z: number }; y: number }
  steps: { at: { x: number; z: number }; y: number }[]
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
  private readonly bulb = new THREE.PointLight('#ffc877', 0, 6.5, 1)
  private readonly glow = new THREE.PointLight('#9fc4ff', 0, 3.2, 1.6)
  private readonly sky: THREE.Mesh
  private readonly patch: THREE.Mesh
  private readonly figures = new Map<string, Figure>()
  private readonly places: Place[]
  private readonly loader = new THREE.TextureLoader()
  private readonly watch: ResizeObserver
  private readonly seen: IntersectionObserver
  private readonly off: (() => void)[] = []
  private turn = TURN.home
  private zoom = 1
  private visible = true
  private light: Daylight = 'day'
  private lampOn = false
  private lampSet: boolean | null = null // by a click, until the light changes
  private tvOn = false
  private tvSet: boolean | null = null
  private settled = false
  private nextWander = 0
  private last = 0
  private raf = 0

  private readonly canvas: HTMLCanvasElement
  private readonly room: RoomPlan
  private readonly on: { draw: OnDraw; pick: (id: string) => void }

  constructor(canvas: HTMLCanvasElement, room: RoomPlan, on: { draw: OnDraw; pick: (id: string) => void }) {
    this.canvas = canvas
    this.room = room
    this.on = on
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' })
    this.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio))
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFShadowMap
    this.places = placesOf(room)

    const { group, sky, patch } = shell(room)
    this.sky = sky
    this.patch = patch
    this.scene.add(group)
    for (const piece of room.pieces) {
      const model = furniture(piece.kind)
      model.position.set(piece.x * CELL, 0, piece.z * CELL)
      this.scene.add(model)
      if (piece.kind === 'lamp') this.bulb.position.set((piece.x + 0.5) * CELL, 1.62, (piece.z + 0.5) * CELL + 0.5)
      if (piece.kind === 'tv') this.glow.position.set((piece.x + 1.4) * CELL, 1, (piece.z + 2) * CELL)
    }
    const [w, d] = [room.w * CELL, room.d * CELL]
    this.sun.position.set(w / 2 + 5, 9, d / 2 + 2.6)
    this.sun.target.position.set(w / 2, 0, d / 2)
    this.sun.castShadow = true
    this.sun.shadow.mapSize.set(2048, 2048)
    this.sun.shadow.radius = 3
    this.sun.shadow.bias = -0.0004
    this.sun.shadow.normalBias = 0.03
    Object.assign(this.sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 22 })
    this.scene.add(this.sun, this.sun.target, this.fill, this.bulb, this.glow)
    this.setLight('day')

    this.watch = new ResizeObserver(() => this.resize())
    this.watch.observe(canvas)
    this.seen = new IntersectionObserver((entries) => (this.visible = entries.at(-1)!.isIntersecting))
    this.seen.observe(canvas)
    this.resize()
    this.listen()
    this.raf = requestAnimationFrame(this.tick)
  }

  dispose(): void {
    cancelAnimationFrame(this.raf)
    this.watch.disconnect()
    this.seen.disconnect()
    for (const off of this.off) off()
    this.scene.traverse((thing) => {
      if (!(thing instanceof THREE.Mesh)) return
      thing.geometry.dispose()
      for (const material of [thing.material].flat() as THREE.MeshBasicMaterial[]) {
        material.map?.dispose()
        material.dispose()
      }
    })
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
    const sky = this.sky.material as THREE.MeshBasicMaterial
    sky.map?.dispose()
    sky.map = skyTexture(light)
    sky.needsUpdate = true
    const patch = this.patch.material as THREE.MeshBasicMaterial
    patch.color.set(l.patch)
    patch.opacity = l.patchOn
    for (const figure of this.figures.values()) figure.card.material.color.set(l.tint)
    this.switches()
  }

  private switches(): void {
    const sitting = [...this.figures.values()].some((f) => f.place?.pose === 'sit')
    this.lampOn = this.lampSet ?? LIGHTS[this.light].lamp
    this.tvOn = this.tvSet ?? (sitting && LIGHTS[this.light].lamp)
    this.bulb.intensity = this.lampOn ? (this.light === 'night' ? 2.6 : 1.5) : 0
    const shade = this.scene.getObjectByName('shade') as THREE.Mesh | undefined
    if (shade) (shade.material as THREE.MeshToonMaterial).emissiveIntensity = this.lampOn ? 0.9 : 0
    const screen = this.scene.getObjectByName('screen') as THREE.Mesh | undefined
    if (screen) (screen.material as THREE.MeshBasicMaterial).color.set(this.tvOn ? '#ffffff' : '#2a2833')
    this.glow.intensity = this.tvOn ? 0.9 : 0
  }

  setGuests(guests: Guest[]): void {
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
    this.settled = this.settled || guests.length > 0
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
    root.add(shadow, card, hit)
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
    const door: Place = { id: 'door', pose: 'stand', at: middle(this.room.door), y: 0, from: this.room.door }
    figure.steps = still() ? [{ at: door.at, y: 0 }] : route(this.room, figure.came, door)
    figure.came = door
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
    for (const [id, figure] of this.figures) {
      if (this.move(figure, passed)) {
        this.figures.delete(id)
        this.scene.remove(figure.root)
        for (const action of figure.actions.values()) action.texture.dispose()
        this.on.draw(id, NaN, NaN)
        continue
      }
      this.play(figure, now)
    }
    if (this.tvOn) {
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
    const to = id ? wanderOn(this.places, places, id, Math.random) : null
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
    const want = poked
      ? actions.has('pout') ? 'pout' : 'wave'
      : walking
        ? 'walk'
        : guest.asleep || pose === 'lie'
          ? 'sleep'
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
    figure.hit.rotation.y = this.turn
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
    listen('pointerdown', (e) => {
      if (e.button !== 0) return
      from = { x: e.clientX, turn: this.turn, moved: false }
      canvas.setPointerCapture(e.pointerId)
    })
    listen('pointermove', (e) => {
      if (!from) {
        canvas.style.cursor = this.under(e) ? 'pointer' : 'grab'
        return
      }
      const by = e.clientX - from.x
      if (Math.abs(by) > 5) from.moved = true
      if (!from.moved) return
      this.turn = THREE.MathUtils.clamp(from.turn - by * 0.004, TURN.least, TURN.most)
      this.look()
    })
    listen('pointerup', (e) => {
      const clicked = from && !from.moved
      from = null
      if (!clicked) return
      const what = this.under(e)
      if (what === 'lamp') this.lampSet = !this.lampOn
      else if (what === 'tv') this.tvSet = !this.tvOn
      else if (what) this.on.pick(what)
      this.switches()
    })
    listen('pointercancel', () => (from = null))
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

  /** What the pointer is on: someone's id, 'lamp', 'tv', or ''. */
  private under(e: PointerEvent): string {
    const box = this.canvas.getBoundingClientRect()
    const ray = new THREE.Raycaster()
    ray.setFromCamera(
      new THREE.Vector2(((e.clientX - box.left) / box.width) * 2 - 1, 1 - ((e.clientY - box.top) / box.height) * 2),
      this.camera,
    )
    const hits = [...this.figures.values()].map((f) => f.hit)
    for (const hit of hits) hit.visible = true
    const found = ray.intersectObjects(hits, false)[0]
    for (const hit of hits) hit.visible = false
    if (found) return String(found.object.userData.id)
    for (const name of ['lamp', 'tv']) {
      const thing = this.scene.getObjectByName(name)
      if (thing && ray.intersectObject(thing, true).length) return name
    }
    return ''
  }
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
