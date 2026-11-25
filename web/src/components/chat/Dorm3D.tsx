import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Archive, LoaderCircle, RotateCw, Sofa, Undo2, UsersRound } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { petSprite } from '@/components/chat/PetSprite'
import { sendJson } from '@/lib/api'
import type { PetSprite } from '@/lib/desktop'
import {
  activityOf,
  BEDROOM_KEY,
  FURNITURE,
  furnitureOf,
  MAX_PIECES,
  planOf,
  SHARED_ROOMS,
  type Furnished,
  type Kind,
  type Piece,
} from '@/lib/dorm'
import {
  FLOORS,
  HANGINGS,
  hangingOf,
  MAX_HUNG,
  WALLPAPERS,
  type FloorKind,
  type HangKind,
  type Hung,
  type WallKind,
} from '@/lib/dorm-look'
import { floorSwatch, paperSwatch } from '@/lib/dorm-paint'
import { DormScene, thumbnails, type Guest } from '@/lib/dorm-scene'
import type { Daylight } from '@/lib/home-stage'
import type { LifePerson } from '@/lib/life'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { POKED, POKED_ASLEEP, POKE_MS } from '@/lib/poke'
import { cn } from '@/lib/utils'

/** 3D 小窝: its rooms, who is in them, and furnishing them. Loaded only when it is opened. */

const ASLEEP = '睡着了'
const TALK_MS = 6_000
const GROUPS = [...new Set(Object.values(FURNITURE).map((f) => f.group))]
const KINDS = Object.keys(FURNITURE) as Kind[]
const HANG_GROUPS = [...new Set(Object.values(HANGINGS).map((h) => h.group))]
const HANG_KINDS = Object.keys(HANGINGS) as HangKind[]
const PAPERS = Object.keys(WALLPAPERS) as WallKind[]
const FLOOR_KINDS = Object.keys(FLOORS) as FloorKind[]
const TABS = ['家具', '墙纸', '地板', '挂饰'] as const

/** Small pictures of everything there is to choose, made once. */
function makePictures() {
  return {
    ...thumbnails(),
    walls: Object.fromEntries(PAPERS.map((kind) => [kind, paperSwatch(kind)])) as Record<WallKind, string>,
    floors: Object.fromEntries(FLOOR_KINDS.map((kind) => [kind, floorSwatch(kind)])) as Record<FloorKind, string>,
  }
}

const labelOf = (thing: Piece | Hung) =>
  'wall' in thing ? hangingOf(thing.kind).label : furnitureOf(thing.kind).label

const BEHIND: Record<Daylight, string> = {
  morning: 'from-[#fff3e0] to-[#f6e3cf] dark:from-[#3a342f] dark:to-[#2b2726]',
  day: 'from-[#eef6fd] to-[#e3edf4] dark:from-[#2c3238] dark:to-[#24282d]',
  evening: 'from-[#ffe4d0] to-[#ecd0d2] dark:from-[#3d302f] dark:to-[#2c2529]',
  night: 'from-[#c9cdea] to-[#b4b6d8] dark:from-[#262a44] dark:to-[#1c1e30]',
}

const roomOf = (person: LifePerson) =>
  person.now ? (person.now.room === '卧室' ? BEDROOM_KEY + person.bot_id : person.now.room) : ''

/** The room with the most of them in it; who is up counts more. */
function liveliest(residents: LifePerson[]): string {
  const score = new Map<string, number>()
  for (const person of residents) {
    const room = roomOf(person)
    if (room) score.set(room, (score.get(room) ?? 0) + (person.now?.doing === ASLEEP ? 1 : 100))
  }
  return [...score].sort((a, b) => b[1] - a[1])[0]?.[0] ?? SHARED_ROOMS[0]
}

function guestOf(person: LifePerson, room: string, sprite: PetSprite | null): Guest {
  const here = roomOf(person) === room && person.now !== null
  // Called in to be looked at, she just sits down.
  const pose = here ? (person.now!.pose ?? 'stand') : 'sit'
  // Whose pet has no picture of sitting stands.
  const shown = sprite && pose === 'sit' && !sprite.actions.sit ? 'stand' : pose
  return {
    id: person.bot_id,
    name: person.name,
    pose: shown,
    for: here && shown === pose ? activityOf(person.now!.doing, pose) : '',
    asleep: here && person.now!.doing === ASLEEP,
    sprite,
  }
}

function Choice({
  label,
  picture,
  large = false,
  chosen = false,
  onClick,
}: {
  label: string
  picture?: string
  large?: boolean
  chosen?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={large ? chosen : undefined}
      className={cn(
        'flex w-18 shrink-0 flex-col items-center gap-0.5 rounded-xl border px-1 pt-1 pb-1.5 text-[11px] hover:border-primary/40 hover:text-primary active:scale-95',
        chosen && 'border-primary bg-primary/10 text-primary',
      )}
    >
      {picture ? (
        <img src={picture} alt="" className={large ? 'h-12 w-full rounded-lg object-cover' : 'size-12 object-contain'} />
      ) : (
        <span className="size-12" />
      )}
      {label}
    </button>
  )
}

