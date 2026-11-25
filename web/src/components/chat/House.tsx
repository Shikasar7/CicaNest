import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Hand } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { PetSprite, usePetSprite } from '@/components/chat/PetSprite'
import { RoomScene } from '@/components/chat/RoomScene'
import { DOOR, FIGURE, MAX_SHOWN, fit, seat, wander, walkMs, type Daylight, type Spot } from '@/lib/home-stage'
import type { LifePerson } from '@/lib/life'
import { POKED, POKED_ASLEEP, POKE_MS } from '@/lib/poke'
import { cn } from '@/lib/utils'

/** The house of the 小窝: cut open like a doll's house, the companions in it. Who is where is
 * lib/home-stage.ts; this only draws it, on the line drawings. */

const ASLEEP = '睡着了'
const WANDER_MS = [12_000, 30_000]
const ACT_MS = [3_500, 8_000]
const TALK_MS = 6_000
const SETTLE_MS = 1_500
// What she does now and then while she stands there, if her pet has it, and for how long.
const ACTS = [
  ['blink', 260],
  ['blink', 260],
  ['blink', 260],
  ['smile', 1800],
  ['think', 2400],
  ['stretch', 1400],
  ['yawn', 1400],
] as const

const WALLS: Record<Daylight, string> = {
  morning: 'bg-amber-50 dark:bg-[color-mix(in_oklch,var(--color-amber-300)_9%,var(--card))]',
  day: 'bg-sky-50 dark:bg-[color-mix(in_oklch,var(--color-sky-300)_8%,var(--card))]',
  evening: 'bg-orange-100/80 dark:bg-[color-mix(in_oklch,var(--color-orange-400)_12%,var(--card))]',
  night: 'bg-indigo-100/70 dark:bg-[color-mix(in_oklch,var(--color-indigo-400)_9%,var(--card))]',
}
// The lamp of each room, in its units (RoomScene); the balcony has the moon.
const LAMPS: Record<string, [number, number]> = { 客厅: [278, 60], 厨房: [177, 48], 书房: [257, 84], 卧室: [271, 114] }

const still = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches
const between = ([least, most]: readonly number[]) => least + Math.random() * (most - least)

function useSize(): [(el: HTMLElement | null) => void, { width: number; height: number }] {
  const [size, setSize] = useState({ width: 0, height: 0 })
  const watch = useRef<ResizeObserver | null>(null)
  const ref = useCallback((el: HTMLElement | null) => {
    watch.current?.disconnect()
    watch.current = null
    if (!el) return
    const measure = () => setSize({ width: el.clientWidth, height: el.clientHeight })
    measure()
    watch.current = new ResizeObserver(measure)
    watch.current.observe(el)
  }, [])
  return [ref, size]
}

/** She is poked: what she says to it, for a moment. */
function usePoke(lines: string[], asleep: boolean): [string, () => void] {
  const [said, setSaid] = useState('')
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const poke = useCallback(() => {
    const pool = asleep ? POKED_ASLEEP : lines.length ? lines : POKED
    setSaid(pool[Math.floor(Math.random() * pool.length)])
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setSaid(''), POKE_MS)
  }, [lines, asleep])
  return [said, poke]
}

/** Where she is now on her way to `spot`: she walks there (from the door, when she just came into the room). */
function useWalk(spot: Spot, comesIn: boolean): { here: Spot; ms: number; back: boolean } {
  const [walk, setWalk] = useState(() => ({ here: comesIn && !still() ? DOOR : spot, ms: 0, back: false }))
  const { here } = walk
  useEffect(() => {
    if (here === spot) return
    // A moment later: she is drawn where she comes from before she goes.
    const timer = window.setTimeout(
      () => setWalk({ here: spot, ms: still() ? 0 : walkMs(here, spot), back: spot.x < here.x }),
      40,
    )
    return () => window.clearTimeout(timer)
  }, [here, spot])
  useEffect(() => {
    if (!walk.ms) return
    const timer = window.setTimeout(() => setWalk((w) => ({ ...w, ms: 0 })), walk.ms)
    return () => window.clearTimeout(timer)
  }, [walk.ms, here])
  return walk
}

