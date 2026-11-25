import { useCallback, useEffect, useId, useState } from 'react'

import { STATUS_LABEL, TOOL_LABEL, TaskRow, type Task, type TaskStatus } from '@/components/chat/Tasks'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { getJson, sendJson, type Bot } from '@/lib/api'
import { formatTime } from '@/lib/format'
import { useBots, useLive } from '@/lib/live'

/** 任务: every companion's background tasks in one place, and the limits each task gets. */

type Limits = { max_steps: number; max_minutes: number; max_tokens: number; at_once: number }
type Ranges = Record<keyof Limits, { default: number; min: number; max: number }>
type TaskWithBot = Task & { bot_id: string }

const FIELDS: { key: keyof Limits; label: string; hint: string }[] = [
  { key: 'max_steps', label: '每个任务最多几步', hint: '一步是用一次工具' },
  { key: 'max_minutes', label: '每个任务最多几分钟', hint: '等你点头、等电脑上线的时间不算' },
  { key: 'max_tokens', label: '每个任务最多多少 token', hint: '到了就让她停手汇报' },
  { key: 'at_once', label: '同时最多做几个', hint: '所有伙伴加起来；每个伙伴同时只做一个' },
]

function LimitsCard() {
  const [limits, setLimits] = useState<Limits | null>(null)
  const [ranges, setRanges] = useState<Ranges | null>(null)
  const [draft, setDraft] = useState<Partial<Record<keyof Limits, string>>>({})
  const [state, setState] = useState<'idle' | 'busy' | 'saved' | string>('idle')
  const id = useId()
  useEffect(() => {
    getJson<{ limits: Limits; ranges: Ranges }>('/api/task-settings').then(
      (data) => (setLimits(data.limits), setRanges(data.ranges)),
      (e: Error) => setState(`读取失败：${e.message}`),
    )
  }, [])
  const changed = Object.keys(draft).length > 0
  const save = async () => {
    setState('busy')
    try {
      const body = Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, Number(v)]))
      const data = await sendJson<{ limits: Limits }>('PUT', '/api/task-settings', body)
      setLimits(data.limits)
      setDraft({})
      setState('saved')
    } catch (e) {
      setState((e as Error).message)
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>上限</CardTitle>
        <CardDescription>之后新开的任务、点「接着做」的任务按这里的来；已经在做的不变。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {limits && ranges && (
          <div className="grid gap-3 sm:grid-cols-2">
            {FIELDS.map((f) => (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={`${id}-${f.key}`}>{f.label}</Label>
                <Input
                  id={`${id}-${f.key}`}
                  type="number"
                  min={ranges[f.key].min}
                  max={ranges[f.key].max}
                  value={draft[f.key] ?? String(limits[f.key])}
                  onChange={(e) => (setDraft((d) => ({ ...d, [f.key]: e.target.value })), setState('idle'))}
                />
                <p className="text-xs text-muted-foreground">
                  {f.hint}（{ranges[f.key].min}–{ranges[f.key].max}，默认 {ranges[f.key].default}）
                </p>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center gap-3">
          <Button size="sm" disabled={!changed || state === 'busy'} onClick={() => void save()}>
            保存
          </Button>
          {changed && (
            <Button size="sm" variant="ghost" onClick={() => setDraft({})}>
              还原
            </Button>
          )}
          {state === 'saved' && <span className="text-xs text-muted-foreground">保存好了</span>}
          {state !== 'idle' && state !== 'busy' && state !== 'saved' && (
            <span role="alert" className="text-xs text-destructive">
              {state}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function AllTasks() {
  const { subscribe } = useLive()
  const { data: bots } = useBots<Bot[]>('/api/bots')
  const [tasks, setTasks] = useState<TaskWithBot[] | null>(null)
  const [error, setError] = useState('')
  const [botId, setBotId] = useState('')
  const [status, setStatus] = useState<'' | TaskStatus>('')
  const [open, setOpen] = useState<number | null>(null)
  const [nonce, setNonce] = useState(0)
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  useEffect(() => {
    let cancelled = false
    getJson<{ tasks: TaskWithBot[] }>('/api/tasks').then(
      (data) => !cancelled && (setTasks(data.tasks), setError('')),
      (e: Error) => !cancelled && setError(`读取失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [nonce])
  useEffect(() => subscribe((item) => item.kind === 'task' && reload()), [subscribe, reload])
  const names = Object.fromEntries((bots ?? []).map((b) => [b.id, b.name]))
  const shown = (tasks ?? []).filter((t) => (!botId || t.bot_id === botId) && (!status || t.status === status))
  const tokens = shown.reduce((sum, t) => sum + t.tokens, 0)
  return (
    <Card>
      <CardHeader>
        <CardTitle>所有任务</CardTitle>
        <CardDescription>最近 100 个；做完的留 30 天。在聊天页的「任务」里也能看到她自己的。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect aria-label="伙伴" value={botId} onChange={(e) => setBotId(e.target.value)}>
            <option value="">所有伙伴</option>
            {(bots ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </NativeSelect>
          <NativeSelect aria-label="状态" value={status} onChange={(e) => setStatus(e.target.value as '' | TaskStatus)}>
            <option value="">所有状态</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </NativeSelect>
          <span className="text-xs text-muted-foreground">
            {shown.length} 个，一共 {tokens.toLocaleString()} token
          </span>
        </div>
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {tasks && shown.length === 0 && <p className="text-muted-foreground">没有任务。</p>}
        <ul className="space-y-2">
          {shown.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              who={names[t.bot_id] ?? t.bot_id}
              open={open === t.id}
              onToggle={() => setOpen(open === t.id ? null : t.id)}
              onChanged={reload}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

type Nod = {
  id: number
  bot_name: string
  task_id: number | null
  plugin_name: string
  tool: string
  given: string
  status: 'pending' | 'allowed' | 'allowed_kind' | 'denied' | 'expired'
  decided_by: string
  created_at: number
  decided_at: number | null
}
const NOD_LABEL: Record<Nod['status'], string> = {
  pending: '等你点头',
  allowed: '允许了一次',
  allowed_kind: '允许了，这次同类不再问',
  denied: '没有允许',
  expired: '没等到你点头',
}

/** 点过头的: what she asked to do, what you said, when and on which device. */
function Nods() {
  const { subscribe } = useLive()
  const [nods, setNods] = useState<Nod[] | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [all, setAll] = useState(false)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let cancelled = false
    getJson<{ approvals: Nod[] }>('/api/approvals?everything=true').then(
      (data) => !cancelled && (setNods(data.approvals), setError('')),
      (e: Error) => !cancelled && setError(`读取失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [nonce])
  useEffect(() => subscribe((item) => item.kind === 'approval' && setNonce((n) => n + 1)), [subscribe])
  const shown = all ? (nods ?? []) : (nods ?? []).slice(0, 10)
  return (
    <Card>
      <CardHeader>
        <CardTitle>点过头的</CardTitle>
        <CardDescription>她要做、问过你的事：你怎么答的，什么时候，在哪台设备上。最近 100 条，留 90 天。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {nods?.length === 0 && <p className="text-muted-foreground">还没有问过你什么。</p>}
        <ul className="divide-y">
          {shown.map((n) => (
            <li key={n.id} className="py-2">
              <button
                type="button"
                onClick={() => setOpen(open === n.id ? null : n.id)}
                aria-expanded={open === n.id}
                className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-0.5 text-left"
              >
                <span className="font-medium">{n.bot_name}</span>
                <span>
                  {n.plugin_name} · {TOOL_LABEL[n.tool] ?? n.tool}
                </span>
                <span className={n.status === 'denied' ? 'text-destructive' : 'text-muted-foreground'}>{NOD_LABEL[n.status]}</span>
                <span className="ml-auto text-xs text-muted-foreground">{formatTime(n.created_at)}</span>
              </button>
              {open === n.id && (
                <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                  {n.decided_by && <p className="break-words">在这里答的：{n.decided_by}</p>}
                  {n.task_id !== null && <p>后台任务 {n.task_id} 里的一步</p>}
                  <pre className="max-h-60 overflow-auto rounded-md bg-muted p-2 whitespace-pre-wrap break-words">{n.given}</pre>
                </div>
              )}
            </li>
          ))}
        </ul>
        {!all && (nods?.length ?? 0) > shown.length && (
          <Button size="sm" variant="ghost" onClick={() => setAll(true)}>
            看全部 {nods?.length} 条
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

export function TasksPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
      <AllTasks />
      <Nods />
      <LimitsCard />
    </div>
  )
}
