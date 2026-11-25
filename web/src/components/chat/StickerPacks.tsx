import { useRef, useState, type ReactNode } from 'react'
import { Download, ImageUp, LoaderCircle, Lock, LockOpen, Plus, RefreshCw, Sparkles, Trash2, X } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { Switch } from '@/components/ui/switch'
import { getJson, sendBlob, sendJson } from '@/lib/api'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import type { Sticker } from '@/lib/api'
import { stickerImage, type BotPacks, type Pack, type PacksOverview, type PluginPack, type Slot } from '@/lib/sticker-packs'
import { cn } from '@/lib/utils'

/** 表情包管理: each companion's own sticker packs. nest draws every mood in her likeness (her
 * picture is the reference), one at a time; any picture can be drawn again or replaced by one of yours. */

const MINUTES_PER_PICTURE = 1.5
const BUSY = new Set(['queued', 'drawing'])

function SlotCard({ slot, onDone }: { slot: Slot; onDone: (message?: string) => void }) {
  const [renaming, setRenaming] = useState(false)
  const [mood, setMood] = useState(slot.mood)
  const [working, setWorking] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const busy = BUSY.has(slot.state)

  const act = async (action: () => Promise<unknown>) => {
    setWorking(true)
    try {
      await action()
      onDone()
    } catch (e) {
      onDone((e as Error).message)
    } finally {
      setWorking(false)
    }
  }
  const rename = () => {
    setRenaming(false)
    if (mood.trim() && mood.trim() !== slot.mood)
      void act(() => sendJson('PATCH', `/api/sticker-packs/slots/${slot.id}`, { mood }))
    else setMood(slot.mood)
  }

  return (
    <div className="group flex flex-col gap-2 rounded-2xl border bg-card p-2.5 shadow-sm">
      <div className="flex items-center gap-1">
        {renaming ? (
          <input
            autoFocus
            value={mood}
            maxLength={12}
            onChange={(e) => setMood(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') rename()
              if (e.key === 'Escape') {
                setMood(slot.mood)
                setRenaming(false)
              }
            }}
            aria-label="表情的名字"
            className="min-w-0 flex-1 rounded-md border bg-background px-1.5 py-0.5 text-sm outline-none"
          />
        ) : (
          <button
            type="button"
            onClick={() => setRenaming(true)}
            title="改名字"
            className="min-w-0 flex-1 truncate text-left text-sm font-semibold"
          >
            {slot.mood}
          </button>
        )}
        <button
          type="button"
          disabled={busy || working}
          onClick={() =>
            void act(() => sendJson('PATCH', `/api/sticker-packs/slots/${slot.id}`, { locked: !slot.locked }))
          }
          aria-pressed={Boolean(slot.locked)}
          aria-label={slot.locked ? `「${slot.mood}」锁住了，点一下放开` : `锁住「${slot.mood}」`}
          title={slot.locked ? '锁住了：「全部重画」不动它' : '锁住：「全部重画」时不动这一张'}
          className={cn(
            'grid size-6 place-items-center rounded-full hover:bg-muted disabled:opacity-40',
            slot.locked ? 'text-primary' : 'text-muted-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
          )}
        >
          {slot.locked ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
        </button>
        <button
          type="button"
          disabled={busy || working}
          onClick={() => void act(() => sendJson('DELETE', `/api/sticker-packs/slots/${slot.id}`))}
          aria-label={`去掉「${slot.mood}」`}
          className="grid size-6 place-items-center rounded-full text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-muted hover:text-destructive disabled:opacity-0"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
      <div className="grid aspect-square place-items-center overflow-hidden rounded-xl bg-muted/40">
        {slot.sticker_id !== null && !busy ? (
          <img src={stickerImage(slot.sticker_id)} alt={slot.mood} className="size-full object-contain" />
        ) : slot.state === 'drawing' ? (
          <span className="flex flex-col items-center gap-1.5 text-xs text-muted-foreground">
            <LoaderCircle className="size-5 animate-spin" />
            正在画…
          </span>
        ) : slot.state === 'queued' ? (
          <span className="text-xs text-muted-foreground">排队中</span>
        ) : slot.state === 'failed' ? (
          <span className="px-2 text-center text-xs text-destructive">{slot.error || '没画成'}</span>
        ) : (
          <span className="text-xs text-muted-foreground">还没画</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <button
          type="button"
          disabled={busy || working}
          onClick={() => input.current?.click()}
          className="rounded-full border px-2 py-1 text-xs text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-40"
        >
          上传
        </button>
        <button
          type="button"
          disabled={busy || working}
          onClick={() =>
            void act(() => sendJson('POST', `/api/sticker-packs/${slot.pack_id}/draw`, { slots: [slot.id] }))
          }
          className="rounded-full border px-2 py-1 text-xs text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-40"
        >
          {slot.sticker_id === null ? '画' : '重新画'}
        </button>
      </div>
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (file) void act(() => sendBlob(`/api/sticker-packs/slots/${slot.id}/picture`, file))
        }}
      />
    </div>
  )
}

function AddMood({ packId, onDone }: { packId: number; onDone: (message?: string) => void }) {
  const [mood, setMood] = useState('')
  const add = async () => {
    if (!mood.trim()) return
    try {
      await sendJson('POST', `/api/sticker-packs/${packId}/slots`, { mood })
      setMood('')
      onDone()
    } catch (e) {
      onDone((e as Error).message)
    }
  }
  return (
    <div className="flex flex-col justify-center gap-2 rounded-2xl border border-dashed p-2.5">
      <input
        value={mood}
        maxLength={12}
        onChange={(e) => setMood(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && void add()}
        placeholder="再加一种：吃瓜…"
        aria-label="新的表情"
        className="rounded-md border bg-background px-2 py-1 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      <button
        type="button"
        onClick={() => void add()}
        disabled={!mood.trim()}
        className="flex items-center justify-center gap-1 rounded-full border px-2 py-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
      >
        <Plus className="size-3.5" />
        加上
      </button>
    </div>
  )
}

/** 专属表情包: every picture that is hers (the pack in use and the ones given to her), and whether she
 * sticks to them in the desk chat. */
function OwnStickers({
  name,
  own,
  ownOnly,
  onOwnOnly,
}: {
  name: string
  own: Sticker[]
  ownOnly: boolean
  onOwnOnly: (on: boolean) => void
}) {
  return (
    <section className="mt-8 rounded-3xl border bg-muted/20 p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 className="font-semibold">专属表情包</h3>
        <span className="text-xs text-muted-foreground">{own.length} 张：使用中的这套里画好的，加上专门给{name}的</span>
        <label className="ml-auto flex items-center gap-2 text-sm">
          <Switch checked={ownOnly} onCheckedChange={onOwnOnly} />
          只用专属表情包里的表情
        </label>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {ownOnly
          ? `聊天里${name}只从这些里挑${own.length ? '' : '；现在一张都没有，所以她不会发表情包'}。`
          : `聊天里${name}先从这些里挑，也会用表情包库里的。`}
      </p>
      {own.length > 0 && (
        <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(4rem,1fr))] gap-2">
          {own.map((s) => (
            <span
              key={s.id}
              title={s.description || s.tags.join('、')}
              className="grid aspect-square place-items-center overflow-hidden rounded-xl bg-card p-1 shadow-sm [contain:layout_paint]"
            >
              <img src={stickerImage(s.id)} alt={s.tags[0] ?? '表情'} loading="lazy" decoding="async" className="max-h-full max-w-full object-contain" />
            </span>
          ))}
        </div>
      )}
    </section>
  )
}

