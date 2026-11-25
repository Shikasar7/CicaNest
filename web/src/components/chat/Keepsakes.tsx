import { useId, useRef, useState, type ReactNode } from 'react'
import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { sendJson } from '@/lib/api'
import { formatAgo } from '@/lib/format'
import { useLiveData } from '@/lib/live-data'

/** 回忆档案: a companion's records of the time with you, word for word. She does not read all of them every
 * time: the parts that go with what is being said are looked up, only in her chat with you.
 *
 * 回忆草稿: before her chat is cleaned away she writes up what was worth keeping (nest.core.keepsake_drafts).
 * She does not use one until you have read it here and kept it; you can correct it first, or throw it away. */

type Keepsake = {
  id: number
  bot_id: string
  title: string
  body: string
  source: string
  draft: boolean
  updated_at: number
}

const TITLE_MAX = 80
const BODY_MAX = 60_000

export function Keepsakes({ botId, name, onClose }: { botId: string; name: string; onClose: () => void }) {
  const { data: records, error: unread, reload } = useLiveData<Keepsake[]>(
    `/api/bots/${encodeURIComponent(botId)}/keepsakes`,
    [],
  )
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<number | 'new' | null>(null)

  const keep = async (record: Keepsake) => {
    setError('')
    try {
      await sendJson('PATCH', `/api/keepsakes/${record.id}`, { keep: true })
      reload()
    } catch (e) {
      setError(`没留下：${(e as Error).message}`)
    }
  }

  const remove = async (record: Keepsake) => {
    const ask = record.draft
      ? `不要这份草稿「${record.title}」？她这段时间的聊天过些天会被清掉，到时候就没处再整理了。`
      : `删掉「${record.title}」？删了就找不回来了。`
    if (!window.confirm(ask)) return
    setError('')
    try {
      await sendJson('DELETE', `/api/keepsakes/${record.id}`)
      reload()
    } catch (e) {
      setError(`没删成：${(e as Error).message}`)
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <div className="min-w-0">
          <div className="font-medium">{name}的回忆档案</div>
          <div className="text-xs text-muted-foreground">
            原文保存。她不会每次都读全部，聊到相关的事时才翻出几段；只在她和你的私聊里用。
          </div>
        </div>
        <ExportPack botId={botId} />
        <Button size="sm" variant="ghost" onClick={onClose}>
          回到聊天
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-sm">
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {unread && <p role="alert" className="text-destructive">读取失败：{unread}</p>}
        {!records && !unread && <p className="text-muted-foreground">正在读取……</p>}
        {records?.some((r) => r.draft) && (
          <p className="rounded-lg bg-muted p-3">
            {name}把最近的聊天整理成了回忆草稿，等你看过才会留下。哪里记错了可以先改；觉得不用留就不要。
          </p>
        )}
        {records?.length === 0 && editing !== 'new' && (
          <p className="text-muted-foreground">还没有回忆。可以用「搬一个进来」导入，或者自己写一份。</p>
        )}
        {[...(records ?? [])].sort((a, b) => Number(b.draft) - Number(a.draft)).map((record) =>
          editing === record.id ? (
            <Editor
              key={record.id}
              initial={record}
              onCancel={() => setEditing(null)}
              onSave={async (title, body) => {
                await sendJson('PATCH', `/api/keepsakes/${record.id}`, { title, body })
                setEditing(null)
                reload()
              }}
            />
          ) : (
            <Record key={record.id} record={record}>
              {record.draft && (
                <Button size="sm" onClick={() => void keep(record)}>
                  留下
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={() => setEditing(record.id)}>
                {record.draft ? '先改一改' : '编辑'}
              </Button>
              <Button size="sm" variant="ghost" className="text-destructive" onClick={() => void remove(record)}>
                {record.draft ? '不要了' : '删除'}
              </Button>
            </Record>
          ),
        )}
        {editing === 'new' ? (
          <Editor
            initial={{ title: '', body: '' }}
            onCancel={() => setEditing(null)}
            onSave={async (title, body) => {
              await sendJson('POST', `/api/bots/${encodeURIComponent(botId)}/keepsakes`, { title, body })
              setEditing(null)
              reload()
            }}
          />
        ) : (
          <Button size="sm" variant="outline" onClick={() => setEditing('new')}>
            <Plus className="size-4" />
            写一份
          </Button>
        )}
      </div>
    </div>
  )
}

/** One record. Opened, its whole text is part of the page (a box of its own that scrolls would catch the wheel
 * and stop it at its end); its title stays at the top of the window meanwhile, to fold it from anywhere. */
function Record({ record, children }: { record: Keepsake; children: ReactNode }) {
  const box = useRef<HTMLDetailsElement>(null)
  return (
    <details
      ref={box}
      className="rounded-lg border"
      open={record.draft}
      onToggle={(e) => !e.currentTarget.open && e.currentTarget.scrollIntoView({ block: 'nearest' })}
    >
      <summary className="sticky -top-4 z-[1] cursor-pointer rounded-lg bg-card p-3">
        {record.draft && (
          <span className="mr-2 rounded bg-primary px-1.5 py-0.5 text-xs text-primary-foreground">待你过目</span>
        )}
        <span className="font-medium">{record.title}</span>
        <span className="ml-2 text-xs text-muted-foreground">
          {record.source && `${record.source} · `}
          {record.body.length} 字 · {formatAgo(record.updated_at)}改过
        </span>
      </summary>
      <div className="px-3 pb-3">
        <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed">{record.body}</pre>
        <div className="mt-2 flex gap-2">
          {children}
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => box.current && (box.current.open = false)}>
            收起
          </Button>
        </div>
      </div>
    </details>
  )
}

function Editor({ initial, onSave, onCancel }: {
  initial: { title: string; body: string }
  onSave: (title: string, body: string) => Promise<void>
  onCancel: () => void
}) {
  const [title, setTitle] = useState(initial.title)
  const [body, setBody] = useState(initial.body)
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  const ids = { title: useId(), body: useId() }
  const save = async () => {
    setState('busy')
    try {
      await onSave(title.trim(), body)
    } catch (e) {
      setState(`没保存成：${(e as Error).message}`)
    }
  }
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="space-y-1">
        <Label htmlFor={ids.title}>标题</Label>
        <Input id={ids.title} value={title} maxLength={TITLE_MAX} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={ids.body}>内容（可以用 Markdown 的 # 标题分段，找起来更准）</Label>
        <Textarea id={ids.body} value={body} maxLength={BODY_MAX} rows={14} onChange={(e) => setBody(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={!title.trim() || !body.trim() || state === 'busy'} onClick={() => void save()}>
          保存
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
      {state !== 'idle' && state !== 'busy' && <p role="alert" className="text-destructive">{state}</p>}
    </div>
  )
}

/** 导出伙伴包: her persona, what she remembers of you and her records as a .zip to keep or move. */
function ExportPack({ botId }: { botId: string }) {
  const [open, setOpen] = useState(false)
  const [memories, setMemories] = useState(true)
  const [records, setRecords] = useState(true)
  const href = `/api/companions/${encodeURIComponent(botId)}/export?memories=${memories}&records=${records}`
  return (
    <div className="relative ml-auto">
      <Button size="sm" variant="outline" onClick={() => setOpen(!open)} aria-expanded={open}>
        导出伙伴包
      </Button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-64 space-y-2 rounded-lg border bg-card p-3 text-xs shadow-lg">
          <label className="flex items-center gap-2">
            <input type="checkbox" className="size-4 accent-primary" checked={memories} onChange={(e) => setMemories(e.target.checked)} />
            带上她记得的关于你的事
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" className="size-4 accent-primary" checked={records} onChange={(e) => setRecords(e.target.checked)} />
            带上回忆档案
          </label>
          <p className="text-muted-foreground">包里有你们之间的事，给别人之前想清楚。桌宠素材在你电脑上，不在包里。</p>
          <a href={href} download className="inline-block rounded-md bg-primary px-3 py-1.5 text-primary-foreground" onClick={() => setOpen(false)}>
            下载 .zip
          </a>
        </div>
      )}
    </div>
  )
}
