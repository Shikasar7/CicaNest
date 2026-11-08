import { useId, useState, type FormEvent } from 'react'
import { ChevronDown, Library, LoaderCircle, Plus, RefreshCw, Search, Trash2 } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { ForwardTo, Gaps, SourceDir } from '@/components/knowledge/Gaps'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type AskResult, type KnowledgeBaseView, type KnowledgeView } from '@/lib/api'
import { formatAgo } from '@/lib/format'
import { useLiveData } from '@/lib/live-data'
import { cn } from '@/lib/utils'

const FILE_LABEL: Record<string, string> = {
  'overview.md': '简介',
  'features.md': '功能',
  'errors.md': '报错',
  'faq.md': '常见问题',
  'versions.md': '版本',
  'notes.md': '补充',
}
// The order 试问 shows each search in: the three that find candidates, how they were fused, the reranker.
const STEPS = ['关键词', '向量', '报错原文', '融合', '重排']

const groupLabel = (group: { group_key: string; name: string }) => group.name || `群 ${group.group_key.split(':').pop()}`

/** 项目资料: the help docs a bot answers a project's questions from (答题 in the groups bound to them). */
export function Knowledge() {
  const view = useLiveData<KnowledgeView>('/api/knowledge', [])
  const data = view.data
  if (view.missing && !data) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还没有资料功能。重启 nest 后再打开这一页。
      </p>
    )
  }
  if (!data) return <FirstLoad error={view.error} what="资料" />

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        项目的帮助资料（功能、报错、常见问题）。群绑定了资料库、bot 在那个群开了「答题」时，有人问这个项目的问题，bot
        先从资料里找出相关的几节，只照资料回答；资料里没有的就直说不确定。问题带截图时，先用「表情包」页设置里选的看图模型读出图上的字。
      </p>
      <StaleNotice error={view.error} />
      <Models data={data} />
      {data.bases.map((base) => (
        <BaseCard key={base.id} base={base} reload={view.reload} />
      ))}
      <AddBase empty={data.bases.length === 0} reload={view.reload} />
      {data.bases.length > 0 && <Groups data={data} reload={view.reload} />}
      {data.bases.map((base) => (
        <Gaps key={base.id} base={base} />
      ))}
      {data.bases.length > 0 && <AskBox bases={data.bases} />}
    </div>
  )
}

function Models({ data }: { data: KnowledgeView }) {
  const items = [
    { label: '向量模型', name: data.models.embed, up: data.models.embed_up },
    { label: '重排模型', name: data.models.rerank, up: data.models.rerank_up },
  ]
  return (
    <section className="flex flex-wrap gap-x-6 gap-y-1 rounded-2xl border bg-card px-5 py-3 text-sm">
      {items.map((m) => (
        <span key={m.label} className="flex items-center gap-2">
          <span aria-hidden className={cn('size-2 rounded-full', m.up ? 'bg-ok' : 'bg-warn')} />
          {m.label} {m.name}：{m.up ? '在运行' : '没开（只用关键词找资料，找不准时也判断不了资料里有没有）'}
        </span>
      ))}
    </section>
  )
}