/** 「全部生成」: what is missing of every companion's pack, after saying how many. */
function DrawEveryone({ onMessage }: { onMessage: (message: string) => void }) {
  const [asking, setAsking] = useState<{ bots: { name: string; count: number }[]; count: number } | null>(null)
  const run = async (work: () => Promise<void>) => {
    try {
      await work()
    } catch (e) {
      onMessage((e as Error).message)
      setAsking(null)
    }
  }
  if (!asking) {
    return (
      <button
        type="button"
        onClick={() =>
          void run(async () => setAsking(await getJson<{ bots: { name: string; count: number }[]; count: number }>('/api/sticker-packs/missing')))
        }
        className="flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <Sparkles className="size-3.5" />
        全部生成
      </button>
    )
  }
  if (asking.count === 0) {
    return (
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        大家的都画好了（没头像的画不了）。
        <button type="button" onClick={() => setAsking(null)} className="hover:text-foreground">
          知道了
        </button>
      </span>
    )
  }
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs">
      要画 {asking.count} 张（{asking.bots.map((b) => `${b.name} ${b.count}`).join('、')}），大约{' '}
      {Math.max(1, Math.round(asking.count * MINUTES_PER_PICTURE))} 分钟，花能画图的模型的额度。
      <button
        type="button"
        onClick={() =>
          void run(async () => {
            const { queued, minutes } = await sendJson<{ queued: number; minutes: number }>('POST', '/api/sticker-packs/draw-everyone')
            setAsking(null)
            onMessage(`开始画了：${queued} 张，大约 ${minutes} 分钟。`)
          })
        }
        className="rounded-full bg-primary px-3 py-1 font-medium text-primary-foreground"
      >
        开始
      </button>
      <button type="button" onClick={() => setAsking(null)} className="text-muted-foreground hover:text-foreground">
        算了
      </button>
    </span>
  )
}

