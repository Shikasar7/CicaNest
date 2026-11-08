import { useId, useState } from 'react'
import { BookOpen, Check, LoaderCircle, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { FirstLoad, ResultText, StaleNotice, type Result } from '@/components/Feedback'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { sendJson, type SlangEntry, type SlangRun, type SlangView } from '@/lib/api'
import { formatAgo, formatTime } from '@/lib/format'
import { editKeys } from '@/lib/forms'
import { useLive, useProviders } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { cn } from '@/lib/utils'

const SOURCE: Record<SlangEntry['source'], string> = { auto: '学到的', edited: '已确认', manual: '手加的' }

const groupLabel = (group: { key: string; name: string }) => group.name || `群 ${group.key.split(':').pop()}`

/** 黑话: what each group's own words mean, learned from its messages; bots use them where 「用黑话」 is on. */
export function Slang() {
  const { slangVersion } = useLive()
  const providers = useProviders()
  const [group, setGroup] = useState('')
  const view = useLiveData<SlangView>(`/api/slang?group=${encodeURIComponent(group)}`, [slangVersion])
  const { busy, error, run } = useAction(view.reload)
  const [learning, setLearning] = useState(false)
  const [learned, setLearned] = useState<Result | null>(null)
  const [adding, setAdding] = useState(false)
  const id = useId()
  const data = view.data

  if (view.missing && !data) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还没有黑话功能。重启 nest 后再打开这一页。
      </p>
    )
  }
  if (!data) return <FirstLoad error={view.error} what="黑话" />
  const current = data.groups.find((g) => g.key === data.group)

  const learnNow = async () => {
    setLearning(true)
    setLearned(null)
    try {
      const result = await sendJson<SlangRun>('POST', '/api/slang/learn', { group_key: data.group })
      setLearned(result.error ? { ok: false, text: result.error } : { ok: true, text: result.detail })
      view.reload()
    } catch (e) {
      setLearned({ ok: false, text: (e as Error).message })
    } finally {
      setLearning(false)
    }
  }
  const defaultModel = data.settings.default_provider

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        定期从群里的聊天找出反复出现、外人看不懂的词和梗，推断意思记在这里。Bot 页每个群的「用黑话」打开后，bot
        回复时会看到这批消息里出现的词条。
      </p>
      <StaleNotice error={view.error} />

      {data.groups.length === 0 ? (
        <section className="flex flex-col items-center rounded-2xl border bg-card px-6 py-12 text-center">
          <span aria-hidden className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
            <BookOpen className="size-5" />
          </span>
          <h2 className="mt-4 text-base font-semibold tracking-tight">还没有群</h2>
          <p className="mt-1.5 max-w-md text-sm text-muted-foreground">bot 在群里收到消息后，这里就能选群学习黑话。</p>
        </section>
      ) : (
        <section className="space-y-4 rounded-2xl border bg-card p-5">
          <div className="flex flex-wrap items-center gap-3">
            <Label htmlFor={`${id}-group`} className="text-sm font-semibold">
              群
            </Label>
            <NativeSelect id={`${id}-group`} value={data.group} onChange={(e) => {
                setGroup(e.target.value)
                setLearned(null)
              }}>
              {data.groups.map((g) => (
                <option key={g.key} value={g.key}>
                  {groupLabel(g)}（{g.terms} 条）
                </option>
              ))}
            </NativeSelect>
            {current && (
              <label className="flex items-center gap-2 text-sm">
                <Switch
                  checked={current.learn}
                  disabled={busy}
                  onCheckedChange={(next) =>
                    void run(() =>
                      sendJson('PATCH', `/api/groups/${encodeURIComponent(current.key)}/features`, { learn_slang: next }),
                    )
                  }
                />
                自动学习
              </label>
            )}
            <Button size="sm" variant="outline" disabled={learning} onClick={() => void learnNow()}>
              {learning ? <LoaderCircle className="animate-spin" /> : <Sparkles />}
              立即学习
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            自动学习：新增 60 条群友消息、并且距离上次学习超过 12 小时，就学一次（每次调用一次模型）。
            {current?.learned_at ? `上次学习 ${formatAgo(current.learned_at)}。` : ''}
          </p>
          <ResultText result={learned} />

          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor={`${id}-model`} className="text-xs font-normal text-muted-foreground">
              学习用的模型
            </Label>
            <NativeSelect
              id={`${id}-model`}
              value={data.settings.provider_id ?? ''}
              disabled={busy}
              onChange={(e) => void run(() => sendJson('PATCH', '/api/slang/settings', { provider_id: e.target.value || null }))}
            >
              <option value="">默认：第一个 bot 的模型{defaultModel ? `（${defaultModel}）` : ''}</option>
              {(providers.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}（{p.model}）
                </option>
              ))}
            </NativeSelect>
            {!data.settings.provider_loaded && (
              <span role="alert" className="text-xs text-destructive">
                这个模型没有加载，学习会失败
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

      {current && (
        <section className="rounded-2xl border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-3">
            <h2 className="text-sm font-semibold">{groupLabel(current)}的词条</h2>
            {!adding && (
              <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
                <Plus />
                手动添加
              </Button>
            )}
          </div>
          {adding && (
            <div className="border-b px-5 py-3">
              <EntryForm
                entry={null}
                onCancel={() => setAdding(false)}
                onSave={(body) =>
                  sendJson('POST', '/api/slang', { group_key: current.key, ...body }).then(() => {
                    setAdding(false)
                    view.reload()
                  })
                }
              />
            </div>
          )}
          {data.entries.length === 0 ? (
            <p className="px-5 py-6 text-sm text-muted-foreground">
              还没有词条。打开自动学习，或者点「立即学习」（至少要有 10 条新消息）。
            </p>
          ) : (
            <ul className="divide-y">
              {data.entries.map((entry) => (
                <EntryRow key={entry.id} entry={entry} reload={view.reload} />
              ))}
            </ul>
          )}
        </section>
      )}

      {data.runs.length > 0 && (
        <section className="rounded-2xl border bg-card px-5 py-3">
          <h2 className="mb-2 text-sm font-semibold">最近几次学习</h2>
          <ul className="space-y-1 text-xs">
            {data.runs.map((r) => (
              <li key={r.id} className="flex gap-3">
                <span className="shrink-0 text-muted-foreground tabular-nums">{formatTime(r.started_at)}</span>
                <span className={cn(r.error && 'text-destructive')}>{r.error ?? r.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function EntryRow({ entry, reload }: { entry: SlangEntry; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const [editing, setEditing] = useState(false)
  const remove = () => {
    if (!window.confirm(`删掉「${entry.term}」？以后学习时可能又会学回来。`)) return
    void run(() => sendJson('DELETE', `/api/slang/${entry.id}`))
  }
  if (editing) {
    return (
      <li className="px-5 py-3">
        <EntryForm
          entry={entry}
          onCancel={() => setEditing(false)}
          onSave={(body) =>
            sendJson('PATCH', `/api/slang/${entry.id}`, body).then(() => {
              setEditing(false)
              reload()
            })
          }
        />
      </li>
    )
  }
  return (
    <li className="flex flex-wrap items-start gap-x-4 gap-y-1 px-5 py-3">
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm">
          <span className="font-medium">{entry.term}</span>
          <span className="text-muted-foreground">：</span>
          {entry.meaning}
        </p>
        {entry.example && <p className="text-xs text-muted-foreground">例：{entry.example}</p>}
        <p className="text-xs text-muted-foreground tabular-nums">
          <span className={cn(entry.source === 'auto' ? 'text-amber-700 dark:text-amber-400' : '')}>{SOURCE[entry.source]}</span> ·
          出现 {entry.hits} 次 · 最近一次 {formatAgo(entry.last_seen_at)}
        </p>
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="flex gap-1">
        {entry.source === 'auto' && (
          <Button size="xs" variant="ghost" disabled={busy} onClick={() => void run(() => sendJson('PATCH', `/api/slang/${entry.id}`, {}))}>
            <Check />
            确认
          </Button>
        )}
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

function EntryForm({
  entry,
  onCancel,
  onSave,
}: {
  entry: SlangEntry | null
  onCancel: () => void
  onSave: (body: { term: string; meaning: string; example: string }) => Promise<unknown>
}) {
  const id = useId()
  const [term, setTerm] = useState(entry?.term ?? '')
  const [meaning, setMeaning] = useState(entry?.meaning ?? '')
  const [example, setExample] = useState(entry?.example ?? '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const save = async () => {
    setBusy(true)
    setProblem('')
    try {
      await onSave({ term, meaning, example })
    } catch (e) {
      setProblem((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const keys = editKeys({ onEnter: () => void save(), onEscape: onCancel })
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-[10rem_1fr]">
        <div className="space-y-1">
          <Label htmlFor={`${id}-term`} className="text-xs">
            词
          </Label>
          <Input id={`${id}-term`} value={term} maxLength={12} autoFocus onChange={(e) => setTerm(e.target.value)} onKeyDown={keys} />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${id}-meaning`} className="text-xs">
            意思
          </Label>
          <Input id={`${id}-meaning`} value={meaning} maxLength={60} onChange={(e) => setMeaning(e.target.value)} onKeyDown={keys} />
        </div>
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-example`} className="text-xs">
          例句（可以不填）
        </Label>
        <Input id={`${id}-example`} value={example} maxLength={120} onChange={(e) => setExample(e.target.value)} onKeyDown={keys} />
      </div>
      <p className="text-xs text-muted-foreground">改过或手加的词条，自动学习不会再改它的意思。</p>
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