/** A small thing she does now and then while nothing else goes on. */
function useAct(free: boolean, has: (action: string) => boolean): string {
  const [act, setAct] = useState('')
  useEffect(() => {
    if (!free || still()) return
    let timer = 0
    const wait = () => {
      timer = window.setTimeout(() => {
        const [name, ms] = ACTS[Math.floor(Math.random() * ACTS.length)]
        if (document.hidden || !has(name)) return wait()
        setAct(name)
        timer = window.setTimeout(() => {
          setAct('')
          wait()
        }, ms)
      }, between(ACT_MS))
    }
    wait()
    return () => {
      window.clearTimeout(timer)
      setAct('')
    }
  }, [free, has])
  return act
}

const NO_LINES: string[] = []

function Resident({
  person,
  spot,
  box,
  comesIn,
  talks,
  onPick,
}: {
  person: LifePerson
  spot: Spot
  box: { width: number; height: number }
  comesIn: boolean
  talks: boolean
  onPick: (id: string) => void
}) {
  const asleep = person.now?.doing === ASLEEP
  const sprite = usePetSprite(person.bot_id)
  const [said, poke] = usePoke(sprite?.poked ?? NO_LINES, asleep)
  const { here, ms, back } = useWalk(spot, comesIn)
  const walking = ms > 0
  const has = useCallback((action: string) => Boolean(sprite?.actions[action]), [sprite])
  const standing = here.pose === 'stand' && !walking && !said && !asleep
  const act = useAct(standing, has)
  const action = said && !asleep
    ? has('pout') ? 'pout' : 'wave'
    : walking
      ? 'walk'
      : asleep || here.pose === 'lie'
        ? 'sleep'
        : here.pose === 'sit'
          ? 'sit'
          : act || 'idle'
  const place = fit(box.width, box.height)
  const left = place.left(here.x)
  const height = FIGURE * place.scale
  // Her words keep inside the room.
  const side = left < box.width * 0.3 ? 'left-0' : left > box.width * 0.7 ? 'right-0' : 'left-1/2 -translate-x-1/2'
  const words = said || (talks && !walking ? person.now?.doing : '')
  return (
    <div
      className={cn('group absolute hover:!z-40 focus-within:!z-40', comesIn && 'animate-in fade-in duration-300')}
      style={{
        left,
        top: place.top(here.y),
        zIndex: 10 + Math.round(here.y / 10),
        transition: walking ? `left ${ms}ms linear, top ${ms}ms linear` : undefined,
      }}
    >
      <button
        type="button"
        onClick={() => onPick(person.bot_id)}
        title={person.now ? `${person.name}：${person.now.doing}` : person.name}
        aria-label={person.now ? `${person.name}：${person.now.doing}` : person.name}
        className="absolute bottom-0 left-0 flex -translate-x-1/2 items-end justify-center rounded-2xl outline-none focus-visible:ring-2 focus-visible:ring-ring"
        style={{ width: height * 0.8, height: height * (here.pose === 'stand' || walking ? 1 : 0.88) }}
      >
        <span aria-hidden className="absolute -bottom-[3px] h-[7px] w-3/5 rounded-[50%] bg-foreground/15 blur-[1.5px]" />
        <span
          className={cn(
            'relative flex origin-bottom items-end justify-center',
            !walking && !said && 'home-breath',
            said && !asleep && 'animate-bounce motion-reduce:animate-none',
          )}
        >
          {sprite ? (
            <PetSprite sprite={sprite} action={action} height={height} flip={walking && back} />
          ) : (
            <BotGlyph name={person.name} id={person.bot_id} size="list" className={cn('shadow-md', asleep && 'opacity-70')} />
          )}
          {asleep && <span className="absolute top-[22%] right-[18%] text-xs font-bold text-indigo-400">z z</span>}
        </span>
      </button>
      <div
        className="pointer-events-none absolute left-0 flex w-0 flex-col items-center"
        style={{ bottom: height * (here.pose === 'stand' || walking ? 1.02 : 0.9) }}
      >
        <div className="relative h-0 w-0">
          <div className={cn('absolute bottom-0 flex w-max max-w-40 flex-col gap-0.5', side, side === 'right-0' ? 'items-end' : side === 'left-0' ? 'items-start' : 'items-center')} style={side === 'left-0' ? { left: -height * 0.35 } : side === 'right-0' ? { right: -height * 0.35 } : undefined}>
            {words ? (
              <span
                role={said ? 'status' : undefined}
                className="rounded-xl border bg-card px-2 py-1 text-[11px] leading-snug shadow-sm animate-in fade-in zoom-in-95 duration-200"
              >
                <span className="line-clamp-3">
                  <b className="font-semibold">{person.name}</b> <span className="text-muted-foreground">{words}</span>
                </span>
              </span>
            ) : (
              <span className="rounded-full bg-card/85 px-1.5 text-[11px] leading-4 font-medium shadow-sm">{person.name}</span>
            )}
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={poke}
        aria-label={`戳一下${person.name}`}
        title="戳一下"
        className="absolute z-10 grid size-6 place-items-center rounded-full border bg-card text-muted-foreground opacity-0 shadow-sm transition-opacity group-hover:opacity-100 hover:text-primary focus-visible:opacity-100 pointer-coarse:opacity-70"
        style={{ left: height * 0.26, bottom: height * 0.4 }}
      >
        <Hand className="size-3" />
      </button>
    </div>
  )
}

