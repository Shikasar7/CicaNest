import { useCallback, useEffect, useId, useState } from 'react'
import { ChevronDown, ChevronRight, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { getJson, sendJson } from '@/lib/api'
import { formatTime } from '@/lib/format'
import { useLive } from '@/lib/live'

import { STATUS_LABEL, type TaskStatus } from './Tasks'

/** 定时任务: what she does by herself at set times (每天 / 工作日 / 每周 / 每月) — each time a task, its report
 * sent to the chat. Nobody is there to nod then, so steps that need your nod are skipped and she says so. */

type Repeat =
  | 'daily'
  | 'weekdays'
  | 'weekly'
  | 'monthly'
  | 'every_1h'
  | 'every_2h'
  | 'every_3h'
  | 'every_4h'
  | 'every_6h'
  | 'every_12h'
type Routine = {
  id: number
  title: string
  goal: string
  repeat: Repeat
  when: string // the first time, local "YYYY-MM-DDTHH:MM"
  rule: string // 「每天 08:00」
  next: string // 「明天 08:00」, '' when switched off
  enabled: boolean
  also_qq: boolean
  source: 'chat' | 'console'
}
type Run = {
  id: number
  at: number
  task_id: number | null
  skipped: string
  status: TaskStatus | null
  report: string | null
  error: string | null
}

const REPEATS: [Repeat, string][] = [
  ['daily', '每天'],
  ['weekdays', '工作日每天'],
  ['weekly', '每周'],
  ['monthly', '每月'],
  ['every_1h', '每小时'],
  ['every_2h', '每 2 小时'],
  ['every_3h', '每 3 小时'],
  ['every_4h', '每 4 小时'],
  ['every_6h', '每 6 小时'],
  ['every_12h', '每 12 小时'],
]
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

const pad = (n: number) => String(n).padStart(2, '0')

/** The first time for a rule, as local "YYYY-MM-DDTHH:MM": today (or the coming weekday / this month's day).
 * A time already gone is moved on by nest itself. */
function firstTime(time: string, repeat: Repeat, weekday: number, day: number): string {
  const now = new Date()
  const at = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (repeat === 'weekly') at.setDate(at.getDate() + ((weekday - ((at.getDay() + 6) % 7) + 7) % 7))
  if (repeat === 'monthly') {
    const last = new Date(at.getFullYear(), at.getMonth() + 1, 0).getDate()
    at.setDate(Math.min(day, last))
  }
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${time}`
}

function useRoutines(botId: string) {
  const { subscribe } = useLive()
  const [routines, setRoutines] = useState<Routine[] | null>(null)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  useEffect(() => {
    let cancelled = false
    getJson<{ routines: Routine[] }>(`/api/routines?bot_id=${encodeURIComponent(botId)}`).then(
      (data) => !cancelled && (setRoutines(data.routines), setError('')),
      (e: Error) => !cancelled && setError(`读取失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [botId, nonce])
  // A run starts or ends a task; she sets one in the chat and then says so.
  useEffect(
    () =>
      subscribe((item) => {
        if ((item.kind === 'task' && item.botId === botId) || item.kind === 'message') reload()
      }),
    [subscribe, botId, reload],
  )
  return { routines, error, reload }
}

export function Routines({ botId, name }: { botId: string; name: string }) {
  const { routines, error, reload } = useRoutines(botId)
  const [open, setOpen] = useState<number | null>(null)
  const [adding, setAdding] = useState(false)
  return (
    <section className="space-y-2">
      <div className="flex items-baseline gap-2">
        <h3 className="font-medium">定时</h3>
        <p className="text-xs text-muted-foreground">
          到点{name}会自己做，做完把结果发到聊天里。那时你不一定在，要你点头的步骤她会跳过，在汇报里告诉你。
        </p>
      </div>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      <ul className="space-y-2">
        {routines?.map((r) => (
          <RoutineRow
            key={r.id}
            routine={r}
            open={open === r.id}
            onToggle={() => setOpen(open === r.id ? null : r.id)}
            onChanged={reload}
          />
        ))}
      </ul>
      {adding ? (
        <NewRoutine botId={botId} onDone={() => (setAdding(false), reload())} onCancel={() => setAdding(false)} />
      ) : (
        <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
          <Plus className="size-4" />
          {routines?.length ? '再设一个' : '设一个定时任务'}
        </Button>
      )}
    </section>
  )
}

