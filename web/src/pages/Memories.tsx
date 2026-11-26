import { useId, useState } from 'react'
import { Brain, LoaderCircle, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { FirstLoad, ResultText, StaleNotice, type Result } from '@/components/Feedback'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type Memory, type MemoryKind, type MemoryRun, type MemoryView } from '@/lib/api'
import { formatAgo } from '@/lib/format'
import { editKeys } from '@/lib/forms'
import { useLive, useProviders } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { cn } from '@/lib/utils'

const KIND: Record<MemoryKind, string> = { like: '喜好', fact: '情况', promise: '约定' }
const CONTENT_MAX = 60

type Person = { id: string; name: string; memories: Memory[] }

function people(memories: Memory[]): Person[] {
  const found = new Map<string, Person>()
  for (const memory of memories) {
    const person = found.get(memory.person_id) ?? { id: memory.person_id, name: memory.person, memories: [] }
    person.memories.push(memory)
    found.set(memory.person_id, person)
  }
  const order: Record<MemoryKind, number> = { promise: 0, like: 1, fact: 2 }
  for (const person of found.values()) person.memories.sort((a, b) => order[a.kind] - order[b.kind] || a.created_at - b.created_at)
  return [...found.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh'))
}

function dayLabel(due: string): string {
  const [, month, day] = due.split('-').map(Number)
  return `${month}月${day}日`
}

/** 长期记忆: what the bots remember of people, per group or private chat. */
export function Memories() {
  const { memoriesVersion } = useLive()
  const providers = useProviders()
  const [place, setPlace] = useState('')
  const view = useLiveData<MemoryView>(`/api/memories?place=${encodeURIComponent(place)}`, [memoriesVersion])
  const { busy, error, run } = useAction(view.reload)
  const [learning, setLearning] = useState(false)
  const [learned, setLearned] = useState<Result | null>(null)
  const id = useId()
  const data = view.data

  if (view.missing && !data) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还没有长期记忆。重启 nest 后再打开这一页。
      </p>
    )
  }
  if (!data) return <FirstLoad error={view.error} what="记忆" />
  const current = data.places.find((p) => p.key === data.place)

  const learnNow = async () => {
    setLearning(true)
    setLearned(null)
    try {
      const result = await sendJson<MemoryRun>('POST', '/api/memories/learn', { place: data.place })
      setLearned(result.error ? { ok: false, text: result.error } : { ok: true, text: result.detail })
      view.reload()
    } catch (e) {
      setLearned({ ok: false, text: (e as Error).message })
    } finally {
      setLearning(false)
    }
  }
  const forget = () => {
    if (!current) return
    if (!window.confirm(`清空「${current.name}」里记下的全部 ${current.count} 条？删掉就找不回来了。以后只从新的聊天里记。`)) return
    void run(() => sendJson('POST', '/api/memories/forget', { place: current.key }))
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Bot 记住的群友的事：喜好、情况，和跟某个 bot 的约定。在伙伴页某个群或私聊的「长期记忆」打开后，隔一阵从那里的聊天整理一次；有人说话时，bot
        会看到记得的关于这个人的事。只在记下的地方用。改过或手加的，自动整理不会再动它。
      </p>
      <StaleNotice error={view.error} />

      {data.places.length === 0 ? (
        <section className="flex flex-col items-center rounded-2xl border bg-card px-6 py-12 text-center">
          <span aria-hidden className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
            <Brain className="size-5" />
          </span>
          <h2 className="mt-4 text-base font-semibold tracking-tight">还没有开长期记忆的地方</h2>
          <p className="mt-1.5 max-w-md text-sm text-muted-foreground">
            到伙伴页，在某个群或私聊的设置里打开「长期记忆」，这里就能看到记下的事。
          </p>
        </section>
      ) : (
        <section className="space-y-4 rounded-2xl border bg-card p-5">
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor={`${id}-place`} className="text-sm font-semibold">
              地方
            </Label>
            <NativeSelect
              id={`${id}-place`}
              value={data.place}
              onChange={(e) => {
                setPlace(e.target.value)
                setLearned(null)
              }}
            >
              {data.places.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}（{p.count} 条）
                </option>
              ))}
            </NativeSelect>
            <Button size="sm" variant="outline" disabled={learning || !current?.bots.length} onClick={() => void learnNow()}>
              {learning ? <LoaderCircle className="animate-spin" /> : <Sparkles />}
              立即整理
            </Button>
            {current && current.count > 0 && (
              <Button size="sm" variant="ghost" className="text-destructive" disabled={busy} onClick={forget}>
                <Trash2 />
                清空这里的记忆
              </Button>
            )}
          </div>
          {current && (
            <p className="text-xs text-muted-foreground">
              {current.bots.length
                ? `开着的 bot：${current.bots.join('、')}。群里新增 20 条（私聊 8 条）群友消息、并且距离上次超过半小时，就整理一次（每次调用一次模型）。`
                : '这里现在没有 bot 开着长期记忆：不再整理，记下的也不会告诉 bot。'}
              {current.learned_at ? `上次整理 ${formatAgo(current.learned_at)}：` : ''}
              {current.learned_at && (
                <span className={cn(current.error && 'text-destructive')}>{current.error || current.detail}</span>
              )}
            </p>
          )}
          <ResultText result={learned} />

          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor={`${id}-model`} className="text-xs font-normal text-muted-foreground">
              整理用的模型
            </Label>
            <NativeSelect
              id={`${id}-model`}
              value={data.settings.provider_id ?? ''}
              disabled={busy}
              onChange={(e) => void run(() => sendJson('PATCH', '/api/memories/settings', { provider_id: e.target.value || null }))}
            >
              <option value="">默认：这里开着记忆的 bot 的模型</option>
              {(providers.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}（{p.model}）
                </option>
              ))}
            </NativeSelect>
            {!data.settings.provider_loaded && (
              <span role="alert" className="text-xs text-destructive">
                这个模型没有加载，整理会失败
              </span>
            )}
          </div>
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
        </section>
      )}

      {current &&
        (data.memories.length === 0 ? (
          <section className="rounded-2xl border bg-card px-5 py-6 text-sm text-muted-foreground">
            这里还没记下什么。聊天攒够了会自动整理，也可以点「立即整理」（至少要有 3 条新消息）。
          </section>
        ) : (
          people(data.memories).map((person) => (
            <PersonCard key={person.id} person={person} view={data} reload={view.reload} />
          ))
        ))}
    </div>
  )
}