/** Whose turn it is to have her bubble open: one at a time in a room. */
function useTurn(count: number): number {
  const [turn, setTurn] = useState(0)
  useEffect(() => {
    if (count < 2) return
    const timer = window.setInterval(() => setTurn((n) => n + 1), TALK_MS)
    return () => window.clearInterval(timer)
  }, [count])
  return count ? turn % count : 0
}

function Room({
  name,
  people,
  light,
  settled,
  onPick,
}: {
  name: string
  people: LifePerson[]
  light: Daylight
  settled: boolean
  onPick: (id: string) => void
}) {
  const [ref, box] = useSize()
  const [moved, setMoved] = useState<Record<string, Spot>>({})
  const who = people.map((p) => `${p.bot_id} ${p.now?.pose ?? 'stand'}`).join('|')
  const places = useMemo(
    () => seat(name, people.map((p) => ({ id: p.bot_id, pose: p.now?.pose ?? 'stand' })), moved),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `who` is what it depends on of `people`
    [name, who, moved],
  )
  const shown = people.filter((p) => places[p.bot_id])
  const more = people.slice(MAX_SHOWN)
  const turn = useTurn(shown.length)
  useEffect(() => {
    const ids = Object.keys(places)
    if (!ids.length || still()) return
    const timer = window.setTimeout(() => {
      const id = ids[Math.floor(Math.random() * ids.length)]
      const to = document.hidden ? null : wander(name, places, id, Math.random)
      // A new object either way: the next time is planned.
      setMoved(to ? { ...places, [id]: to } : { ...places })
    }, between(WANDER_MS))
    return () => window.clearTimeout(timer)
  }, [name, places])
  const asleep = people.length > 0 && people.every((p) => p.now?.doing === ASLEEP)
  const dark = light === 'night' || light === 'evening'
  const lit = dark && people.length > 0 && !asleep
  const lamp = LAMPS[name.endsWith('卧室') ? '卧室' : name]
  const place = fit(box.width, box.height)
  return (
    <div
      ref={ref}
      className={cn('relative min-w-0 overflow-hidden transition-[flex-grow] duration-500', WALLS[light])}
      style={{ flex: `${people.length ? 1.7 : 1} 1 0` }}
    >
      <RoomScene
        room={name}
        night={light === 'night'}
        className={people.length ? 'text-foreground/30' : 'text-foreground/12'}
      />
      {box.height > 0 &&
        shown.map((p, n) => (
          <Resident
            key={p.bot_id}
            person={p}
            spot={places[p.bot_id]}
            box={box}
            comesIn={settled && p.now?.doing !== ASLEEP}
            talks={n === turn && p.now?.doing !== ASLEEP}
            onPick={onPick}
          />
        ))}
      {/* The light: what is dark at night, and the lamp where someone is up. */}
      <span
        aria-hidden
        className={cn(
          'pointer-events-none absolute inset-0 z-50 transition-colors duration-1000',
          asleep
            ? 'bg-indigo-950/30 dark:bg-black/45'
            : light === 'night' && !lit
              ? 'bg-indigo-950/15 dark:bg-black/35'
              : '',
        )}
      />
      {lit && lamp && box.height > 0 && (
        <span
          aria-hidden
          className="pointer-events-none absolute z-[5] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(closest-side,color-mix(in_oklch,var(--color-amber-300)_55%,transparent),transparent)] dark:bg-[radial-gradient(closest-side,color-mix(in_oklch,var(--color-amber-300)_30%,transparent),transparent)]"
          style={{
            left: place.x(lamp[0]),
            top: place.top(lamp[1]),
            width: 190 * place.scale,
            height: 190 * place.scale,
          }}
        />
      )}
      <span className="absolute top-2 left-2 z-[60] rounded-md bg-card/90 px-1.5 py-0.5 text-[11px] font-medium shadow-sm">
        {name}
      </span>
      {more.length > 0 && (
        <span className="absolute top-2 right-2 z-[60] flex items-center gap-1 rounded-full bg-card/90 py-0.5 pr-1 pl-2 text-[11px] shadow-sm">
          还有
          {more.map((p) => (
            <button
              key={p.bot_id}
              type="button"
              onClick={() => onPick(p.bot_id)}
              title={p.now ? `${p.name}：${p.now.doing}` : p.name}
              aria-label={p.name}
            >
              <BotGlyph name={p.name} id={p.bot_id} size="sm" />
            </button>
          ))}
        </span>
      )}
    </div>
  )
}

