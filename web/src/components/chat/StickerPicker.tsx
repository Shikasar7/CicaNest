import { useEffect, useRef, useState } from 'react'
import { ImageUp, LoaderCircle, Settings2, Star } from 'lucide-react'

import { sendBlob, sendJson, type Sticker } from '@/lib/api'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { stickerImage } from '@/lib/sticker-packs'
import { cn } from '@/lib/utils'

/** The desk chat's 表情包 button: her own pack, what you starred, what you sent lately, the
 * whole collection, or a picture of your own. Picking one sends it at once. */

type Tab = 'hers' | 'starred' | 'recent' | 'all'
type Picker = { hers: Sticker[]; recent: Sticker[]; starred: Sticker[] }

function Tile({ sticker, starred, onPick, onStar }: {
  sticker: Sticker
  starred: boolean
  onPick: (id: number) => void
  onStar: (id: number, on: boolean) => void
}) {
  return (
    <div className="group/tile relative">
      <button
        type="button"
        onClick={() => onPick(sticker.id)}
        title={sticker.description || sticker.tags.join('、') || '表情'}
        className="grid aspect-square w-full place-items-center overflow-hidden rounded-xl bg-muted/40 p-1 transition-transform hover:scale-105 hover:bg-muted active:scale-95"
      >
        <img src={stickerImage(sticker.id)} alt={sticker.tags[0] ?? '表情'} loading="lazy" className="max-h-full max-w-full object-contain" />
      </button>
      <button
        type="button"
        onClick={() => onStar(sticker.id, !starred)}
        aria-label={starred ? '取消收藏' : '收藏'}
        title={starred ? '取消收藏' : '收藏'}
        className={cn(
          'absolute -top-1 -right-1 grid size-6 place-items-center rounded-full border bg-popover shadow-sm transition-opacity hover:text-primary focus-visible:opacity-100',
          starred ? 'text-primary' : 'text-muted-foreground opacity-0 group-hover/tile:opacity-100 max-sm:opacity-70',
        )}
      >
        <Star className={cn('size-3', starred && 'fill-current')} />
      </button>
    </div>
  )
}

export function StickerPicker({
  botId,
  botName,
  onPick,
  onManage,
  onClose,
}: {
  botId: string
  botName: string
  onPick: (stickerId: number) => void
  onManage: () => void
  onClose: () => void
}) {
  const { stickersVersion } = useLive()
  const picker = useLiveData<Picker>(`/api/sticker-packs/bots/${encodeURIComponent(botId)}/picker`, [stickersVersion])
  const [tab, setTab] = useState<Tab | null>(null)
  const [query, setQuery] = useState('')
  const all = useLiveData<{ items: Sticker[] }>(
    `/api/stickers?status=ready&limit=60&q=${encodeURIComponent(query.trim())}`,
    [stickersVersion],
  )
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && onClose()
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [onClose])

  const hers = picker.data?.hers ?? []
  const recent = picker.data?.recent ?? []
  const shown: Tab = tab ?? (hers.length ? 'hers' : recent.length ? 'recent' : 'all')
  const starred = picker.data?.starred ?? []
  const starredIds = new Set(starred.map((s) => s.id))
  const items = shown === 'hers' ? hers : shown === 'starred' ? starred : shown === 'recent' ? recent : (all.data?.items ?? [])

  const star = async (id: number, on: boolean) => {
    setError('')
    try {
      await sendJson('PUT', `/api/stickers/${id}/star`, { on })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const upload = async (file: File | undefined) => {
    if (!file) return
    setUploading(true)
    setError('')
    try {
      const { sticker } = await sendBlob<{ sticker: Sticker }>('/api/stickers/upload', file)
      onPick(sticker.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setUploading(false)
    }
  }

  const tabs: [Tab, string][] = [
    ['hers', `${botName}的`],
    ['starred', '收藏'],
    ['recent', '常用'],
    ['all', '全部'],
  ]
  return (
    <div
      ref={box}
      className="absolute bottom-full left-0 z-30 mb-2 flex h-80 w-[min(26rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-3xl border bg-popover text-popover-foreground shadow-xl animate-in fade-in slide-in-from-bottom-2 duration-150 motion-reduce:animate-none"
    >
      <div className="flex items-center gap-1 border-b px-2 py-1.5">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            aria-pressed={shown === key}
            className={cn(
              'rounded-full px-3 py-1 text-sm',
              shown === key ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted',
            )}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => input.current?.click()}
          disabled={uploading}
          title="用一张自己的图片"
          className="ml-auto grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
        >
          {uploading ? <LoaderCircle className="size-4 animate-spin" /> : <ImageUp className="size-4" />}
        </button>
        <button
          type="button"
          onClick={onManage}
          title="表情包管理"
          className="grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Settings2 className="size-4" />
        </button>
      </div>
      {shown === 'all' && (
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜表情：开心、猫、无语…"
          aria-label="搜表情"
          className="mx-3 mt-2 rounded-full border bg-background px-3 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      )}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {items.length > 0 ? (
          <div className="grid grid-cols-5 gap-2">
            {items.map((s) => (
              <Tile
                key={s.id}
                sticker={s}
                starred={starredIds.has(s.id)}
                onPick={onPick}
                onStar={(id, on) => void star(id, on)}
              />
            ))}
          </div>
        ) : (
          <p className="px-2 py-8 text-center text-sm text-muted-foreground">
            {shown === 'hers' ? (
              <>
                {botName}还没有自己的表情包。
                <button type="button" onClick={onManage} className="text-primary underline-offset-2 hover:underline">
                  去画一套
                </button>
              </>
            ) : shown === 'starred' ? (
              '还没有收藏。表情右上角的星星点一下就收进来。'
            ) : shown === 'recent' ? (
              '还没发过表情。'
            ) : (
              '没有找到。'
            )}
          </p>
        )}
        {error && (
          <p role="alert" className="pt-2 text-center text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
      <input
        ref={input}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        hidden
        onChange={(e) => {
          void upload(e.target.files?.[0])
          e.target.value = ''
        }}
      />
    </div>
  )
}
