import { useCallback, useEffect, useId, useState, type ReactNode } from 'react'

import { StatusDot, type Tone } from '@/components/StatusDot'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ApiError, getJson, type AuthInfo, type SystemView } from '@/lib/api'
import { useLive } from '@/lib/live'
import { cn } from '@/lib/utils'

/** Pieces the 系统 and 平台接入 pages share: GET /api/system, one action at a time per row, a row, a saved field. */
export function useSystem() {
  const { systemVersion, accounts } = useLive()
  const [data, setData] = useState<SystemView | null>(null)
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let cancelled = false
    getJson<SystemView>('/api/system').then(
      (value) => {
        if (cancelled) return
        setData(value)
        setError('')
      },
      (e: Error) => {
        if (cancelled) return
        setMissing(e instanceof ApiError && e.status === 404)
        setError(e.message)
      },
    )
    return () => {
      cancelled = true
    }
    // Account status changes (QQ coming online) also change what this page shows.
  }, [systemVersion, accounts, nonce])
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, missing, reload }
}

/** One action at a time per service row, with its result or error shown right there. */
export function useRun(reload: () => void) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const run = async (name: string, action: () => Promise<unknown>) => {
    setBusy(name)
    setError('')
    try {
      await action()
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return { busy, error, run }
}

export function ServiceRow({
  name,
  tone,
  state,
  detail,
  error,
  children,
  below,
}: {
  name: string
  tone: Tone
  state: string
  detail?: ReactNode
  error?: string
  children?: ReactNode
  /** Shown under the row, full width. */
  below?: ReactNode
}) {
  return (
    <li className="flex flex-wrap items-start gap-x-6 gap-y-3 py-4 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2.5">
          <StatusDot tone={tone} />
          <span className="font-medium">{name}</span>
          <span className="text-sm text-muted-foreground">{state}</span>
        </div>
        {detail && <div className="pl-[18px] text-xs leading-relaxed text-muted-foreground">{detail}</div>}
        {error && (
          <p role="alert" className="pl-[18px] text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
      {below && <div className="basis-full pl-[18px]">{below}</div>}
    </li>
  )
}

/** Whether this browser runs on nest's own machine (addresses like 127.0.0.1 only work there). */
export function useOnThisMachine(): boolean {
  const [local, setLocal] = useState(true)
  useEffect(() => {
    getJson<AuthInfo>('/api/auth').then(
      (info) => setLocal(info.local),
      () => setLocal(true), // a nest from before remote access: always this machine
    )
  }, [])
  return local
}
export function PathField({
  label,
  hint,
  value,
  placeholder,
  validate,
  onSave,
}: {
  label: string
  hint: string
  value: string
  placeholder?: string
  /** Why the trimmed value cannot be saved, checked before sending; undefined = fine. */
  validate?: (next: string) => string | undefined
  onSave: (next: string) => Promise<unknown>
}) {
  const id = useId()
  const [draft, setDraft] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const current = draft ?? value
  const save = async () => {
    if (draft === null || draft.trim() === value) {
      setDraft(null)
      return
    }
    const problem = validate?.(draft.trim())
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError('')
    try {
      await onSave(draft.trim())
      setDraft(null)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          value={current}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          aria-describedby={`${id}-hint`}
          onChange={(e) => {
            setDraft(e.target.value)
            setError('')
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) void save()
            if (e.key === 'Escape') {
              setDraft(null)
              setError('')
            }
          }}
          className="font-mono text-xs"
        />
        <Button size="sm" variant="outline" disabled={busy || draft === null} onClick={() => void save()} className="shrink-0">
          {busy ? '保存中…' : '保存'}
        </Button>
      </div>
      <p id={`${id}-hint`} className={cn('text-xs', error ? 'text-destructive' : 'text-muted-foreground')} role={error ? 'alert' : undefined}>
        {error || (saved ? '已保存' : hint)}
      </p>
    </div>
  )
}
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border bg-card p-5">
      <h2 className="mb-4 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}