/** Packs that installed plugins bring, to take for her; and this pack as a plugin to share. */
function Share({ botId, pack, onMessage }: { botId: string; pack: Pack | null; onMessage: (message: string) => void }) {
  const { stickersVersion, botsVersion } = useLive()
  const offered = useLiveData<PluginPack[]>('/api/sticker-packs/from-plugins', [botsVersion, stickersVersion]).data ?? []
  const take = async (p: PluginPack) => {
    try {
      const made = await sendJson<{ skipped: number }>(
        'POST',
        `/api/sticker-packs/bots/${encodeURIComponent(botId)}/from-plugin`,
        { plugin: p.plugin, path: p.path },
      )
      onMessage(`拿过来了：「${p.name}」${made.skipped ? `，有 ${made.skipped} 张用不了` : ''}。`)
    } catch (e) {
      onMessage((e as Error).message)
    }
  }
  if (!offered.length && !pack?.slots.some((s) => s.sticker_id !== null)) return null
  return (
    <section className="mt-6 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      {offered.map((p) => (
        <button
          key={`${p.plugin}/${p.path}`}
          type="button"
          onClick={() => void take(p)}
          className="rounded-full border px-3 py-1 hover:text-foreground"
        >
          用插件「{p.plugin_name}」里的「{p.name}」（{p.count} 张）
        </button>
      ))}
      {pack?.slots.some((s) => s.sticker_id !== null) && (
        <a
          href={`/api/sticker-packs/${pack.id}/plugin`}
          download
          className="flex items-center gap-1 rounded-full border px-3 py-1 hover:text-foreground"
          title="存成一个插件包，别人在「插件」页就能装。里面只有这些图和名字；画的是谁、能不能给别人，你自己掂量"
        >
          <Download className="size-3.5" />
          这套做成插件，分享给别人
        </a>
      )}
    </section>
  )
}