function PersonCard({ person, view, reload }: { person: Person; view: MemoryView; reload: () => void }) {
  const [adding, setAdding] = useState(false)
  return (
    <section className="rounded-2xl border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-3">
        <h2 className="text-sm font-semibold">
          {person.name}
          <span className="ml-2 text-xs font-normal text-muted-foreground tabular-nums">{person.id}</span>
        </h2>
        {!adding && (
          <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
            <Plus />
            添加
          </Button>
        )}
      </div>
      {adding && (
        <div className="border-b px-5 py-3">
          <MemoryForm
            memory={null}
            bots={view.bots}
            onCancel={() => setAdding(false)}
            onSave={(body) =>
              sendJson('POST', '/api/memories', { place: view.place, person_id: person.id, ...body }).then(() => {
                setAdding(false)
                reload()
              })
            }
          />
        </div>
      )}
      <ul className="divide-y">
        {person.memories.map((memory) => (
          <MemoryRow key={memory.id} memory={memory} bots={view.bots} reload={reload} />
        ))}
      </ul>
    </section>
  )
}

function MemoryRow({ memory, bots, reload }: { memory: Memory; bots: MemoryView['bots']; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const [editing, setEditing] = useState(false)
  const remove = () => {
    if (!window.confirm(`删掉「${memory.content}」？`)) return
    void run(() => sendJson('DELETE', `/api/memories/${memory.id}`))
  }
  if (editing) {
    return (
      <li className="px-5 py-3">
        <MemoryForm
          memory={memory}
          bots={bots}
          onCancel={() => setEditing(false)}
          onSave={(body) =>
            sendJson('PATCH', `/api/memories/${memory.id}`, { content: body.content, due: body.due ?? '' }).then(() => {
              setEditing(false)
              reload()
            })
          }
        />
      </li>
    )
  }
  const about =
    memory.kind === 'promise'
      ? `和${memory.bot_name || '某个 bot'}约的${memory.due ? `，${dayLabel(memory.due)}` : ''}`
      : ''
  return (
    <li className="flex flex-wrap items-start gap-x-4 gap-y-1 px-5 py-3">
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm">
          <span
            className={cn(
              'mr-2 rounded-md px-1.5 py-0.5 text-[11px]',
              memory.kind === 'promise' ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
            )}
          >
            {KIND[memory.kind]}
          </span>
          {memory.content}
        </p>
        <p className="text-xs text-muted-foreground">
          {[about, memory.source === 'manual' ? '手加或改过' : '整理出来的', memory.used_at ? `上次用到 ${formatAgo(memory.used_at)}` : '还没用到']
            .filter(Boolean)
            .join('，')}
        </p>
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="flex gap-1">
        <Button size="xs" variant="ghost" disabled={busy} onClick={() => setEditing(true)}>
          <Pencil />
          改
        </Button>
        <Button size="xs" variant="ghost" className="text-destructive" disabled={busy} onClick={remove}>
          <Trash2 />
          删除
        </Button>
      </div>
    </li>
  )
}

type MemoryBody = { kind: MemoryKind; content: string; bot_id?: string; due?: string }

function MemoryForm({
  memory,
  bots,
  onCancel,
  onSave,
}: {
  memory: Memory | null
  bots: MemoryView['bots']
  onCancel: () => void
  onSave: (body: MemoryBody) => Promise<unknown>
}) {
  const id = useId()
  const [kind, setKind] = useState<MemoryKind>(memory?.kind ?? 'like')
  const [content, setContent] = useState(memory?.content ?? '')
  const [bot, setBot] = useState(memory?.bot_id ?? bots[0]?.id ?? '')
  const [due, setDue] = useState(memory?.due ?? '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const save = async () => {
    setBusy(true)
    setProblem('')
    try {
      await onSave(kind === 'promise' ? { kind, content, bot_id: bot, due } : { kind, content })
    } catch (e) {
      setProblem((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const keys = editKeys({ onEnter: () => void save(), onEscape: onCancel })
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-[7rem_1fr]">
        <div className="space-y-1">
          <Label htmlFor={`${id}-kind`} className="text-xs">
            种类
          </Label>
          <NativeSelect
            id={`${id}-kind`}
            className="w-full"
            value={kind}
            disabled={memory !== null}
            onChange={(e) => setKind(e.target.value as MemoryKind)}
          >
            <option value="like">喜好</option>
            <option value="fact">情况</option>
            <option value="promise">约定</option>
          </NativeSelect>
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${id}-content`} className="text-xs">
            内容
          </Label>
          <Input
            id={`${id}-content`}
            value={content}
            maxLength={CONTENT_MAX}
            autoFocus
            placeholder={kind === 'promise' ? '比如：周五提醒他交报告' : kind === 'like' ? '比如：喜欢猫，不吃香菜' : '比如：在上海上班'}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={keys}
          />
        </div>
      </div>
      {kind === 'promise' && (
        <div className="grid gap-2 sm:grid-cols-[10rem_10rem]">
          <div className="space-y-1">
            <Label htmlFor={`${id}-bot`} className="text-xs">
              和哪个 bot 约的
            </Label>
            <NativeSelect
              id={`${id}-bot`}
              className="w-full"
              value={bot}
              disabled={memory !== null}
              onChange={(e) => setBot(e.target.value)}
            >
              {bots.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${id}-due`} className="text-xs">
              日期（可以不填）
            </Label>
            <Input id={`${id}-due`} type="date" value={due} onChange={(e) => setDue(e.target.value)} onKeyDown={keys} />
          </div>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        用第三人称写，不用写名字。约定过了日期三天后自动忘掉；没写日期的约定两个月没提也会忘掉（手加的除外）。
      </p>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => void save()}>
          {busy && <LoaderCircle className="animate-spin" />}
          保存
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
          取消
        </Button>
        {problem && (
          <span role="alert" className="text-xs text-destructive">
            {problem}
          </span>
        )}
      </div>
    </div>
  )
}