function Shelf({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="shrink-0">
      <p className="px-1 pb-1 text-[11px] text-muted-foreground">{name}</p>
      <div className="flex gap-1.5">{children}</div>
    </div>
  )
}

const tool =
  'flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 hover:text-primary disabled:pointer-events-none disabled:opacity-40'

export default function Dorm3D({
  residents,
  light,
  onPick,
}: {
  residents: LifePerson[]
  light: Daylight
  onPick: (id: string) => void
}) {
  const { botsVersion } = useLive()
  const { data: saved, reload } = useLiveData<{ rooms: Record<string, unknown> }>('/api/life/rooms', [botsVersion])
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const scene = useRef<DormScene | null>(null)
  const labels = useRef(new Map<string, HTMLElement>())
  const [failed, setFailed] = useState(false)
  const [sprites, setSprites] = useState<Record<string, PetSprite | null>>({})
  const [chosenRoom, setChosenRoom] = useState<string | null>(null)
  const [called, setCalled] = useState(false)
  const [said, setSaid] = useState<{ id: string; words: string } | null>(null)
  const [turn, setTurn] = useState(0)
  // Furnishing: the room as it is being changed, and as it was before each change.
  const [draft, setDraft] = useState<Furnished | null>(null)
  const [before, setBefore] = useState<Furnished[]>([])
  const [thing, setThing] = useState<Piece | Hung | null>(null)
  const [tab, setTab] = useState<(typeof TABS)[number]>('家具')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [pictures, setPictures] = useState<ReturnType<typeof makePictures> | null>(null)
  const editing = draft !== null

  const rooms = useMemo(
    () => [
      ...SHARED_ROOMS.map((key) => ({ key, name: key })),
      ...residents.map((p) => ({ key: BEDROOM_KEY + p.bot_id, name: `${p.name}的卧室` })),
    ],
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- what it depends on of `residents`
    [residents.map((p) => p.bot_id + p.name).join('|')],
  )
  const [first] = useState(() => liveliest(residents))
  const wanted = chosenRoom ?? first
  const room = rooms.some((r) => r.key === wanted) ? wanted : SHARED_ROOMS[0]
  const stored = JSON.stringify(saved?.rooms[room] ?? null)
  const plan = useMemo(() => planOf(room, JSON.parse(stored) ?? undefined), [room, stored])

  const inside = residents.filter((p) => roomOf(p) === room)
  const shown = called ? residents : inside
  const key = shown.map((p) => `${p.bot_id} ${roomOf(p)} ${p.now?.pose} ${p.now?.doing}`).join('|')
  const latest = useRef({ shown, sprites, room, draft })
  useEffect(() => {
    latest.current = { shown, sprites, room, draft }
  })

  useEffect(() => {
    if (!canvas.current) return
    let made: DormScene
    try {
      made = new DormScene(canvas.current, planOf(latest.current.room), {
        draw: (id, x, y) => {
          const label = labels.current.get(id)
          if (!label) return
          label.style.visibility = Number.isFinite(x) ? 'visible' : 'hidden'
          if (Number.isFinite(x)) label.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`
        },
        pick: (id) => {
          made.poke(id)
          const person = latest.current.shown.find((p) => p.bot_id === id)
          const asleep = person?.now?.doing === ASLEEP && roomOf(person) === latest.current.room
          const lines = latest.current.sprites[id]?.poked
          const pool = asleep ? POKED_ASLEEP : lines?.length ? lines : POKED
          setSaid({ id, words: pool[Math.floor(Math.random() * pool.length)] })
        },
        furnished: (changed) => {
          const old = latest.current.draft
          if (old) setBefore((all) => [...all, old])
          setDraft(changed)
          setMessage('')
        },
        chosen: setThing,
      })
    } catch {
      // oxlint-disable-next-line react/set-state-in-effect -- no WebGL here: only found out by trying
      setFailed(true)
      return
    }
    scene.current = made
    return () => {
      scene.current = null
      made.dispose()
    }
  }, [])

  useEffect(() => scene.current?.enter(plan), [plan])
  useEffect(() => scene.current?.setLight(light), [light])
  useEffect(() => scene.current?.setEditing(editing), [editing])

  useEffect(() => {
    let cancelled = false
    for (const person of latest.current.shown) {
      void petSprite(person.bot_id).then((sprite) => {
        if (cancelled) return
        setSprites((known) => (person.bot_id in known ? known : { ...known, [person.bot_id]: sprite }))
      })
    }
    return () => {
      cancelled = true
    }
  }, [key])

  useEffect(() => {
    const people = latest.current.shown
    // Once it is known what they look like: they come in together.
    if (people.some((p) => !(p.bot_id in sprites))) return
    scene.current?.setGuests(people.map((p) => guestOf(p, latest.current.room, sprites[p.bot_id])))
  }, [key, sprites, plan, editing])

  useEffect(() => {
    if (!said) return
    const timer = window.setTimeout(() => setSaid(null), POKE_MS)
    return () => window.clearTimeout(timer)
  }, [said])

  useEffect(() => {
    const timer = window.setInterval(() => setTurn((n) => n + 1), TALK_MS)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!editing) return
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (e.key === 'r' || e.key === 'R') scene.current?.rotate()
      else if (e.key === 'Delete' || e.key === 'Backspace') scene.current?.stow()
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editing])

  const start = () => {
    setDraft({ pieces: plan.pieces, look: plan.look })
    setBefore([])
    setMessage('')
    if (!pictures) setPictures(makePictures())
  }
  const stop = () => {
    scene.current?.setFurnished(plan)
    setDraft(null)
    setBefore([])
    setMessage('')
  }
  const undo = () => {
    const last = before.at(-1)
    if (!last) return
    scene.current?.setFurnished(last)
    setBefore(before.slice(0, -1))
    setDraft(last)
  }
  const reset = () => {
    const { pieces, look } = planOf(room)
    if (draft) setBefore([...before, draft])
    scene.current?.setFurnished({ pieces, look })
    setDraft({ pieces, look })
  }
  const save = async () => {
    if (!draft) return
    setBusy(true)
    setMessage('')
    try {
      const { pieces, look } = planOf(room)
      const same = JSON.stringify(draft) === JSON.stringify({ pieces, look })
      await sendJson('PUT', `/api/life/rooms/${encodeURIComponent(room)}`, same ? { pieces: null } : draft)
      reload()
      setDraft(null)
      setBefore([])
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const add = (kind: Kind) => {
    if ((draft?.pieces.length ?? 0) >= MAX_PIECES) setMessage('这间房的家具够多了')
    else if (!scene.current?.add(kind)) setMessage(`放不下「${furnitureOf(kind).label}」了，先挪开一点`)
  }
  const hang = (kind: HangKind) => {
    if ((draft?.look.hung.length ?? 0) >= MAX_HUNG) setMessage('墙上挂的够多了')
    else if (!scene.current?.hang(kind)) setMessage(`墙上没有地方挂「${hangingOf(kind).label}」了，先挪开一点`)
  }

  if (failed) {
    return (
      <p className="m-auto rounded-2xl border border-dashed p-6 text-center text-sm text-muted-foreground">
        这台电脑画不了 3D，先看线稿的吧。
      </p>
    )
  }
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap gap-1.5 text-xs">
        {rooms.map((r) => {
          const people = residents.filter((p) => roomOf(p) === r.key)
          return (
            <button
              key={r.key}
              type="button"
              disabled={editing}
              aria-pressed={r.key === room}
              onClick={() => setChosenRoom(r.key)}
              className={cn(
                'flex items-center gap-1.5 rounded-full border px-3 py-1 disabled:opacity-50',
                r.key === room ? 'border-primary/40 bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {r.name}
              {people.slice(0, 4).map((p) => (
                <BotGlyph key={p.bot_id} name={p.name} id={p.bot_id} size="sm" className="!size-4 text-[9px]" />
              ))}
            </button>
          )
        })}
      </div>
      <div className={cn('relative min-h-72 flex-1 overflow-hidden rounded-3xl border bg-linear-to-b', BEHIND[light])}>
        <canvas ref={canvas} className="absolute inset-0 size-full cursor-grab touch-none active:cursor-grabbing" />
        <div className={cn('pointer-events-none absolute inset-0 overflow-hidden', editing && 'hidden')}>
          {shown.map((p, n) => {
            const doing = roomOf(p) === room ? p.now?.doing : '过来看看'
            const words = said?.id === p.bot_id ? said.words : turn % shown.length === n ? doing : undefined
            return (
              <div
                key={p.bot_id}
                ref={(el) => {
                  if (el) labels.current.set(p.bot_id, el)
                  else labels.current.delete(p.bot_id)
                }}
                className="invisible absolute top-0 left-0 h-0 w-0"
              >
                <button
                  type="button"
                  onClick={() => onPick(p.bot_id)}
                  title={p.now ? `${p.name}：${p.now.doing}` : p.name}
                  className={cn(
                    'pointer-events-auto absolute bottom-0 left-0 w-max max-w-48 -translate-x-1/2 text-left text-xs leading-snug shadow-sm',
                    words ? 'z-10 rounded-xl border bg-card px-2.5 py-1' : 'rounded-full bg-card/85 px-2 font-medium',
                  )}
                >
                  <span className="line-clamp-3">
                    <b className="font-semibold">{p.name}</b>
                    {words && <span className="text-muted-foreground"> {words}</span>}
                  </span>
                </button>
              </div>
            )
          })}
        </div>
        {editing && (
          <div className="absolute inset-x-3 top-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-card/85 px-3 py-1 font-medium">
              {thing ? labelOf(thing) : '拖动家具和挂饰挪位置，点下面的加进来'}
            </span>
            <button type="button" className={tool} disabled={!thing || 'wall' in thing} onClick={() => scene.current?.rotate()}>
              <RotateCw className="size-3.5" />
              旋转（R）
            </button>
            <button type="button" className={tool} disabled={!thing} onClick={() => scene.current?.stow()}>
              <Archive className="size-3.5" />
              收起来（Del）
            </button>
            <button type="button" className={tool} disabled={!before.length} onClick={undo}>
              <Undo2 className="size-3.5" />
              撤销
            </button>
            <button type="button" className={tool} onClick={reset}>
              恢复原样
            </button>
            <span className="ml-auto flex gap-2">
              <button type="button" className={tool} disabled={busy} onClick={stop}>
                取消
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void save()}
                className="flex items-center gap-1.5 rounded-full bg-primary px-4 py-1 font-medium text-primary-foreground active:scale-[0.98] disabled:opacity-50"
              >
                {busy && <LoaderCircle className="size-3.5 animate-spin" />}
                布置好了
              </button>
            </span>
          </div>
        )}
        {message && (
          <p role="alert" className="absolute inset-x-0 bottom-14 mx-auto w-fit rounded-full border bg-card px-4 py-1 text-xs shadow-sm">
            {message}
          </p>
        )}
        {!editing && (
          <div className="pointer-events-none absolute inset-x-3 bottom-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-card/80 px-3 py-1 text-muted-foreground max-sm:hidden">
              {shown.length ? '点小人戳一下，点名字看她的今天；灯和电视能开关；拖动转一转，滚轮放大' : '现在没有人在这里'}
            </span>
            <span className="pointer-events-auto ml-auto flex gap-2">
              <button
                type="button"
                onClick={() => setCalled(!called)}
                aria-pressed={called}
                title="只是看看，不改她们的日程"
                className={cn(tool, called && 'border-primary/40 text-primary')}
              >
                <UsersRound className="size-3.5" />
                {called ? '都叫来了' : '叫大家都来'}
              </button>
              <button type="button" onClick={start} disabled={!saved} className={tool}>
                <Sofa className="size-3.5" />
                布置
              </button>
            </span>
          </div>
        )}
      </div>
      {draft && (
        <div className="rounded-2xl border bg-card p-2">
          <div role="tablist" className="flex gap-1 pb-1.5 text-xs">
            {TABS.map((name) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={tab === name}
                onClick={() => setTab(name)}
                className={cn(
                  'rounded-full px-3 py-0.5',
                  tab === name ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {name}
              </button>
            ))}
          </div>
          <div className="flex gap-4 overflow-x-auto pb-1">
            {tab === '家具' &&
              GROUPS.map((group) => (
                <Shelf key={group} name={group}>
                  {KINDS.filter((kind) => FURNITURE[kind].group === group).map((kind) => (
                    <Choice key={kind} label={FURNITURE[kind].label} picture={pictures?.pieces[kind]} onClick={() => add(kind)} />
                  ))}
                </Shelf>
              ))}
            {tab === '挂饰' &&
              HANG_GROUPS.map((group) => (
                <Shelf key={group} name={group}>
                  {HANG_KINDS.filter((kind) => HANGINGS[kind].group === group).map((kind) => (
                    <Choice key={kind} label={HANGINGS[kind].label} picture={pictures?.hung[kind]} onClick={() => hang(kind)} />
                  ))}
                </Shelf>
              ))}
            {tab === '墙纸' && (
              <Shelf name="两面墙一起换">
                {PAPERS.map((kind) => (
                  <Choice
                    key={kind}
                    large
                    label={WALLPAPERS[kind].label}
                    picture={pictures?.walls[kind]}
                    chosen={draft.look.wall === kind}
                    onClick={() => draft.look.wall !== kind && scene.current?.decorate({ wall: kind })}
                  />
                ))}
              </Shelf>
            )}
            {tab === '地板' && (
              <Shelf name="整间一起换">
                {FLOOR_KINDS.map((kind) => (
                  <Choice
                    key={kind}
                    large
                    label={FLOORS[kind].label}
                    picture={pictures?.floors[kind]}
                    chosen={draft.look.floor === kind}
                    onClick={() => draft.look.floor !== kind && scene.current?.decorate({ floor: kind })}
                  />
                ))}
              </Shelf>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
