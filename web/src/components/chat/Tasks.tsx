import { useCallback, useEffect, useId, useState } from 'react'
import { ChevronDown, ChevronRight, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { getJson, sendJson } from '@/lib/api'
import { formatTime } from '@/lib/format'
import { useLive } from '@/lib/live'
import { cn } from '@/lib/utils'

import { Routines } from './Routines'

/** 任务: what she works on in the background, step by step with her tools, and reports on in the chat when
 * done. She starts one herself when you ask for something that takes a while; you can start one here too.
 * Each step can be looked into; a task can be stopped, gone on with, or added to while it runs. */

export type TaskStatus =
  | 'queued'
  | 'running'
  | 'waiting_pc'
  | 'done'
  | 'failed'
  | 'stopped'
  | 'limit'
  | 'interrupted'
export type Task = {
  id: number
  title: string
  goal: string
  status: TaskStatus
  source: 'chat' | 'console'
  steps: number
  tokens: number
  seconds: number
  max_steps: number
  report: string
  error: string
  created_at: number
  ended_at: number | null
  live: boolean
}
type Step = {
  id: number
  kind: 'say' | 'tool' | 'note' | 'stop' | 'resume' | 'approval' | 'fold'
  tool: string
  given: string
  result: string
  status: 'running' | 'done'
  ms: number | null
  created_at: number
}

export const STATUS_LABEL: Record<TaskStatus, string> = {
  queued: '排队中',
  running: '在做',
  waiting_pc: '等电脑上线',
  done: '做完了',
  failed: '没做成',
  stopped: '停了',
  limit: '到上限了',
  interrupted: '被重启打断',
}
const ACTIVE: TaskStatus[] = ['queued', 'running', 'waiting_pc']
const RESUMABLE: TaskStatus[] = ['failed', 'stopped', 'limit', 'interrupted']
export const TOOL_LABEL: Record<string, string> = {
  read_page: '读网页',
  set_reminder: '设提醒',
  list_reminders: '看提醒',
  change_reminder: '改提醒',
  cancel_reminder: '取消提醒',
  remember: '记下一件事',
  write_memory: '写进回忆',
  draw_picture: '画图',
  download_file: '下载文件',
  recall_memories: '翻回忆档案',
  search_chat: '查聊天记录',
  list_files: '看文件夹',
  read_file: '读文件',
  write_file: '写文件',
  edit_file: '改文件',
  deliver_file: '交文件给你',
  run_command: '跑命令',
  win_list_files: '看电脑上的文件夹',
  win_read_file: '读电脑上的文件',
  win_write_file: '在电脑上写文件',
  win_edit_file: '改电脑上的文件',
  win_delete: '删电脑上的文件',
  win_move: '移动电脑上的文件',
  win_run_command: '在电脑上跑命令',
  win_open: '在电脑上打开',
}

const minutes = (s: number) => (s < 60 ? `${Math.round(s)} 秒` : `${Math.round(s / 60)} 分钟`)

/** Her tasks, kept fresh as they change. */
export function useTasks(botId: string) {
  const { subscribe } = useLive()
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  useEffect(() => {
    let cancelled = false
    getJson<{ tasks: Task[] }>(`/api/tasks?bot_id=${encodeURIComponent(botId)}`).then(
      (data) => !cancelled && (setTasks(data.tasks), setError('')),
      (e: Error) => !cancelled && setError(`读取失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [botId, nonce])
  useEffect(() => subscribe((item) => item.kind === 'task' && item.botId === botId && reload()), [subscribe, botId, reload])
  return { tasks, error, reload }
}

export function Tasks({ botId, name, onClose }: { botId: string; name: string; onClose: () => void }) {
  const { tasks, error, reload } = useTasks(botId)
  const [open, setOpen] = useState<number | null>(null)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <div className="min-w-0">
          <div className="font-medium">{name}的任务</div>
          <div className="text-xs text-muted-foreground">
            要花一阵子的事她会在后台一步步做，做完把结果发到聊天里。聊天里说「帮我查一下……整理成一份」她就会开任务。
          </div>
        </div>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
          回到聊天
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-sm">
        <Routines botId={botId} name={name} />
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {tasks?.length === 0 && <p className="text-muted-foreground">还没有任务。</p>}
        <ul className="space-y-2">
          {tasks?.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              open={open === t.id}
              onToggle={() => setOpen(open === t.id ? null : t.id)}
              onChanged={reload}
            />
          ))}
        </ul>
        <NewTask botId={botId} onAdded={reload} />
      </div>
    </div>
  )
}

export function TaskRow({ task, open, onToggle, onChanged, who }: {
  task: Task
  /** Whose it is, shown first (the console's 任务 page lists everyone's). */
  who?: string
  open: boolean
  onToggle: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const act = async (path: string, method: 'POST' | 'DELETE' = 'POST') => {
    setBusy(true)
    setError('')
    try {
      await sendJson(method, path)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const active = ACTIVE.includes(task.status)
  return (
    <li className="rounded-lg border">
      <div className="flex flex-wrap items-center gap-3 p-3">
        <button type="button" onClick={onToggle} aria-expanded={open} className="flex min-w-0 flex-1 items-start gap-2 text-left">
          {open ? <ChevronDown className="mt-0.5 size-4 shrink-0" /> : <ChevronRight className="mt-0.5 size-4 shrink-0" />}
          <span className="min-w-0">
            <span className="block font-medium break-words">{task.title}</span>
            <span className="block text-xs text-muted-foreground">
              <span className={cn(task.status === 'running' && 'text-primary', task.status === 'failed' && 'text-destructive')}>
                {STATUS_LABEL[task.status]}
              </span>
              {` · ${task.steps} 步 · ${minutes(task.seconds)} · ${formatTime(task.created_at)}`}
              {who && ` · ${who}`}
              {task.source === 'chat' && ' · 聊天里交给她的'}
            </span>
          </span>
        </button>
        {active && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(`/api/tasks/${task.id}/stop`)}>
            停下
          </Button>
        )}
        {RESUMABLE.includes(task.status) && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(`/api/tasks/${task.id}/resume`)}>
            接着做
          </Button>
        )}
        {!active && (
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive"
            disabled={busy}
            onClick={() => void act(`/api/tasks/${task.id}`, 'DELETE')}
          >
            删除
          </Button>
        )}
      </div>
      {error && <p role="alert" className="px-3 pb-2 text-xs text-destructive">{error}</p>}
      {open && <TaskDetail task={task} />}
    </li>
  )
}

function TaskDetail({ task }: { task: Task }) {
  const [log, setLog] = useState<Step[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    // Refetched whenever the task changes (the row is given a fresh object then).
    getJson<{ log: Step[] }>(`/api/tasks/${task.id}`).then(
      (data) => !cancelled && (setLog(data.log), setError('')),
      (e: Error) => !cancelled && setError(e.message),
    )
    return () => {
      cancelled = true
    }
  }, [task])
  const active = ACTIVE.includes(task.status)
  return (
    <div className="space-y-3 border-t bg-muted/30 p-3">
      <p className="text-xs whitespace-pre-wrap text-muted-foreground">要做的：{task.goal}</p>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {log?.length === 0 && <p className="text-xs text-muted-foreground">{active ? '还没开始用工具。' : '没有步骤。'}</p>}
      <ol className="space-y-1.5">{log?.map((s) => <StepLine key={s.id} step={s} />)}</ol>
      {task.error && <p className="text-xs text-destructive">出错：{task.error}</p>}
      {task.report && (
        <div className="rounded-md border bg-background p-2.5">
          <div className="pb-1 text-xs text-muted-foreground">她的汇报</div>
          <p className="whitespace-pre-wrap break-words">{task.report}</p>
        </div>
      )}
      {active && <AddNote taskId={task.id} />}
    </div>
  )
}

function StepLine({ step }: { step: Step }) {
  const [open, setOpen] = useState(false)
  if (step.kind === 'tool') {
    return (
      <li className="text-xs">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-1.5 text-left hover:text-foreground">
          {open ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
          <span className="font-medium">{TOOL_LABEL[step.tool] ?? step.tool}</span>
          <span className="truncate text-muted-foreground">{step.given}</span>
          {step.status === 'running' && <span className="shrink-0 text-primary">进行中…</span>}
          {step.ms !== null && <span className="ml-auto shrink-0 text-muted-foreground">{(step.ms / 1000).toFixed(1)} 秒</span>}
        </button>
        {open && (
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-background p-2 text-[11px] whitespace-pre-wrap break-words">
            {step.given}
            {'\n\n'}
            {step.result || '（还没有结果）'}
          </pre>
        )}
      </li>
    )
  }
  if (step.kind === 'approval') {
    return (
      <li className="text-xs text-amber-600 dark:text-amber-400">
        <span className="font-medium">要你点头：</span>
        {step.tool} {step.given} → {step.status === 'running' ? '等你回…' : step.result}
      </li>
    )
  }
  const label = { say: '她说', note: '你追加', stop: '停下', resume: '接着做', fold: '前面的步骤收成了小结' }[step.kind]
  return (
    <li className={cn('text-xs', step.kind === 'say' ? 'text-foreground' : 'text-muted-foreground')}>
      <span className="font-medium">{label}：</span>
      <span className="whitespace-pre-wrap break-words">{step.result}</span>
    </li>
  )
}

function AddNote({ taskId }: { taskId: number }) {
  const [text, setText] = useState('')
  const [state, setState] = useState<'idle' | 'busy' | 'sent' | string>('idle')
  const send = async () => {
    setState('busy')
    try {
      await sendJson('POST', `/api/tasks/${taskId}/notes`, { text: text.trim() })
      setText('')
      setState('sent')
    } catch (e) {
      setState((e as Error).message)
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input
        value={text}
        maxLength={1000}
        placeholder="追加要求（下一步开始前她会看到）"
        aria-label="追加要求"
        className="h-8 flex-1 text-xs"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && text.trim() && void send()}
      />
      <Button size="sm" variant="outline" disabled={!text.trim() || state === 'busy'} onClick={() => void send()}>
        追加
      </Button>
      {state === 'sent' && <span className="text-xs text-muted-foreground">她下一步会看到</span>}
      {state !== 'idle' && state !== 'busy' && state !== 'sent' && <span role="alert" className="text-xs text-destructive">{state}</span>}
    </div>
  )
}

function NewTask({ botId, onAdded }: { botId: string; onAdded: () => void }) {
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  const ids = { title: useId(), goal: useId() }
  const add = async () => {
    setState('busy')
    try {
      await sendJson('POST', '/api/tasks', { bot_id: botId, title: title.trim(), goal: goal.trim() })
      setTitle('')
      setGoal('')
      setState('idle')
      onAdded()
    } catch (e) {
      setState((e as Error).message)
    }
  }
  return (
    <div className="space-y-3 rounded-lg border border-dashed p-3">
      <div className="space-y-1">
        <Label htmlFor={ids.title}>交给她一件事</Label>
        <Input id={ids.title} value={title} maxLength={40} placeholder="比如：比较三款机械键盘" onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={ids.goal}>具体要求（可不填）</Label>
        <Textarea
          id={ids.goal}
          value={goal}
          maxLength={2000}
          rows={3}
          placeholder="范围、要点、想要什么样的结果"
          onChange={(e) => setGoal(e.target.value)}
        />
      </div>
      <div className="flex items-center gap-3">
        <p className="text-xs text-muted-foreground">她能读网页、翻回忆档案和聊天记录、设提醒，在自己的文件夹里读写文件、跑命令，做好的文件交到「文件」里。每个任务最多 40 步、30 分钟。</p>
        <Button size="sm" className="ml-auto shrink-0" disabled={!title.trim() || state === 'busy'} onClick={() => void add()}>
          <Plus className="size-4" />
          开始
        </Button>
      </div>
      {state !== 'idle' && state !== 'busy' && <p role="alert" className="text-xs text-destructive">{state}</p>}
    </div>
  )
}
