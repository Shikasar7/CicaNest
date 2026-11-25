import { useCallback, useEffect, useRef, useState } from 'react'
import { ShieldQuestion } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { getJson, sendJson } from '@/lib/api'
import { chatView, desktop } from '@/lib/desktop'
import { useLive } from '@/lib/live'

/** 确认: a step a companion wants to take that needs your nod, shown over every page of the console and the
 * desktop chat window until someone answers (on any device). It shows exactly what will be run. */

type Approval = {
  id: number
  bot_id: string
  bot_name: string
  task_id: number | null
  plugin_name: string
  tool: string
  given: string
  created_at: number
  expires_at: number
}

function left(expires: number): string {
  const seconds = Math.max(0, Math.round(expires - Date.now() / 1000))
  return seconds >= 120 ? `${Math.round(seconds / 60)} 分钟` : `${seconds} 秒`
}

export function Approvals() {
  const { subscribe } = useLive()
  const [waiting, setWaiting] = useState<Approval[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const [, tick] = useState(0)
  const told = useRef(new Set<number>())

  const reload = useCallback(() => {
    getJson<{ approvals: Approval[] }>('/api/approvals').then(
      (data) => setWaiting(data.approvals),
      () => {},
    )
  }, [])
  useEffect(reload, [reload])
  useEffect(() => subscribe((item) => item.kind === 'approval' && reload()), [subscribe, reload])
  useEffect(() => {
    if (!waiting.length) return
    const timer = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [waiting.length])
  // The desktop chat window says so in a Windows notification, once per request.
  useEffect(() => {
    if (!chatView || !desktop?.notify) return
    for (const a of waiting) {
      if (told.current.has(a.id)) continue
      told.current.add(a.id)
      void desktop.notify({ title: `${a.bot_name} 要你点头`, body: `「${a.plugin_name}」的 ${a.tool}`, botId: a.bot_id })
    }
  }, [waiting])

  const answer = async (id: number, choice: 'once' | 'kind' | 'deny') => {
    setBusy(id)
    setError('')
    try {
      await sendJson('POST', `/api/approvals/${id}`, { choice })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
      reload()
    }
  }

  if (!waiting.length) return null
  return (
    <div className="fixed right-4 bottom-4 z-50 flex max-h-[80svh] w-[min(28rem,calc(100vw-2rem))] flex-col gap-3 overflow-y-auto">
      {waiting.map((a) => (
        <div key={a.id} role="alertdialog" aria-label={`${a.bot_name} 要你点头`} className="space-y-3 rounded-xl border-2 border-amber-500/60 bg-card p-4 shadow-xl">
          <div className="flex items-start gap-2">
            <ShieldQuestion className="mt-0.5 size-5 shrink-0 text-amber-500" />
            <div className="min-w-0">
              <div className="font-medium">
                {a.bot_name} 想用「{a.plugin_name}」的 {a.tool}
              </div>
              <div className="text-xs text-muted-foreground">
                {a.task_id ? `在任务 ${a.task_id} 里` : '在聊天里'} · 还剩 {left(a.expires_at)}，不回就不做
              </div>
            </div>
          </div>
          <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-[11px] whitespace-pre-wrap break-words">{a.given}</pre>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy === a.id} onClick={() => void answer(a.id, 'once')}>
              允许一次
            </Button>
            <Button size="sm" variant="outline" disabled={busy === a.id} onClick={() => void answer(a.id, 'kind')}>
              {a.task_id ? '这个任务里同类不再问' : '这次回话里同类不再问'}
            </Button>
            <Button size="sm" variant="ghost" className="text-destructive" disabled={busy === a.id} onClick={() => void answer(a.id, 'deny')}>
              不允许
            </Button>
          </div>
        </div>
      ))}
      {error && <p role="alert" className="rounded-md bg-card p-2 text-xs text-destructive shadow">{error}</p>}
    </div>
  )
}