function PackEditor({
  pack,
  overview,
  hasAvatar,
  onMessage,
  footer,
}: {
  pack: Pack
  overview: PacksOverview
  hasAvatar: boolean
  onMessage: (message: string) => void
  footer: ReactNode
}) {
  const [extra, setExtra] = useState(pack.extra)
  const [asking, setAsking] = useState<'missing' | 'all' | null>(null)
  const patch = async (body: Record<string, unknown>) => {
    try {
      await sendJson('PATCH', `/api/sticker-packs/${pack.id}`, body)
    } catch (e) {
      onMessage((e as Error).message)
    }
  }
  const missing = pack.slots.filter((s) => s.sticker_id === null && !BUSY.has(s.state)).length
  const busy = pack.slots.filter((s) => BUSY.has(s.state)).length
  const idle = pack.slots.filter((s) => !BUSY.has(s.state) && !s.locked).length
  const locked = pack.slots.filter((s) => s.locked).length
  const count = asking === 'missing' ? missing : idle
  const draw = async (which: 'missing' | 'all') => {
    setAsking(null)
    try {
      const { queued, minutes } = await sendJson<{ queued: number; minutes: number }>(
        'POST',
        `/api/sticker-packs/${pack.id}/draw`,
        { which },
      )
      onMessage(`开始画了：${queued} 张，大约 ${minutes} 分钟。可以关掉这里，画好的会自己出现。`)
    } catch (e) {
      onMessage((e as Error).message)
    }
  }
  const cannot = !overview.can_draw
    ? '没有能画图的模型：先在「模型」页加一个 Codex（ChatGPT 订阅）模型。'
    : !hasAvatar
      ? '她还没有头像：先在 Bot 页给她设一张，nest 照着头像画。'
      : ''

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b px-5 py-3">
        <select
          value={pack.style}
          onChange={(e) => void patch({ style: e.target.value })}
          aria-label="画风"
          className="rounded-full border bg-primary/10 px-3 py-1.5 text-sm font-medium text-primary outline-none"
        >
          {overview.styles.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <input
          value={extra}
          maxLength={200}
          onChange={(e) => setExtra(e.target.value)}
          onBlur={() => extra !== pack.extra && void patch({ extra })}
          placeholder="额外要求（可以不写）：比如「都戴着猫耳」"
          aria-label="额外要求"
          className="min-w-48 flex-1 rounded-full border bg-background px-4 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button
          type="button"
          disabled={Boolean(cannot) || missing === 0}
          onClick={() => setAsking('missing')}
          className="flex items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground active:scale-[0.98] disabled:opacity-40"
        >
          <Sparkles className="size-4" />
          画还没画的{missing > 0 && `（${missing}）`}
        </button>
        <button
          type="button"
          disabled={Boolean(cannot) || idle === 0}
          onClick={() => setAsking('all')}
          className="flex items-center gap-1.5 rounded-full border border-primary/50 px-4 py-1.5 text-sm text-primary active:scale-[0.98] disabled:opacity-40"
        >
          <RefreshCw className="size-4" />
          全部重画{locked > 0 && `（锁住的 ${locked} 张不动）`}
        </button>
      </div>
      {cannot && <p className="border-b bg-warn/10 px-5 py-2 text-sm text-warn">{cannot}</p>}
      {asking && (
        <div className="flex flex-wrap items-center gap-3 border-b bg-primary/5 px-5 py-2.5 text-sm">
          <span>
            要画 {count} 张，一张一张画，每张大约一分半钟，一共约 {Math.max(1, Math.round(count * MINUTES_PER_PICTURE))}{' '}
            分钟，用的是「模型」页里能画图的模型（花它的额度）。开始吗？
          </span>
          <button
            type="button"
            onClick={() => void draw(asking)}
            className="rounded-full bg-primary px-4 py-1 text-sm font-medium text-primary-foreground"
          >
            开始
          </button>
          <button type="button" onClick={() => setAsking(null)} className="text-muted-foreground hover:text-foreground">
            算了
          </button>
        </div>
      )}
      {busy > 0 && (
        <p className="flex items-center gap-2 border-b px-5 py-2 text-xs text-muted-foreground">
          <LoaderCircle className="size-3.5 animate-spin" />
          还有 {busy} 张在画，大约 {Math.max(1, Math.round(busy * MINUTES_PER_PICTURE))} 分钟。
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-3">
          {pack.slots.map((slot) => (
            <SlotCard key={slot.id} slot={slot} onDone={(m) => m && onMessage(m)} />
          ))}
          <AddMood packId={pack.id} onDone={(m) => m && onMessage(m)} />
        </div>
        {footer}
      </div>
    </div>
  )
}

export function StickerPacks({ initialBot, onClose }: { initialBot: string | null; onClose: () => void }) {
  const { stickersVersion, botsVersion } = useLive()
  const overview = useLiveData<PacksOverview>('/api/sticker-packs', [stickersVersion, botsVersion])
  const [botId, setBotId] = useState<string | null>(initialBot)
  const bot = overview.data?.bots.find((b) => b.bot_id === botId) ?? overview.data?.bots[0] ?? null
  const detail = useLiveData<BotPacks>(
    bot ? `/api/sticker-packs/bots/${encodeURIComponent(bot.bot_id)}` : '/api/sticker-packs',
    [stickersVersion],
  )
  const packs = bot && detail.data && 'packs' in detail.data ? detail.data : null
  const [packId, setPackId] = useState<number | null>(null)
  const pack = packs?.packs.find((p) => p.id === packId) ?? packs?.packs.find((p) => p.active) ?? packs?.packs[0] ?? null
  const [message, setMessage] = useState('')
  const [renaming, setRenaming] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const run = async (action: () => Promise<unknown>) => {
    setMessage('')
    try {
      await action()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }
  const create = () =>
    run(async () => {
      const made = await sendJson<Pack>('POST', `/api/sticker-packs/bots/${encodeURIComponent(bot!.bot_id)}`, {
        name: packs?.packs.length ? `第${packs.packs.length + 1}套` : '默认表情包',
      })
      setPackId(made.id)
    })

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b px-5 py-3">
        <h2 className="text-base font-semibold">表情包管理</h2>
        {overview.data?.can_draw && <DrawEveryone onMessage={setMessage} />}
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="ml-auto grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1">
        <ul className="w-52 shrink-0 space-y-1 overflow-y-auto border-r bg-muted/30 p-2">
          {overview.data?.bots.map((b) => (
            <li key={b.bot_id}>
              <button
                type="button"
                onClick={() => {
                  setBotId(b.bot_id)
                  setPackId(null)
                }}
                aria-current={bot?.bot_id === b.bot_id ? 'true' : undefined}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-2xl px-2.5 py-2 text-left',
                  bot?.bot_id === b.bot_id ? 'bg-card shadow-sm ring-1 ring-border' : 'hover:bg-card/60',
                )}
              >
                <BotGlyph name={b.name} id={b.bot_id} size="md" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{b.name}</span>
                  <span className="text-xs text-primary">
                    {b.done}
                    <span className="text-muted-foreground">/{b.total}</span>
                  </span>
                  <span className="mt-1 block h-1 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-primary transition-[width] duration-500"
                      style={{ width: `${b.total ? (b.done / b.total) * 100 : 0}%` }}
                    />
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        {bot && packs ? (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center gap-2 border-b px-5 py-2.5">
              {packs.packs.map((p) =>
                renaming !== null && p.id === pack?.id ? (
                  <input
                    key={p.id}
                    autoFocus
                    value={renaming}
                    maxLength={20}
                    onChange={(e) => setRenaming(e.target.value)}
                    onBlur={() => {
                      const name = renaming
                      setRenaming(null)
                      if (name.trim() && name !== p.name)
                        void run(() => sendJson('PATCH', `/api/sticker-packs/${p.id}`, { name }))
                    }}
                    onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                    aria-label="这套表情包的名字"
                    className="w-32 rounded-full border bg-background px-3 py-1 text-sm outline-none"
                  />
                ) : (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => {
                      setPackId(p.id)
                      setDeleting(false)
                    }}
                    aria-pressed={p.id === pack?.id}
                    className={cn(
                      'flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm',
                      p.id === pack?.id ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {p.name}
                    {p.active && (
                      <span className="rounded-full border border-current px-1.5 text-[10px] leading-4">使用中</span>
                    )}
                  </button>
                ),
              )}
              <button
                type="button"
                onClick={() => void create()}
                className="flex items-center gap-1 rounded-full border border-dashed px-3 py-1 text-sm text-muted-foreground hover:text-foreground"
              >
                <Plus className="size-3.5" />
                新建
              </button>
              {pack && (
                <span className="ml-auto flex items-center gap-1.5">
                  {!pack.active && (
                    <button
                      type="button"
                      onClick={() => void run(() => sendJson('PATCH', `/api/sticker-packs/${pack.id}`, { active: true }))}
                      className="rounded-full border px-3 py-1 text-sm hover:border-primary hover:text-primary"
                    >
                      用这套
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setRenaming(pack.name)}
                    className="rounded-full border px-3 py-1 text-sm text-muted-foreground hover:text-foreground"
                  >
                    重命名
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      deleting
                        ? void run(async () => {
                            setDeleting(false)
                            await sendJson('DELETE', `/api/sticker-packs/${pack.id}`)
                            setPackId(null)
                          })
                        : setDeleting(true)
                    }
                    className={cn(
                      'rounded-full px-3 py-1 text-sm',
                      deleting ? 'bg-destructive text-white' : 'border text-muted-foreground hover:text-destructive',
                    )}
                  >
                    {deleting ? '连图一起删掉？' : '删除'}
                  </button>
                </span>
              )}
            </div>
            <label className="flex items-center gap-2 border-b px-5 py-2 text-sm text-muted-foreground">
              <Switch
                checked={packs.desk_stickers}
                onCheckedChange={(on) =>
                  void run(() =>
                    sendJson('PUT', `/api/sticker-packs/bots/${encodeURIComponent(bot.bot_id)}/desk`, { on }),
                  )
                }
              />
              {bot.name}在聊天里发表情包（平均四句最多一张，只在桌面聊天里，不发到 QQ）
            </label>
            {message && (
              <p role="status" className="border-b bg-muted/50 px-5 py-2 text-sm">
                {message}
              </p>
            )}
            {pack && overview.data ? (
              <PackEditor
                key={pack.id}
                pack={pack}
                overview={overview.data}
                hasAvatar={packs.has_avatar}
                onMessage={setMessage}
                footer={
                  <>
                  <OwnStickers
                    name={bot.name}
                    own={packs.own}
                    ownOnly={packs.own_only}
                    onOwnOnly={(on) =>
                      void run(() =>
                        sendJson('PUT', `/api/sticker-packs/bots/${encodeURIComponent(bot.bot_id)}/own-only`, { on }),
                      )
                    }
                  />
                  <Share botId={bot.bot_id} pack={pack} onMessage={setMessage} />
                  </>
                }
              />
            ) : (
              <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center text-sm text-muted-foreground">
                <p>
                  {bot.name}还没有自己的表情包。新建一套，nest 会照着她的头像画 15 种表情：开心、卖萌、害羞、委屈、比心……
                </p>
                <button
                  type="button"
                  onClick={() => void create()}
                  className="flex items-center gap-1.5 rounded-full bg-primary px-5 py-2 font-medium text-primary-foreground"
                >
                  <ImageUp className="size-4" />
                  新建一套
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            {overview.error || detail.error || '正在读取…'}
          </div>
        )}
      </div>
    </div>
  )
}
