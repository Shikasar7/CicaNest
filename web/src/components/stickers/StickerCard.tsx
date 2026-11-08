import { useId, useState } from 'react'
import { Bookmark, BookmarkCheck, LoaderCircle, Pencil, RotateCcw, ShieldAlert, ShieldCheck, Trash2 } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { STATUS } from '@/components/stickers/shared'
import { sendJson, stickerUrl, type Sticker } from '@/lib/api'
import { formatAgo } from '@/lib/format'
import { editKeys } from '@/lib/forms'
import { cn } from '@/lib/utils'


/** One collected sticker: the picture, what the vision model (or the user) said about it, and its actions. */
export function StickerCard({
  sticker,
  bots,
  selected,
  onSelect,
  reload,
}: {
  sticker: Sticker
  bots: { id: string; name: string }[]
  selected: boolean
  onSelect: (next: boolean) => void
  reload: () => void
}) {
  const { busy, error, run } = useAction(reload)
  const [editing, setEditing] = useState(false)
  const patch = (body: Record<string, unknown>) => run(() => sendJson('PATCH', `/api/stickers/${sticker.id}`, body))
  const remove = () => {
    if (!window.confirm('删掉这张表情？删了不能恢复，群里再有人发时会重新收藏。')) return
    void run(() => sendJson('DELETE', `/api/stickers/${sticker.id}`))
  }
  const muted = sticker.status === 'unsuitable' || sticker.status === 'failed'
  const owner = bots.find((bot) => bot.id === sticker.bot_id)?.name

  return (
    <li
      className={cn(
        'flex flex-col rounded-xl border bg-card p-3 transition-shadow',
        selected && 'ring-2 ring-ring/60',
        muted && 'bg-muted/40',
      )}
    >
      <div className="flex items-start gap-3">
        <label className="relative shrink-0 cursor-pointer">
          <img
            src={stickerUrl(sticker)}
            alt={sticker.description || '表情包'}
            loading="lazy"
            className={cn('size-20 rounded-lg border bg-muted object-contain', muted && 'opacity-60')}
          />
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => onSelect(e.target.checked)}
            aria-label="选中这张"
            className="absolute top-1 left-1 size-4 accent-primary"
          />
        </label>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span
              className={cn(
                'rounded-full px-2 py-0.5',
                sticker.status === 'ready' && 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
                sticker.status === 'pending' && 'bg-muted text-muted-foreground',
                muted && 'bg-destructive/10 text-destructive',
              )}
            >
              {STATUS[sticker.status]}
            </span>
            {sticker.kept && <span className="rounded-full bg-primary/10 px-2 py-0.5 text-primary">保留</span>}
            {sticker.animated && <span className="text-muted-foreground">动图</span>}
            {sticker.kind === 'mface' && <span className="text-muted-foreground">商城表情</span>}
            {sticker.kind === 'upload' && <span className="text-muted-foreground">手动添加</span>}
            {sticker.kind === 'drawn' && <span className="text-muted-foreground">画的</span>}
            {sticker.bot_id && <span className="text-muted-foreground">{owner ?? '已删除的 bot'}专用</span>}
          </div>
          {sticker.description || sticker.caption ? (
            <p className="text-sm break-words">
              {sticker.description}
              {sticker.caption && <span className="text-muted-foreground">「{sticker.caption}」</span>}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">还没有描述</p>
          )}
          {sticker.tags.length > 0 && (
            <p className="flex flex-wrap gap-1">
              {sticker.tags.map((tag) => (
                <span key={tag} className="rounded-md bg-muted px-1.5 py-0.5 text-xs">
                  {tag}
                </span>
              ))}
            </p>
          )}
          {sticker.note && <p className="text-xs text-destructive">{sticker.note}</p>}
          <p className="text-xs text-muted-foreground tabular-nums">
            见过 {sticker.seen_count} 次 · 发过 {sticker.used_count} 次 · 最近一次 {formatAgo(sticker.last_seen_at)}
          </p>
        </div>
      </div>
      {editing ? (
        <EditForm sticker={sticker} busy={busy} onCancel={() => setEditing(false)} onSave={async (body) => (await patch(body)) && setEditing(false)} />
      ) : (
        <div className="mt-2 flex flex-wrap gap-1">
          <Button size="xs" variant="ghost" disabled={busy} onClick={() => setEditing(true)}>
            <Pencil />
            改描述
          </Button>
          <Button size="xs" variant="ghost" disabled={busy} onClick={() => void patch({ kept: !sticker.kept })}>
            {sticker.kept ? <BookmarkCheck /> : <Bookmark />}
            {sticker.kept ? '取消保留' : '保留'}
          </Button>
          {sticker.status === 'unsuitable' ? (
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => void patch({ status: 'ready' })}>
              <ShieldCheck />
              恢复
            </Button>
          ) : (
            sticker.status === 'ready' && (
              <Button size="xs" variant="ghost" disabled={busy} onClick={() => void patch({ status: 'unsuitable' })}>
                <ShieldAlert />
                不适合
              </Button>
            )
          )}
          <Button
            size="xs"
            variant="ghost"
            disabled={busy}
            onClick={() => void run(() => sendJson('POST', `/api/stickers/${sticker.id}/describe`))}
          >
            <RotateCcw />
            重新描述
          </Button>
          <Button size="xs" variant="ghost" className="text-destructive" disabled={busy} onClick={remove}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Trash2 />}
            删除
          </Button>
          <NativeSelect
            className="h-6 max-w-32 text-xs"
            aria-label="谁能发这张表情"
            disabled={busy}
            value={sticker.bot_id ?? ''}
            onChange={(e) =>
              void run(() =>
                sendJson('POST', '/api/stickers/owner', { ids: [sticker.id], bot_id: e.target.value || null }),
              )
            }
          >
            <option value="">所有 bot 都能发</option>
            {sticker.bot_id && !owner && <option value={sticker.bot_id}>已删除的 bot 专用</option>}
            {bots.map((bot) => (
              <option key={bot.id} value={bot.id}>
                {bot.name}专用
              </option>
            ))}
          </NativeSelect>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          {error}
        </p>
      )}
    </li>
  )
}

