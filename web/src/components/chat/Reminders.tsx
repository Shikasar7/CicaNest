import { useEffect, useId, useState } from 'react'
import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { getJson, sendJson } from '@/lib/api'
import { cn } from '@/lib/utils'

/** 提醒: what she reminds you of, and when. Also made in the chat by saying 「明天三点提醒我开会」. At its time she
 * says it in her own words: on the desk when the desktop app is open, else on QQ (and on QQ too when asked). */

type Repeat = '' | 'daily' | 'weekdays' | 'weekly' | 'monthly'
type Reminder = {
  id: number
  text: string
  due: string
  when: string
  repeat: Repeat
  also_qq: boolean
  active: boolean
  source: 'chat' | 'console'
}
type Listing = { reminders: Reminder[]; desk_open: boolean }

const REPEATS: { value: Repeat; label: string }[] = [
  { value: '', label: '一次' },
  { value: 'daily', label: '每天' },
  { value: 'weekdays', label: '工作日' },
  { value: 'weekly', label: '每周' },
  { value: 'monthly', label: '每月' },
]
const repeatLabel = (r: Repeat) => REPEATS.find((x) => x.value === r)?.label ?? r

function inAnHour(): string {
  const d = new Date(Date.now() + 3600_000)
  d.setMinutes(0, 0, 0)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function Reminders({ botId, name, hasQQ, onClose }: {
  botId: string
  name: string
  hasQQ: boolean
  onClose: () => void
}) {
  const [listing, setListing] = useState<Listing | null>(null)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)
  const reload = () => setNonce((n) => n + 1)

  useEffect(() => {
    let cancelled = false
    getJson<Listing>(`/api/reminders?bot_id=${encodeURIComponent(botId)}`).then(
      (data) => !cancelled && (setListing(data), setError('')),
      (e: Error) => !cancelled && setError(`读取失败：${e.message}`),
    )
    return () => {
      cancelled = true
    }
  }, [botId, nonce])

  const act = async (work: () => Promise<unknown>) => {
    try {
      await work()
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <div className="min-w-0">
          <div className="font-medium">{name}的提醒</div>
          <div className="text-xs text-muted-foreground">
            到点她会用自己的话提醒你。也可以在聊天里直接说「明天下午三点提醒我开会」。
          </div>
        </div>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
          回到聊天
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-sm">
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {listing && hasQQ && (
          <p className="text-xs text-muted-foreground">
            {listing.desk_open
              ? '桌面端开着：提醒出现在桌面上；勾了「也发 QQ」的同时发到 QQ。'
              : '桌面端没开：到点会发到 QQ 私聊。'}
          </p>
        )}
        {listing?.reminders.length === 0 && <p className="text-muted-foreground">还没有提醒。</p>}
        <ul className="space-y-2">
          {listing?.reminders.map((r) => (
            <li key={r.id} className={cn('flex flex-wrap items-center gap-3 rounded-lg border p-3', !r.active && 'opacity-60')}>
              <Switch
                checked={r.active}
                aria-label={r.active ? '关掉这个提醒' : '打开这个提醒'}
                onCheckedChange={(on) => void act(() => sendJson('PATCH', `/api/reminders/${r.id}`, { active: on }))}
              />
              <div className="min-w-0 flex-1">
                <div className="font-medium break-words">{r.text}</div>
                <div className="text-xs text-muted-foreground">
                  {r.active ? `下次：${r.when}` : '已经提醒过 / 已关'}
                  {r.repeat && ` · ${repeatLabel(r.repeat)}`}
                  {r.also_qq && ' · 也发 QQ'}
                  {r.source === 'chat' && ' · 聊天里说的'}
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive"
                onClick={() => void act(() => sendJson('DELETE', `/api/reminders/${r.id}`))}
              >
                删除
              </Button>
            </li>
          ))}
        </ul>
        <NewReminder botId={botId} hasQQ={hasQQ} onAdded={reload} />
      </div>
    </div>
  )
}

function NewReminder({ botId, hasQQ, onAdded }: { botId: string; hasQQ: boolean; onAdded: () => void }) {
  const [text, setText] = useState('')
  const [due, setDue] = useState(inAnHour)
  const [repeat, setRepeat] = useState<Repeat>('')
  const [alsoQQ, setAlsoQQ] = useState(false)
  const [state, setState] = useState<'idle' | 'busy' | string>('idle')
  const ids = { text: useId(), due: useId(), repeat: useId(), qq: useId() }

  const add = async () => {
    setState('busy')
    try {
      await sendJson('POST', '/api/reminders', { bot_id: botId, text: text.trim(), due, repeat, also_qq: alsoQQ })
      setText('')
      setState('idle')
      onAdded()
    } catch (e) {
      setState((e as Error).message)
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-dashed p-3">
      <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
        <div className="space-y-1">
          <Label htmlFor={ids.text}>提醒我</Label>
          <Input
            id={ids.text}
            value={text}
            maxLength={60}
            placeholder="喝水、开会、吃药…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && text.trim() && void add()}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={ids.due}>时间</Label>
          <Input id={ids.due} type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor={ids.repeat}>重复</Label>
          <NativeSelect id={ids.repeat} value={repeat} onChange={(e) => setRepeat(e.target.value as Repeat)}>
            {REPEATS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {hasQQ && (
          <label htmlFor={ids.qq} className="flex items-center gap-2 text-xs text-muted-foreground">
            <Switch id={ids.qq} checked={alsoQQ} onCheckedChange={setAlsoQQ} />
            重要：桌面端开着也同时发到 QQ
          </label>
        )}
        <Button size="sm" className="ml-auto" disabled={!text.trim() || !due || state === 'busy'} onClick={() => void add()}>
          <Plus className="size-4" />
          加一个提醒
        </Button>
      </div>
      {state !== 'idle' && state !== 'busy' && <p role="alert" className="text-xs text-destructive">{state}</p>}
    </div>
  )
}
