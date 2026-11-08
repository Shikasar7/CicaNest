import { useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Radar } from 'lucide-react'

import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { WatchCard } from '@/components/watches/WatchCard'
import { WatchForm } from '@/components/watches/WatchForm'
import { EMPTY_FORM } from '@/components/watches/shared'
import { Button } from '@/components/ui/button'
import type { Bot, WatchView } from '@/lib/api'
import { useDraft, useEditFocus } from '@/lib/drafts'
import { useBots, useLive } from '@/lib/live'
import { useWatches } from '@/lib/watches'

const byName = (a: WatchView, b: WatchView) => (a.name < b.name ? -1 : a.name > b.name ? 1 : a.id - b.id)

/**
 * Writes that just landed, shown over the list they were made on until the refetch brings the list back (or
 * anything else replaces it), so a saved card does not flash its old values, a new one appears at once and a
 * deleted one goes at once.
 */
type Pending = { over: WatchView[] | null; upserts: WatchView[]; removed: number[] }

function usePendingList(data: WatchView[] | null) {
  const [pending, setPending] = useState<Pending | null>(null)
  // The list as it is when a write lands (the write's callback may come from an earlier render).
  const current = useRef(data)
  useEffect(() => {
    current.current = data
  }, [data])
  const list = useMemo(() => {
    const rows = data ?? []
    if (!pending || pending.over !== data) return rows
    const changed = new Set([...pending.removed, ...pending.upserts.map((w) => w.id)])
    const upserts = pending.upserts.filter((w) => !pending.removed.includes(w.id))
    return [...rows.filter((w) => !changed.has(w.id)), ...upserts].sort(byName)
  }, [data, pending])
  const add = (change: Partial<Pick<Pending, 'upserts' | 'removed'>>) =>
    setPending((p) => {
      const base = p && p.over === current.current ? p : { over: current.current, upserts: [], removed: [] }
      return {
        over: base.over,
        upserts: [...base.upserts.filter((w) => !change.upserts?.some((u) => u.id === w.id)), ...(change.upserts ?? [])],
        removed: [...base.removed, ...(change.removed ?? [])],
      }
    })
  return {
    list,
    saved: (view: WatchView) => add({ upserts: [view] }),
    removed: (id: number) => add({ removed: [id] }),
  }
}

export function Watches() {
  const watches = useWatches()
  const bots = useBots<Bot[]>('/api/bots')
  const { accounts } = useLive()
  const creating = useDraft(EMPTY_FORM)
  const { editButton: addButton } = useEditFocus(creating.editing)
  const { list, saved, removed } = usePendingList(watches.data)
  // The watch added last, so its card can offer 立即检查.
  const [addedId, setAddedId] = useState<number | null>(null)
  // Twice a minute, so "3 分钟前" and "马上检查" stay current (local timer, no network).
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 30_000)
    return () => clearInterval(timer)
  }, [])

  if (watches.missing && !watches.data) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还没有监控功能。重启 nest 后再打开这一页。
      </p>
    )
  }
  if (!watches.data) return <FirstLoad error={watches.error} what="监控" />

  const online = (id: string) => accounts.find((a) => a.id === id)?.status.state === 'connected'
  const empty = list.length === 0 && !creating.editing

  return (
    <div className="flex flex-col gap-4">
      {!empty && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            定时查看订阅、网页和价格，有新内容时让 bot 发一条短消息到 QQ。新加的监控先试运行，不会真的发出去。
          </p>
          {!creating.editing && (
            <Button ref={addButton} onClick={creating.start}>
              <Plus />
              添加监控
            </Button>
          )}
        </div>
      )}
      <StaleNotice error={watches.error} />
      {creating.editing && (
        <section
          aria-labelledby="add-watch"
          className="animate-in fade-in-0 slide-in-from-top-2 rounded-2xl border bg-card p-5 shadow-[var(--shadow-lift)] duration-300"
        >
          <h2 id="add-watch" className="text-base font-semibold tracking-tight">
            添加监控
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">填好要看的地址，再选哪个 bot 推送到哪里。</p>
          <div className="mt-5">
            <WatchForm
              watch={null}
              draft={creating}
              bots={bots.data}
              onSaved={(view) => {
                setAddedId(view.id)
                saved(view)
                watches.reload()
              }}
            />
          </div>
        </section>
      )}
      {empty && (
        <section className="flex flex-col items-center rounded-2xl border bg-card px-6 py-12 text-center">
          <span aria-hidden className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
            <Radar className="size-5" />
          </span>
          <h2 className="mt-4 text-base font-semibold tracking-tight">还没有监控</h2>
          <p className="mt-1.5 max-w-md text-sm text-muted-foreground">
            监控会定时查看你关心的订阅、网页或商品价格，有新内容时让 bot 发一条短消息到群里或私聊。
          </p>
          <Button ref={addButton} className="mt-5" onClick={creating.start}>
            <Plus />
            添加监控
          </Button>
        </section>
      )}
      {list.map((watch) => (
        <WatchCard
          key={watch.id}
          watch={watch}
          bot={bots.data?.find((b) => b.id === watch.bot_id)}
          bots={bots.data}
          online={online(watch.bot_id)}
          now={now}
          justAdded={addedId === watch.id}
          onSaved={(view) => {
            saved(view)
            watches.reload()
          }}
          onDeleted={(id) => {
            removed(id)
            watches.reload()
          }}
          reload={watches.reload}
        />
      ))}
    </div>
  )
}