function EditForm({
  sticker,
  busy,
  onCancel,
  onSave,
}: {
  sticker: Sticker
  busy: boolean
  onCancel: () => void
  onSave: (body: { description: string; caption: string; tags: string[] }) => void
}) {
  const id = useId()
  const [description, setDescription] = useState(sticker.description)
  const [caption, setCaption] = useState(sticker.caption)
  const [tags, setTags] = useState(sticker.tags.join('、'))
  const save = () =>
    onSave({
      description,
      caption,
      tags: tags
        .split(/[、,，\s]+/)
        .map((t) => t.trim())
        .filter(Boolean),
    })
  const keys = editKeys({ onEnter: save, onEscape: onCancel })
  return (
    <div className="mt-2 space-y-2 rounded-lg bg-muted/50 p-2">
      <div className="space-y-1">
        <Label htmlFor={`${id}-d`} className="text-xs">
          描述
        </Label>
        <Input id={`${id}-d`} value={description} maxLength={60} onChange={(e) => setDescription(e.target.value)} onKeyDown={keys} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-c`} className="text-xs">
          图上的字
        </Label>
        <Input id={`${id}-c`} value={caption} maxLength={60} onChange={(e) => setCaption(e.target.value)} onKeyDown={keys} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-t`} className="text-xs">
          标签（最多 3 个，用顿号隔开）
        </Label>
        <Input id={`${id}-t`} value={tags} onChange={(e) => setTags(e.target.value)} onKeyDown={keys} />
      </div>
      <p className="text-xs text-muted-foreground">改过的描述不会再被看图模型覆盖，除非点「重新描述」。</p>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={save}>
          保存
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  )
}
