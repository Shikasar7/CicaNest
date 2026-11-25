import { lazy, Suspense, useEffect, useState } from 'react'
import { LoaderCircle, MessageCircleHeart, RefreshCw, Settings2, Sofa, Sparkles, Trash2, X } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { HomeSettings } from '@/components/chat/HomeSettings'
import { House } from '@/components/chat/House'
import { Switch } from '@/components/ui/switch'
import { sendJson } from '@/lib/api'
import { daylightOf } from '@/lib/home-stage'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import type { LifeBlock, LifePerson, LifeProfile, LifeView } from '@/lib/life'
import { cn } from '@/lib/utils'

/** 小窝: a little house where the companions have days of their own. Drawn with lines and the
 * theme's colours (no pictures); the light follows the time of day. */

const SHARED = ['客厅', '厨房', '书房', '阳台']
const MINUTE = 60_000
const VIEW_KEY = 'nest-home-view'
const LivingRoom3D = lazy(() => import('@/components/chat/LivingRoom3D'))

/** The line drawings, or the 3D living room (a first look). */
function useView(): ['lines' | '3d', (view: 'lines' | '3d') => void] {
  const [view, setView] = useState<'lines' | '3d'>(() => (localStorage.getItem(VIEW_KEY) === '3d' ? '3d' : 'lines'))
  return [
    view,
    (next) => {
      localStorage.setItem(VIEW_KEY, next)
      setView(next)
    },
  ]
}

/** The time, a minute at a time. */
function useMinute(): number {
  const [minute, setMinute] = useState(() => Math.floor(Date.now() / MINUTE))
  useEffect(() => {
    const timer = setInterval(() => setMinute(Math.floor(Date.now() / MINUTE)), 10_000)
    return () => clearInterval(timer)
  }, [])
  return minute
}

