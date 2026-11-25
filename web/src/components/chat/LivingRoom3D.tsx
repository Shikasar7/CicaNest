import { useEffect, useRef, useState } from 'react'
import { UsersRound } from 'lucide-react'

import { petSprite } from '@/components/chat/PetSprite'
import type { PetSprite } from '@/lib/desktop'
import { LIVING_ROOM } from '@/lib/dorm'
import { DormScene } from '@/lib/dorm-scene'
import type { Daylight, Pose } from '@/lib/home-stage'
import type { LifePerson } from '@/lib/life'
import { POKED, POKE_MS } from '@/lib/poke'
import { cn } from '@/lib/utils'

/** 3D 小窝, a first look: the living room. Loaded only when it is opened. */

const ASLEEP = '睡着了'
const TALK_MS = 6_000

const BEHIND: Record<Daylight, string> = {
  morning: 'from-[#fff3e0] to-[#f6e3cf] dark:from-[#3a342f] dark:to-[#2b2726]',
  day: 'from-[#eef6fd] to-[#e3edf4] dark:from-[#2c3238] dark:to-[#24282d]',
  evening: 'from-[#ffe4d0] to-[#ecd0d2] dark:from-[#3d302f] dark:to-[#2c2529]',
  night: 'from-[#c9cdea] to-[#b4b6d8] dark:from-[#262a44] dark:to-[#1c1e30]',
}

/** As her day has it; called in, she sits down. Whose pet has no picture of sitting stands. */
function poseOf(person: LifePerson, sprite: PetSprite | null): Pose {
  const pose = person.now?.room === LIVING_ROOM.name ? (person.now.pose ?? 'stand') : 'sit'
  return sprite && pose !== 'stand' && !sprite.actions.sit ? 'stand' : pose
}

export default function LivingRoom3D({
  residents,
  light,
  onPick,
}: {
  residents: LifePerson[]
  light: Daylight
  onPick: (id: string) => void
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const scene = useRef<DormScene | null>(null)
  const labels = useRef(new Map<string, HTMLElement>())
  const [failed, setFailed] = useState(false)
  const [sprites, setSprites] = useState<Record<string, PetSprite | null>>({})
  const [called, setCalled] = useState<boolean | null>(null)
  const [said, setSaid] = useState<{ id: string; words: string } | null>(null)
  const [turn, setTurn] = useState(0)

  const inside = residents.filter((p) => p.now?.room === LIVING_ROOM.name && p.now.doing !== ASLEEP)
  // Nobody in the living room just now: everyone is called in, so that there is something to see.
  const all = called ?? inside.length === 0
  const shown = all ? residents : inside
  const key = shown.map((p) => `${p.bot_id} ${p.now?.room} ${p.now?.pose}`).join('|')
  const latest = useRef({ shown, sprites })
  useEffect(() => {
    latest.current = { shown, sprites }
  })

  useEffect(() => {
    if (!canvas.current) return
    let made: DormScene
    try {
      made = new DormScene(canvas.current, LIVING_ROOM, {
        draw: (id, x, y) => {
          const label = labels.current.get(id)
          if (!label) return
          label.style.visibility = Number.isFinite(x) ? 'visible' : 'hidden'
          if (Number.isFinite(x)) label.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`
        },
        pick: (id) => {
          made.poke(id)
          const lines = latest.current.sprites[id]?.poked
          const pool = lines?.length ? lines : POKED
          setSaid({ id, words: pool[Math.floor(Math.random() * pool.length)] })
        },
      })
    } catch {
      setFailed(true) // no WebGL here
      return
    }
    scene.current = made
    return () => {
      scene.current = null
      made.dispose()
    }
  }, [])

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

  useEffect(() => scene.current?.setLight(light), [light])

  useEffect(() => {
    scene.current?.setGuests(
      latest.current.shown
        .filter((p) => p.bot_id in sprites)
        .map((p) => ({
          id: p.bot_id,
          name: p.name,
          pose: poseOf(p, sprites[p.bot_id]),
          asleep: false,
          sprite: sprites[p.bot_id],
        })),
    )
  }, [key, sprites])

  useEffect(() => {
    if (!said) return
    const timer = window.setTimeout(() => setSaid(null), POKE_MS)
    return () => window.clearTimeout(timer)
  }, [said])

  useEffect(() => {
    const timer = window.setInterval(() => setTurn((n) => n + 1), TALK_MS)
    return () => window.clearInterval(timer)
  }, [])

  if (failed) {
    return (
      <p className="m-auto rounded-2xl border border-dashed p-6 text-center text-sm text-muted-foreground">
        这台电脑画不了 3D，先看线稿的吧。
      </p>
    )
  }
  return (
    <div className={cn('relative min-h-80 flex-1 overflow-hidden rounded-3xl border bg-linear-to-b', BEHIND[light])}>
      <canvas ref={canvas} className="absolute inset-0 size-full cursor-grab touch-none active:cursor-grabbing" />
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {shown.map((p, n) => {
          const doing = p.now?.room === LIVING_ROOM.name && p.now.doing !== ASLEEP ? p.now.doing : '来客厅看看'
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
      <div className="pointer-events-none absolute inset-x-3 bottom-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-card/80 px-3 py-1 text-muted-foreground">
          点小人戳一下，点名字看她的今天；灯和电视能开关；拖动转一转，滚轮放大
        </span>
        <button
          type="button"
          onClick={() => setCalled(!all)}
          aria-pressed={all}
          className={cn(
            'pointer-events-auto ml-auto flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 hover:text-primary',
            all && 'border-primary/40 text-primary',
          )}
        >
          <UsersRound className="size-3.5" />
          {all ? '大家都在（只是看看）' : '叫大家都来看看'}
        </button>
      </div>
    </div>
  )
}