function BaseCard({ base, reload }: { base: KnowledgeBaseView; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const files = Object.entries(base.files)
    .map(([file, n]) => `${FILE_LABEL[file] ?? file} ${n}`)
    .join(' · ')
  const remove = () => {
    if (!window.confirm(`删掉资料库「${base.name}」？绑定它的群不再按资料回答项目问题（文件夹里的文件不会删）。`)) return
    void run(() => sendJson('DELETE', `/api/knowledge/${base.id}`))
  }
  return (
    <section className="space-y-2 rounded-2xl border bg-card p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span aria-hidden className="grid size-8 place-items-center rounded-lg bg-muted text-muted-foreground">
          <Library className="size-4" />
        </span>
        <h2 className="text-base font-semibold">{base.name}</h2>
        <span className="text-sm text-muted-foreground">{base.sections} 节</span>
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(() => sendJson('POST', `/api/knowledge/${base.id}/reload`))}>
            {busy ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
            重新载入
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={remove} aria-label={`删掉资料库「${base.name}」`}>
            <Trash2 />
          </Button>
        </div>
      </div>
      <p className="text-xs break-all text-muted-foreground">{base.folder}</p>
      {files && <p className="text-sm">{files}</p>}
      <p className="text-xs text-muted-foreground">
        {base.loaded_at ? `载入于 ${formatAgo(base.loaded_at)}` : '还没载入'}
        {base.vectors ? '，向量已算好' : '，向量还没算（向量模型没开）'}。文件改了会自动重新载入。
      </p>
      <ForwardTo base={base} reload={reload} />
      <SourceDir base={base} reload={reload} />
      {base.problem && (
        <p role="alert" className="text-sm text-destructive">
          {base.problem}
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

function AddBase({ empty, reload }: { empty: boolean; reload: () => void }) {
  const [open, setOpen] = useState(empty)
  const [name, setName] = useState('')
  const [folder, setFolder] = useState('')
  const { busy, error, run } = useAction(reload)
  const id = useId()
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (await run(() => sendJson('POST', '/api/knowledge', { name, folder }))) {
      setName('')
      setFolder('')
      setOpen(false)
    }
  }
  if (!open) {
    return (
      <div>
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
          <Plus />
          添加资料库
        </Button>
      </div>
    )
  }
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-3 rounded-2xl border bg-card p-5">
      <h2 className="text-sm font-semibold">添加资料库</h2>
      <div className="grid gap-3 sm:grid-cols-[12rem_1fr]">
        <div className="space-y-1">
          <Label htmlFor={`${id}-name`}>项目名</Label>
          <Input id={`${id}-name`} value={name} placeholder="我的项目" onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${id}-folder`}>资料文件夹（这台机器上的路径）</Label>
          <Input
            id={`${id}-folder`}
            value={folder}
            placeholder="~/knowledge/my-project/docs/help"
            onChange={(e) => setFolder(e.target.value)}
          />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        文件夹里要有 features.md、errors.md、faq.md、overview.md、versions.md 这类帮助文件（格式见本项目的
        docs/help）。只读这几个文件。
      </p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !name.trim() || !folder.trim()}>
          {busy && <LoaderCircle className="animate-spin" />}
          添加
        </Button>
        {!empty && (
          <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
            取消
          </Button>
        )}
      </div>
    </form>
  )
}

function Groups({ data, reload }: { data: KnowledgeView; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const id = useId()
  return (
    <section className="rounded-2xl border bg-card">
      <h2 className="border-b px-5 py-3 text-sm font-semibold">哪个群用哪份资料</h2>
      {data.groups.length === 0 ? (
        <p className="px-5 py-4 text-sm text-muted-foreground">bot 在群里收到消息后，这里就能选群。</p>
      ) : (
        <ul className="divide-y">
          {data.groups.map((g, i) => (
            <li key={g.group_key} className="flex flex-wrap items-center gap-3 px-5 py-2.5">
              <Label htmlFor={`${id}-${i}`} className="min-w-40 flex-1 font-normal">
                {groupLabel(g)}
              </Label>
              <NativeSelect
                id={`${id}-${i}`}
                value={g.base_id ?? ''}
                disabled={busy}
                onChange={(e) =>
                  void run(() =>
                    sendJson('PUT', '/api/knowledge/groups', {
                      group_key: g.group_key,
                      base_id: e.target.value ? Number(e.target.value) : null,
                    }),
                  )
                }
              >
                <option value="">不用资料</option>
                {data.bases.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </NativeSelect>
            </li>
          ))}
        </ul>
      )}
      <p className="border-t px-5 py-2.5 text-xs text-muted-foreground">
        只对开了「答题」的 bot 起作用（Bot 页每个群的「回复方式」）。一个群建议只让一个 bot 开答题，免得几个一起抢答。
      </p>
      {error && (
        <p role="alert" className="px-5 pb-3 text-xs text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

function AskBox({ bases }: { bases: KnowledgeBaseView[] }) {
  const [baseId, setBaseId] = useState(bases[0].id)
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<AskResult | null>(null)
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState('')
  const id = useId()
  const ask = async (event: FormEvent) => {
    event.preventDefault()
    setAsking(true)
    setError('')
    try {
      setResult(await sendJson<AskResult>('POST', `/api/knowledge/${baseId}/ask`, { question }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAsking(false)
    }
  }
  return (
    <section className="space-y-3 rounded-2xl border bg-card p-5">
      <div>
        <h2 className="text-sm font-semibold">试问</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          像群友那样问一句，看会找出哪几节、每一步怎么排的。不调用聊天模型；真正答题时，模型还会先补几个搜索词。
        </p>
      </div>
      <form onSubmit={(e) => void ask(e)} className="flex flex-wrap gap-2">
        {bases.length > 1 && (
          <NativeSelect aria-label="资料库" value={baseId} onChange={(e) => setBaseId(Number(e.target.value))}>
            {bases.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </NativeSelect>
        )}
        <Label htmlFor={`${id}-q`} className="sr-only">
          问题
        </Label>
        <Input
          id={`${id}-q`}
          className="min-w-60 flex-1"
          value={question}
          maxLength={500}
          placeholder="比如：怎么导出聊天记录"
          onChange={(e) => setQuestion(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={asking || !question.trim()}>
          {asking ? <LoaderCircle className="animate-spin" /> : <Search />}
          找资料
        </Button>
      </form>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {result && <AskResultView result={result} />}
    </section>
  )
}

function AskResultView({ result }: { result: AskResult }) {
  const verdict = !result.reranked
    ? `没有重排（重排模型没开），按找到的顺序给了 ${result.sections.length} 节`
    : result.found
      ? `找到 ${result.sections.length} 节（最相关 ${result.best.toFixed(2)}）`
      : `资料里没找到（最相关 ${result.best.toFixed(2)}，低于门槛），答题时会直说不确定`
  return (
    <div className="space-y-3">
      <p className={cn('text-sm font-medium', result.found ? 'text-ok' : 'text-warn')}>
        {verdict}
        <span className="ml-2 text-xs font-normal text-muted-foreground">{result.took_ms} ms</span>
      </p>
      {result.degraded && <p className="text-xs text-warn">{result.degraded}</p>}
      {result.sections.length > 0 && (
        <ol className="space-y-1.5">
          {result.sections.map((s) => (
            <SectionRow key={s.id} section={s} />
          ))}
        </ol>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {STEPS.filter((name) => result.steps[name]).map((name) => (
          <div key={name} className="rounded-lg bg-surface p-3">
            <h3 className="mb-1.5 text-xs font-semibold text-muted-foreground">{name}</h3>
            {result.steps[name].length === 0 ? (
              <p className="text-xs text-muted-foreground">没有</p>
            ) : (
              <ol className="space-y-0.5 text-xs">
                {result.steps[name].map((step) => (
                  <li key={step.id} className="flex gap-2">
                    <span className="min-w-0 flex-1 truncate" title={step.id}>
                      {step.title}
                    </span>
                    <span className="shrink-0 text-muted-foreground tabular-nums">
                      {step.score < 1 ? step.score.toFixed(3) : step.score.toFixed(1)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function SectionRow({ section }: { section: AskResult['sections'][number] }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        <span className="text-xs text-muted-foreground">{FILE_LABEL[section.file] ?? section.file}</span>
        <span className="min-w-0 flex-1 truncate font-medium">{section.title}</span>
        <span className="text-xs text-muted-foreground tabular-nums">{section.score.toFixed(3)}</span>
        <ChevronDown aria-hidden className={cn('size-4 transition-transform', !open && '-rotate-90')} />
      </button>
      {open && <pre className="border-t px-3 py-2 text-xs whitespace-pre-wrap">{section.text}</pre>}
    </li>
  )
}
