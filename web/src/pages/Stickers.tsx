import { useState } from 'react'
import { Smile, Trash2 } from 'lucide-react'

import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { Segmented } from '@/components/Segmented'
import { StickerAdd } from '@/components/stickers/StickerAdd'
import { StickerCard } from '@/components/stickers/StickerCard'
import { STATUS } from '@/components/stickers/shared'
import { StickerSettingsPanel } from '@/components/stickers/StickerSettingsPanel'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type Bot, type StickerList, type StickerStatus } from '@/lib/api'
import { useBots, useLive, useProviders } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'

const PAGE = 60
const FILTERS: (StickerStatus | 'all')[] = ['all', 'ready', 'pending', 'unsuitable', 'failed']

/** 表情包: what the bots collected from the groups, how each was described, and the collection's settings. */
export function Stickers() {
  const { stickersVersion } = useLive()
  const providers = useProviders()
  const bots = useBots<Bot[]>('/api/bots')
  const [status, setStatus] = useState<StickerStatus | 'all'>('all')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const [selected, setSelected] = useState<Set<number>>(() => new Set())
  const [problem, setProblem] = useState('')
  const params = new URLSearchParams({ limit: String(limit) })
  if (status !== 'all') params.set('status', status)
  if (query.trim()) params.set('q', query.trim())
  const list = useLiveData<StickerList>(`/api/stickers?${params}`, [stickersVersion])

  if (list.missing && !list.data) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还没有表情包功能。重启 nest 后再打开这一页。
      </p>
    )
  }
  if (!list.data) return <FirstLoad error={list.error} what="表情包" />
  const data = list.data
  const shown = new Set(data.items.map((s) => s.id))
  const chosen = [...selected].filter((id) => shown.has(id))
  const total = Object.values(data.counts).reduce((a, b) => a + (b ?? 0), 0)

  const removeChosen = async () => {
    if (!window.confirm(`删掉选中的 ${chosen.length} 张表情？删了不能恢复。`)) return
    setProblem('')
    try {
      await sendJson('POST', '/api/stickers/delete', { ids: chosen })
      setSelected(new Set())
      list.reload()
    } catch (e) {
      setProblem((e as Error).message)
    }
  }

  const ownChosen = async (botId: string | null) => {
    setProblem('')
    try {
      await sendJson('POST', '/api/stickers/owner', { ids: chosen, bot_id: botId })
      setSelected(new Set())
      list.reload()
    } catch (e) {
      setProblem((e as Error).message)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        bot 在群里看到的表情包会收藏在这里，也可以自己加或者让模型画，都由看图模型写一句描述。Bot 页每个群的「发表情」打开后，bot
        回复时可以挑一张发出去。每张表情可以设成某个 bot 专用，别的 bot 就不会发它。
      </p>
      <StaleNotice error={list.error} />
      <StickerSettingsPanel data={data} providers={providers.data} reload={list.reload} />
      <StickerAdd bots={bots.data ?? []} canDraw={data.can_draw} reload={list.reload} />

      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="按状态筛选"
          options={FILTERS.map((f) => ({
            value: f,
            label: `${f === 'all' ? '全部' : STATUS[f]} ${f === 'all' ? total : (data.counts[f] ?? 0)}`,
          }))}
          value={status}
          onChange={(next) => {
            setStatus(next)
            setLimit(PAGE)
          }}
        />
        <Input
          className="w-48"
          placeholder="搜描述、字或标签"
          aria-label="搜表情"
          value={query}
          maxLength={40}
          onChange={(e) => {
            setQuery(e.target.value)
            setLimit(PAGE)
          }}
        />
        {chosen.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <NativeSelect
              aria-label="设置选中表情谁能发"
              value="pick"
              onChange={(e) => void ownChosen(e.target.value || null)}
            >
              <option value="pick" disabled>
                选中的 {chosen.length} 张设成…
              </option>
              <option value="">所有 bot 都能发</option>
              {(bots.data ?? []).map((bot) => (
                <option key={bot.id} value={bot.id}>
                  {bot.name}专用
                </option>
              ))}
            </NativeSelect>
            <Button size="sm" variant="destructive" onClick={() => void removeChosen()}>
              <Trash2 />
              删掉选中的 {chosen.length} 张
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              取消选择
            </Button>
          </div>
        )}
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}

      {data.items.length === 0 ? (
        <section className="flex flex-col items-center rounded-2xl border bg-card px-6 py-12 text-center">
          <span aria-hidden className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
            <Smile className="size-5" />
          </span>
          <h2 className="mt-4 text-base font-semibold tracking-tight">
            {total === 0 ? '还没有收藏表情' : '没有符合条件的表情'}
          </h2>
          {total === 0 && (
            <p className="mt-1.5 max-w-md text-sm text-muted-foreground">
              在上面打开一个群的收藏，群友发表情包时就会收进来。
            </p>
          )}
        </section>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {data.items.map((sticker) => (
            <StickerCard
              key={sticker.id}
              sticker={sticker}
              bots={bots.data ?? []}
              selected={selected.has(sticker.id)}
              onSelect={(next) =>
                setSelected((current) => {
                  const copy = new Set(current)
                  if (next) copy.add(sticker.id)
                  else copy.delete(sticker.id)
                  return copy
                })
              }
              reload={list.reload}
            />
          ))}
        </ul>
      )}
      {data.total > data.items.length && limit < 1000 && (
        <Button variant="outline" className="self-center" onClick={() => setLimit((n) => n + PAGE)}>
          再显示 {Math.min(PAGE, data.total - data.items.length)} 张（共 {data.total} 张）
        </Button>
      )}
    </div>
  )
}