/** The landing: what is upstairs beside the bedrooms. */
function Stairs({ light }: { light: Daylight }) {
  return (
    <div aria-hidden className={cn('relative min-w-0 overflow-hidden', WALLS[light])} style={{ flex: '0.6 1 0' }}>
      <RoomScene room="楼梯" className="text-foreground/15" />
      {light === 'night' && <span className="absolute inset-0 bg-indigo-950/15 dark:bg-black/35" />}
    </div>
  )
}

function Floor({ height, width, children }: { height: number; width?: number; children: ReactNode }) {
  return (
    <div className="mx-auto w-full" style={{ maxWidth: width }}>
      <div
        className="flex divide-x-[3px] divide-border border-x-[3px] border-border transition-[height] duration-500"
        style={{ height }}
      >
        {children}
      </div>
      <div className="h-2 rounded-[2px] bg-border" />
    </div>
  )
}

function Roof({ width }: { width?: number }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 400 46"
      preserveAspectRatio="none"
      className="mx-auto block h-10 w-full text-border"
      style={{ maxWidth: width === undefined ? undefined : width + 28 }}
    >
      <path d="M318 4h22v26h-22z" fill="currentColor" opacity=".75" />
      <path d="M200 3 398 45H2z" fill="color-mix(in oklab, currentColor 45%, var(--card))" />
      <path d="M200 3 398 45H2z" fill="none" stroke="currentColor" strokeWidth="3" vectorEffect="non-scaling-stroke" />
      <circle cx="200" cy="27" r="7" fill="var(--card)" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function rows<T>(items: T[], size: number): T[][] {
  return Array.from({ length: Math.ceil(items.length / size) }, (_, n) => items.slice(n * size, (n + 1) * size))
}

export function House({
  residents,
  rooms,
  light,
  onPick,
}: {
  residents: LifePerson[]
  rooms: string[]
  light: Daylight
  onPick: (id: string) => void
}) {
  const [ref, { width }] = useSize()
  // Who turns up in a room later comes in by the door.
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(true), SETTLE_MS)
    return () => window.clearTimeout(timer)
  }, [])
  const narrow = width > 0 && width < 500
  const across = narrow ? 2 : 4
  const height = Math.round(narrow ? Math.min(230, Math.max(130, width * 0.38)) : Math.min(340, Math.max(160, width * 0.235)))
  const upstairs = rows(residents, across)
  // A floor with few bedrooms is a smaller one, with the roof over it.
  const wide = (count: number) => (count >= (narrow ? 2 : 3) ? undefined : Math.round((count + 0.6) * height * 1.75))
  const top = upstairs.length === 1 ? wide(residents.length) : undefined
  return (
    <div ref={ref} className="pt-1">
      {width > 0 && (
        <>
          {upstairs.length > 0 && <Roof width={top} />}
          {upstairs.map((people, n) => (
            <Floor
              key={n}
              height={people.some((p) => p.now?.room === '卧室') ? height : Math.round(height * 0.55)}
              width={upstairs.length === 1 ? top : undefined}
            >
              {people.map((p) => (
                <Room
                  key={p.bot_id}
                  name={`${p.name}的卧室`}
                  people={p.now?.room === '卧室' ? [p] : []}
                  light={light}
                  settled={settled}
                  onPick={onPick}
                />
              ))}
              {people.length < across && <Stairs light={light} />}
            </Floor>
          ))}
          {rows(rooms, across).map((names) => (
            <Floor key={names[0]} height={height}>
              {names.map((room) => (
                <Room
                  key={room}
                  name={room}
                  people={residents.filter((p) => p.now?.room === room)}
                  light={light}
                  settled={settled}
                  onPick={onPick}
                />
              ))}
            </Floor>
          ))}
        </>
      )}
    </div>
  )
}
