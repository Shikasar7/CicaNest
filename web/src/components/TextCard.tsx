import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Check, LoaderCircle, Plus, RotateCcw, Trash2 } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

/** One named text (a 人设 or a 语气) edited in place: its name, its body, where it is used, save, delete
 * and, for a built-in one, reset. The parent says what each action sends. */
export function TextCard({
  name,
  body,
  nameLabel,
  bodyLabel,
  nameMax,
  bodyMax,
  rows = 6,
  badge,
  usedBy,
  onSave,
  onDelete,
  onReset,
  reload,
}: {
  name: string
  body: string
  nameLabel: string
  bodyLabel: string
  nameMax: number
  bodyMax: number
  rows?: number
  badge?: ReactNode
  usedBy: string[]
  onSave: (name: string, body: string) => Promise<unknown>
  /** Absent: it cannot be deleted. */
  onDelete?: () => Promise<unknown>
  onReset?: () => Promise<unknown>
  reload: () => void
}) {
  const [draftName, setName] = useState(name)
  const [draftBody, setBody] = useState(body)
  const { busy, error, run } = useAction(reload)
  const id = useId()
  // A save elsewhere (another tab, a reset) replaces what is shown, unless there are unsaved edits.
  const [seen, setSeen] = useState({ name, body })
  if (seen.name !== name || seen.body !== body) {
    if (draftName === seen.name && draftBody === seen.body) {
      setName(name)
      setBody(body)
    }
    setSeen({ name, body })
  }
  const changed = draftName.trim() !== name || draftBody.trim() !== body
  const save = (event: FormEvent) => {
    event.preventDefault()
    void run(() => onSave(draftName.trim(), draftBody.trim()))
  }
  return (
    <form onSubmit={save} className="space-y-3 rounded-2xl border bg-card p-5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1 space-y-1">
          <Label htmlFor={`${id}-name`} className="text-xs text-muted-foreground">
            {nameLabel}
          </Label>
          <Input id={`${id}-name`} value={draftName} maxLength={nameMax} onChange={(e) => setName(e.target.value)} />
        </div>
        {badge}
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-body`} className="text-xs text-muted-foreground">
          {bodyLabel}
        </Label>
        <Textarea id={`${id}-body`} rows={rows} value={draftBody} maxLength={bodyMax} onChange={(e) => setBody(e.target.value)} />
      </div>
      <p className="text-xs text-muted-foreground">{usedBy.length ? `用在：${usedBy.join('、')}` : '还没有群在用'}</p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={busy || !changed || !draftName.trim() || !draftBody.trim()}>
          {busy ? <LoaderCircle className="animate-spin" /> : <Check />}
          保存
        </Button>
        {changed && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              setName(name)
              setBody(body)
            }}
          >
            撤销修改
          </Button>
        )}
        {onReset && (
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void run(onReset)}>
            <RotateCcw />
            恢复默认
          </Button>
        )}
        {onDelete && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="ml-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
            disabled={busy || usedBy.length > 0}
            title={usedBy.length ? '还有群在用，先换掉再删' : undefined}
            onClick={() => {
              if (window.confirm(`删掉「${name}」？`)) void run(onDelete)
            }}
          >
            <Trash2 />
            删除
          </Button>
        )}
      </div>
    </form>
  )
}

/** 新建: a name and a body, added through `onCreate`. */
export function NewTextForm({
  title,
  nameLabel,
  bodyLabel,
  namePlaceholder,
  bodyPlaceholder,
  nameMax,
  bodyMax,
  onCreate,
  reload,
}: {
  title: string
  nameLabel: string
  bodyLabel: string
  namePlaceholder: string
  bodyPlaceholder: string
  nameMax: number
  bodyMax: number
  onCreate: (name: string, body: string) => Promise<unknown>
  reload: () => void
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [body, setBody] = useState('')
  const { busy, error, run } = useAction(reload)
  const id = useId()
  if (!open) {
    return (
      <div>
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Plus />
          {title}
        </Button>
      </div>
    )
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (await run(() => onCreate(name.trim(), body.trim()))) {
      setName('')
      setBody('')
      setOpen(false)
    }
  }
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-3 rounded-2xl border bg-card p-5">
      <h2 className="text-sm font-semibold">{title}</h2>
      <div className="space-y-1">
        <Label htmlFor={`${id}-name`}>{nameLabel}</Label>
        <Input id={`${id}-name`} value={name} maxLength={nameMax} placeholder={namePlaceholder} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-body`}>{bodyLabel}</Label>
        <Textarea id={`${id}-body`} rows={6} value={body} maxLength={bodyMax} placeholder={bodyPlaceholder} onChange={(e) => setBody(e.target.value)} />
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !name.trim() || !body.trim()}>
          {busy && <LoaderCircle className="animate-spin" />}
          添加
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
          取消
        </Button>
      </div>
    </form>
  )
}