function RoutineRow({ routine, open, onToggle, onChanged }: {
  routine: Routine
  open: boolean
  onToggle: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const act = async (method: 'POST' | 'PATCH' | 'DELETE', path: string, body?: unknown, done?: string) => {
    setBusy(true)
    setError('')
    try {
      await sendJson(method, path, body)
      if (done) setNote(done)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <li className="rounded-lg border">
      <div className="flex flex-wrap items-center gap-3 p-3">
        <button type="button" onClick={onToggle} aria-expanded={open} className="flex min-w-0 flex-1 items-start gap-2 text-left">
          {open ? <ChevronDown className="mt-0.5 size-4 shrink-0" /> : <ChevronRight className="mt-0.5 size-4 shrink-0" />}
          <span className="min-w-0">
            <span className="block font-medium break-words">{routine.title}</span>
            <span className="block text-xs text-muted-foreground">
              {routine.rule}
              {routine.enabled ? ` · 下次 ${routine.next}` : ' · 停着'}
              {routine.also_qq && ' · 汇报也发 QQ'}
              {routine.source === 'chat' &&
                (routine.enabled ? ' · 聊天里让她设的' : ' · 她在聊天里设的：展开看过要做的事，再打开开关')}
            </span>
          </span>
        </button>
        <Switch
          checked={routine.enabled}
          disabled={busy}
          aria-label={routine.enabled ? '停用' : '启用'}
          onCheckedChange={(on) => void act('PATCH', `/api/routines/${routine.id}`, { enabled: on })}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void act('POST', `/api/routines/${routine.id}/run`, undefined, '开始做了，在下面的任务里能看到')}
        >
          现在做一次
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="text-destructive"
          disabled={busy}
          onClick={() => void act('DELETE', `/api/routines/${routine.id}`)}
        >
          删除
        </Button>
      </div>
      {error && <p role="alert" className="px-3 pb-2 text-xs text-destructive">{error}</p>}
      {note && !error && <p className="px-3 pb-2 text-xs text-muted-foreground">{note}</p>}
      {open && <RoutineRuns routine={routine} />}
    </li>
  )
}

function RoutineRuns({ routine }: { routine: Routine }) {
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    getJson<{ runs: Run[] }>(`/api/routines/${routine.id}/runs`).then(
      (data) => !cancelled && setRuns(data.runs),
      (e: Error) => !cancelled && setError(`读取失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [routine])
  return (
    <div className="space-y-2 border-t px-3 py-2 text-xs">
      <p className="whitespace-pre-wrap text-muted-foreground">{routine.goal}</p>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {runs?.length === 0 && <p className="text-muted-foreground">还没到过点。</p>}
      <ul className="space-y-1">
        {runs?.map((run) => (
          <li key={run.id} className="flex gap-2">
            <span className="shrink-0 text-muted-foreground">{formatTime(run.at)}</span>
            <span className="min-w-0 break-words">
              {run.skipped
                ? `没做：${run.skipped}`
                : run.status
                  ? `${STATUS_LABEL[run.status]}${run.report ? `：${run.report.slice(0, 120)}` : ''}`
                  : '任务已删除'}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function NewRoutine({ botId, onDone, onCancel }: { botId: string; onDone: () => void; onCancel: () => void }) {
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [time, setTime] = useState('08:00')
  const [repeat, setRepeat] = useState<Repeat>('daily')
  const [weekday, setWeekday] = useState(0)
  const [day, setDay] = useState(1)
  const [alsoQq, setAlsoQq] = useState(false)
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  const ids = { title: useId(), goal: useId(), time: useId(), repeat: useId(), weekday: useId(), day: useId(), qq: useId() }
  const add = async () => {
    setState('busy')
    try {
      await sendJson('POST', '/api/routines', {
        bot_id: botId,
        title: title.trim(),
        goal: goal.trim(),
        when: firstTime(time, repeat, weekday, day),
        repeat,
        also_qq: alsoQq,
      })
      setState('idle')
      onDone()
    } catch (e) {
      setState((e as Error).message)
    }
  }
  return (
    <div className="space-y-3 rounded-lg border border-dashed p-3">
      <div className="space-y-1">
        <Label htmlFor={ids.title}>做什么</Label>
        <Input id={ids.title} value={title} maxLength={40} placeholder="比如：早报" onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={ids.goal}>每次要做成什么样（可不填）</Label>
        <Textarea
          id={ids.goal}
          value={goal}
          maxLength={2000}
          rows={3}
          placeholder="比如：看看今天的天气和我关注的新闻，挑要紧的告诉我"
          onChange={(e) => setGoal(e.target.value)}
        />
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor={ids.repeat}>多久一次</Label>
          <NativeSelect id={ids.repeat} value={repeat} onChange={(e) => setRepeat(e.target.value as Repeat)}>
            {REPEATS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </NativeSelect>
        </div>
        {repeat === 'weekly' && (
          <div className="space-y-1">
            <Label htmlFor={ids.weekday}>星期几</Label>
            <NativeSelect id={ids.weekday} value={weekday} onChange={(e) => setWeekday(Number(e.target.value))}>
              {WEEKDAYS.map((label, i) => (
                <option key={label} value={i}>
                  星期{label}
                </option>
              ))}
            </NativeSelect>
          </div>
        )}
        {repeat === 'monthly' && (
          <div className="space-y-1">
            <Label htmlFor={ids.day}>几号</Label>
            <Input
              id={ids.day}
              type="number"
              min={1}
              max={31}
              className="w-20"
              value={day}
              onChange={(e) => setDay(Math.min(31, Math.max(1, Number(e.target.value) || 1)))}
            />
          </div>
        )}
        <div className="space-y-1">
          <Label htmlFor={ids.time}>{repeat.startsWith('every_') ? '从几点起' : '几点'}</Label>
          <Input id={ids.time} type="time" className="w-28" value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
        <label htmlFor={ids.qq} className="flex items-center gap-2 pb-2 text-xs">
          <Switch id={ids.qq} size="sm" checked={alsoQq} onCheckedChange={setAlsoQq} />
          汇报也发到 QQ
        </label>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={!title.trim() || !time || state === 'busy'} onClick={() => void add()}>
          设好
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
      {state !== 'idle' && state !== 'busy' && <p role="alert" className="text-xs text-destructive">{state}</p>}
    </div>
  )
}