function Timeline({ blocks, now }: { blocks: LifeBlock[]; now: LifeBlock | null }) {
  return (
    <ol className="space-y-1.5">
      {blocks.map((b) => {
        const current = now !== null && now.from === b.from && now.doing === b.doing
        return (
          <li
            key={`${b.from}${b.doing}`}
            className={cn(
              'flex gap-3 rounded-xl px-2.5 py-1.5 text-sm',
              current ? 'bg-primary/10 ring-1 ring-primary/30' : 'text-muted-foreground',
            )}
          >
            <span className="w-11 shrink-0 tabular-nums">{b.from}</span>
            <span className="min-w-0">
              <span className={cn(current && 'font-medium text-foreground')}>
                {b.room} · {b.doing}
              </span>
              {b.share && <span className="block text-xs text-primary/80">「{b.share}」</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

function ProfileEditor({ person, onClose }: { person: LifePerson; onClose: () => void }) {
  const [profile, setProfile] = useState<LifeProfile>(person.profile)
  const [busy, setBusy] = useState<'save' | 'draft' | null>(null)
  const [error, setError] = useState('')
  const path = `/api/life/profiles/${encodeURIComponent(person.bot_id)}`
  const run = async (kind: 'save' | 'draft') => {
    setBusy(kind)
    setError('')
    try {
      const saved =
        kind === 'save'
          ? await sendJson<LifeProfile>('PUT', path, { profile })
          : await sendJson<LifeProfile>('POST', `${path}/draft`)
      setProfile(saved)
      if (kind === 'save') onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }
  const field = 'w-full rounded-xl border bg-background px-3 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring'
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">{person.name}的生活设定</h3>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void run('draft')}
          className="flex items-center gap-1 rounded-full border px-3 py-1 text-xs text-muted-foreground hover:text-primary disabled:opacity-50"
        >
          {busy === 'draft' ? <LoaderCircle className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
          让她按人设自己写
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1 text-xs text-muted-foreground">
          几点起
          <input type="time" value={profile.wake} onChange={(e) => setProfile({ ...profile, wake: e.target.value })} className={field} />
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          几点睡
          <input type="time" value={profile.sleep} onChange={(e) => setProfile({ ...profile, sleep: e.target.value })} className={field} />
        </label>
      </div>
      <div className="space-y-1 text-xs text-muted-foreground">
        爱待的房间
        <div className="flex flex-wrap gap-1.5">
          {SHARED.map((room) => {
            const on = profile.rooms.includes(room)
            return (
              <button
                key={room}
                type="button"
                onClick={() =>
                  setProfile({ ...profile, rooms: on ? profile.rooms.filter((r) => r !== room) : [...profile.rooms, room] })
                }
                aria-pressed={on}
                className={cn('rounded-full border px-3 py-1 text-xs', on ? 'border-primary bg-primary/10 text-primary' : '')}
              >
                {room}
              </button>
            )
          })}
        </div>
      </div>
      <label className="block space-y-1 text-xs text-muted-foreground">
        爱好（用逗号隔开）
        <input
          value={profile.hobbies.join('，')}
          onChange={(e) => setProfile({ ...profile, hobbies: e.target.value.split(/[,，、]/).map((x) => x.trim()).filter(Boolean) })}
          className={field}
        />
      </label>
      {(['lately', 'habits', 'friends'] as const).map((key) => (
        <label key={key} className="block space-y-1 text-xs text-muted-foreground">
          {{ lately: '最近在迷的', habits: '小习惯', friends: '和谁要好' }[key]}
          <input value={profile[key]} maxLength={80} onChange={(e) => setProfile({ ...profile, [key]: e.target.value })} className={field} />
        </label>
      ))}
      <label className="block space-y-1 text-xs text-muted-foreground">
        第一次聊天的日子（用来记认识多少天，不填就按 nest 知道的最早一天）
        <input
          type="date"
          value={profile.since ?? ''}
          onChange={(e) => setProfile({ ...profile, since: e.target.value })}
          className={field}
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <Switch checked={profile.home} onCheckedChange={(home) => setProfile({ ...profile, home })} />
        住在小窝里
      </label>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void run('save')}
          className="rounded-full bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground active:scale-[0.98] disabled:opacity-50"
        >
          保存
        </button>
        <button type="button" onClick={onClose} className="rounded-full px-4 py-1.5 text-sm text-muted-foreground hover:text-foreground">
          取消
        </button>
      </div>
      <p className="text-xs text-muted-foreground">改了设定，明天的日程才会按新的排；想马上看效果就「重新排今天」。</p>
    </div>
  )
}

export function Home({ onClose, onOpenChat }: { onClose: () => void; onOpenChat: (botId: string) => void }) {
  const { botsVersion } = useLive()
  const minute = useMinute()
  const { data, error } = useLiveData<LifeView>('/api/life', [botsVersion, minute]) // they go about their day
  const { light, label } = daylightOf(new Date(minute * MINUTE).getHours())
  const [view, setView] = useView()
  const [picked, setPicked] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [settings, setSettings] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const person = data?.people.find((p) => p.bot_id === picked) ?? null
  const residents = data?.people.filter((p) => p.home) ?? []
  const act = async (action: () => Promise<unknown>, done = '') => {
    setBusy(true)
    setMessage('')
    try {
      await action()
      if (done) setMessage(done)
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b px-5 py-3">
        <h2 className="text-base font-semibold">小窝</h2>
        <span className="text-xs text-muted-foreground">
          {data?.day} · {label}
        </span>
        {data?.on && (
          <div className="flex rounded-full border p-0.5 text-xs">
            {(
              [
                ['lines', '线稿'],
                ['3d', '3D 客厅（试）'],
              ] as const
            ).map(([key, name]) => (
              <button
                key={key}
                type="button"
                aria-pressed={view === key}
                onClick={() => setView(key)}
                className={cn(
                  'rounded-full px-2.5 py-0.5',
                  view === key ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {name}
              </button>
            ))}
          </div>
        )}
        <label className="ml-auto flex items-center gap-2 text-sm">
          {busy && <LoaderCircle className="size-4 animate-spin text-muted-foreground" />}
          <Switch
            checked={Boolean(data?.on)}
            disabled={busy || !data}
            onCheckedChange={(on) =>
              void act(() => sendJson('PUT', '/api/life', { on }), on ? '小窝亮灯了：今天的日程排好了。' : '')
            }
          />
          她们的日子
        </label>
        {data?.on && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => sendJson('POST', '/api/life/plan'), '重新排好了。')}
            className="flex items-center gap-1 rounded-full border px-3 py-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className="size-3.5" />
            重新排今天
          </button>
        )}
        <button
          type="button"
          onClick={() => setSettings((v) => !v)}
          aria-pressed={settings}
          aria-label="打扰和作息"
          title="她们多久来找你、免打扰、作息"
          className={cn('grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground', settings && 'bg-muted text-primary')}
        >
          <Settings2 className="size-4" />
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      {(message || error) && <p className="border-b bg-muted/50 px-5 py-2 text-sm">{message || error}</p>}
      {settings ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="mx-auto max-w-2xl">
            <HomeSettings />
          </div>
        </div>
      ) : data && !data.on ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
          <Sofa className="size-14 text-primary/40" />
          <p className="max-w-md text-sm text-muted-foreground">
            打开「她们的日子」，伙伴们就住进小窝了：每天早上一起排好各自的日程，起床、做饭、忙自己的事、偶尔一起玩。
            和你聊天时她知道自己正在干什么，也会偶尔主动来找你：分享今天的小事、饭点问你吃了没、很晚了催你睡、
            好久没聊说想你、认识一百天；群里冷场了会有人起个话头，两个人待在一起也会拉个小群聊几句。你在电脑前时才会来，
            一天几次、几点到几点安静都能在右上角的设置里改（一开始每人每天最多 3 次，大家加起来 6 次，23 点到 8 点安静，
            只有「这么晚还不睡」例外），连着两次没理她当天就不来了。
            每天排日程花一次模型调用，每次主动找你花一次。
          </p>
        </div>
      ) : (
        <div className="relative flex min-h-0 flex-1">
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
            {residents.length === 0 && data && (
              <p className="rounded-2xl border border-dashed p-4 text-center text-sm text-muted-foreground">
                还没有人住进来：会回话、有模型的伙伴才住在小窝里。
              </p>
            )}
            {view === '3d' ? (
              <div className="flex h-[min(38rem,calc(100dvh-11rem))]">
                <Suspense fallback={<LoaderCircle className="m-auto size-6 animate-spin text-muted-foreground" />}>
                  <LivingRoom3D residents={residents} light={light} onPick={setPicked} />
                </Suspense>
              </div>
            ) : (
              <House residents={residents} rooms={data?.rooms ?? SHARED} light={light} onPick={setPicked} />
            )}
            {!!data?.moments.length && (
              <div className="rounded-3xl border bg-muted/20 p-4">
                <h3 className="text-sm font-semibold">她们跟你说过的小事</h3>
                <p className="text-xs text-muted-foreground">排明天的日程时会看这些，免得前后对不上。不想要的可以删。</p>
                <ul className="mt-2 space-y-1">
                  {data.moments.map((m) => (
                    <li key={m.id} className="group flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">{data.people.find((p) => p.bot_id === m.bot_id)?.name}：</span>
                      <span className="min-w-0 flex-1 truncate">{m.text}</span>
                      <button
                        type="button"
                        aria-label="删掉"
                        onClick={() => void act(() => sendJson('DELETE', `/api/life/moments/${m.id}`))}
                        className="opacity-0 group-hover:opacity-100"
                      >
                        <Trash2 className="size-3.5 text-muted-foreground hover:text-destructive" />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          {person && (
            <aside className="w-72 shrink-0 max-md:absolute max-md:inset-0 max-md:z-[70] max-md:w-full space-y-4 overflow-y-auto border-l bg-card p-4 animate-in fade-in slide-in-from-right-2 duration-200 motion-reduce:animate-none">
              <div className="flex items-center gap-3">
                <BotGlyph name={person.name} id={person.bot_id} size="lg" />
                <div className="min-w-0">
                  <div className="font-semibold">{person.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {person.now ? `在${person.now.room}${person.now.doing}` : '不住在小窝里'}
                    {person.now?.mood && ` · ${person.now.mood}`}
                  </div>
                  {person.since && (
                    <div className="text-xs text-muted-foreground">
                      {person.milestone ? `今天${person.milestone} 🎉` : `${person.since} 开始认识的`}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setPicked(null)
                    setEditing(false)
                  }}
                  aria-label="收起"
                  className="ml-auto grid size-7 place-items-center rounded-full text-muted-foreground hover:bg-muted"
                >
                  <X className="size-4" />
                </button>
              </div>
              {editing ? (
                <ProfileEditor key={person.bot_id} person={person} onClose={() => setEditing(false)} />
              ) : (
                <>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => onOpenChat(person.bot_id)}
                      className="flex items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground active:scale-[0.98]"
                    >
                      <MessageCircleHeart className="size-4" />
                      去找她
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(true)}
                      className="rounded-full border px-4 py-1.5 text-sm text-muted-foreground hover:text-foreground"
                    >
                      生活设定
                    </button>
                  </div>
                  {person.day.length > 0 ? (
                    <div>
                      <h3 className="pb-2 text-xs font-medium text-muted-foreground">她的今天</h3>
                      <Timeline blocks={person.day} now={person.now} />
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      {person.home ? '今天的日程还没排。' : '她现在不住在小窝里（不会回话、没有模型，或者你让她搬出去了）。'}
                    </p>
                  )}
                </>
              )}
            </aside>
          )}
        </div>
      )}
    </div>
  )
}
