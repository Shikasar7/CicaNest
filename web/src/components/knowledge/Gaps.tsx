import { useId, useState, type FormEvent } from 'react'
import { Check, LoaderCircle, MessageSquarePlus, Trash2, X } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { sendJson, type GapsView, type KnowledgeBaseView, type KnowledgeGap } from '@/lib/api'
import { formatAgo } from '@/lib/format'
import { useLiveData } from '@/lib/live-data'
import { RECIPIENT_HINT } from '@/lib/platforms'
import { cn } from '@/lib/utils'

const REASON: Record<KnowledgeGap['reason'], string> = { not_found: '资料里没找到', uncertain: '答得不确定' }
const STATUS: Record<KnowledgeGap['status'], string> = { open: '待处理', answered: '已补回答', ignored: '已忽略' }

/** 转给谁: the QQ number unanswered questions are forwarded to by private message. */
export function ForwardTo({ base, reload }: { base: KnowledgeBaseView; reload: () => void }) {
  const [value, setValue] = useState(base.forward_to)
  const { busy, error, run } = useAction(reload)
  const id = useId()
  const save = (event: FormEvent) => {
    event.preventDefault()
    void run(() => sendJson('PATCH', `/api/knowledge/${base.id}`, { forward_to: value.trim() }))
  }
  return (
    <form onSubmit={save} className="flex flex-wrap items-center gap-2 text-sm">
      <Label htmlFor={`${id}-to`} className="font-normal">
        答不上来的问题私聊转给
      </Label>
      <Input
        id={`${id}-to`}
        className="w-48"
        value={value}
        placeholder="不转就留空"
        onChange={(e) => setValue(e.target.value)}
      />
      <Button type="submit" size="sm" variant="outline" disabled={busy || value.trim() === base.forward_to}>
        保存
      </Button>
      <span className="w-full text-xs text-muted-foreground">
        由回答的那个 bot 私聊发出（对方在别的平台时，由那边在线的 bot 发），几个问题合成一条，10 分钟最多一条、每天最多 10 条；bot 在「只看不说」时只记下不转。{RECIPIENT_HINT}
      </span>
      {error && (
        <p role="alert" className="w-full text-xs text-destructive">
          {error}
        </p>
      )}
    </form>
  )
}

/** 源码文件夹: the project's source checkout the bot reads when the docs do not answer (看源码). */
export function SourceDir({ base, reload }: { base: KnowledgeBaseView; reload: () => void }) {
  const [value, setValue] = useState(base.source_dir ?? '')
  const { busy, error, run } = useAction(reload)
  const id = useId()
  if (base.source_dir === undefined) return null // an older nest
  const save = (event: FormEvent) => {
    event.preventDefault()
    void run(() => sendJson('PATCH', `/api/knowledge/${base.id}`, { source_dir: value.trim() }))
  }
  const source = base.source
  const state = !source
    ? '没设置：资料答不上的问题只会转给你。'
    : !source.ready
      ? '找不到这个文件夹。'
      : `${source.files} 个源码文件${source.commit ? `，版本 ${source.commit}（${source.date}）` : ''}。每天 4:30 自动拉取最新代码。`
  return (
    <form onSubmit={save} className="flex flex-wrap items-center gap-2 text-sm">
      <Label htmlFor={`${id}-src`} className="font-normal">
        源码文件夹
      </Label>
      <Input
        id={`${id}-src`}
        className="min-w-60 flex-1"
        value={value}
        placeholder="项目源码的文件夹，不读源码就留空"
        onChange={(e) => setValue(e.target.value)}
      />
      <Button type="submit" size="sm" variant="outline" disabled={busy || value.trim() === base.source_dir}>
        保存
      </Button>
      <span className="w-full text-xs text-muted-foreground">
        资料答不上来时，回答的 bot 会先照常回一句，再用自己的模型去搜、读这份源码（只读，最多 10 步，每小时最多 6
        次）。讲的内容在代码里确认过，就在群里补一条「翻了下源码：…」（哪怕还要对方补信息）；没找到代码依据就不说。转给你的私聊里都会附上它的分析。{state}
      </span>
      {error && (
        <p role="alert" className="w-full text-xs text-destructive">
          {error}
        </p>
      )}
    </form>
  )
}

/** 没答上的问题: project questions the docs did not answer; an answer added here is searched like the docs. */
export function Gaps({ base }: { base: KnowledgeBaseView }) {
  const [all, setAll] = useState(false)
  const view = useLiveData<GapsView>(`/api/knowledge/${base.id}/gaps?status=${all ? 'all' : 'open'}`, [])
  const data = view.data
  return (
    <section className="rounded-2xl border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b px-5 py-3">
        <h2 className="text-sm font-semibold">{base.name} 没答上的问题</h2>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
          也显示已处理的
        </label>
      </div>
      {!data ? (
        <p className="px-5 py-4 text-sm text-muted-foreground">{view.error || '读取中…'}</p>
      ) : data.gaps.length === 0 ? (
        <p className="px-5 py-4 text-sm text-muted-foreground">
          {all ? '还没有记录。' : '没有待处理的问题。'}资料答不上来、或者 bot 自己说不确定的项目问题会记在这里。
        </p>
      ) : (
        <ul className="divide-y">
          {data.gaps.map((gap) => (
            <GapRow key={gap.id} gap={gap} reload={view.reload} />
          ))}
        </ul>
      )}
      {data && data.notes.length > 0 && <Notes notes={data.notes} reload={view.reload} />}
    </section>
  )
}

function GapRow({ gap, reload }: { gap: KnowledgeGap; reload: () => void }) {
  const [answering, setAnswering] = useState(false)
  const { busy, error, run } = useAction(reload)
  return (
    <li className="space-y-1.5 px-5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
        <span>{formatAgo(gap.created_at)}</span>
        <span>{gap.group_name || gap.group_key}</span>
        <span>{gap.asker || '有人'}问</span>
        <span className={cn(gap.reason === 'not_found' ? 'text-warn' : '')}>{REASON[gap.reason]}</span>
        {gap.status !== 'open' && <span>· {STATUS[gap.status]}</span>}
        {gap.forwarded_at && <span>· 已私聊转给你</span>}
      </div>
      <p className="text-sm whitespace-pre-wrap">{gap.question}</p>
      {gap.answer && <p className="text-xs text-muted-foreground">bot 回了：{gap.answer}</p>}
      {gap.analysis && (
        <div className="rounded-lg bg-surface px-3 py-2 text-xs">
          <p className="mb-0.5 font-medium">{gap.analysis_sure ? '看了源码，已在群里补答' : '看了源码，没找到代码依据，没在群里说'}</p>
          <p className="whitespace-pre-wrap">{gap.analysis}</p>
          {gap.analysis_files && <p className="mt-1 break-all text-muted-foreground">依据：{gap.analysis_files.split('\n').join('、')}</p>}
        </div>
      )}
      {gap.status === 'open' && !answering && (
        <div className="flex gap-1">
          <Button size="sm" variant="outline" onClick={() => setAnswering(true)}>
            <MessageSquarePlus />
            补回答
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(() => sendJson('POST', `/api/knowledge/gaps/${gap.id}/ignored`))}>
            <X />
            忽略
          </Button>
        </div>
      )}
      {gap.status === 'ignored' && (
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void run(() => sendJson('POST', `/api/knowledge/gaps/${gap.id}/open`))}>
          恢复待处理
        </Button>
      )}
      {answering && <AnswerForm gap={gap} onDone={() => setAnswering(false)} reload={reload} />}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </li>
  )
}

function AnswerForm({ gap, onDone, reload }: { gap: KnowledgeGap; onDone: () => void; reload: () => void }) {
  const [title, setTitle] = useState(gap.question.replace(/\s+/g, ' ').slice(0, 30))
  // Starts from what the source showed, when the bot read it: check it, then save.
  const [body, setBody] = useState(gap.analysis ?? '')
  const { busy, error, run } = useAction(reload)
  const id = useId()
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (await run(() => sendJson('POST', `/api/knowledge/gaps/${gap.id}/answer`, { title, body }))) onDone()
  }
  return (
    <form onSubmit={(e) => void submit(e)} className="space-y-2 rounded-lg bg-surface p-3">
      <div className="space-y-1">
        <Label htmlFor={`${id}-t`}>标题（像资料里的一节，写这个问题问的是什么）</Label>
        <Input id={`${id}-t`} value={title} maxLength={60} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-b`}>回答</Label>
        <Textarea
          id={`${id}-b`}
          rows={4}
          value={body}
          maxLength={1500}
          placeholder="比如：目前还不支持，下个版本会加。"
          onChange={(e) => setBody(e.target.value)}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {gap.analysis ? '回答先填了 bot 看源码得出的结论，核对、改好再保存。' : ''}
        保存后马上能被找到，下次有人这样问就照这里答。它只存在 nest 里，想放进正式资料，之后把它合并进资料的源文件。
      </p>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !title.trim() || !body.trim()}>
          {busy ? <LoaderCircle className="animate-spin" /> : <Check />}
          保存回答
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          取消
        </Button>
      </div>
    </form>
  )
}

function Notes({ notes, reload }: { notes: GapsView['notes']; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const remove = (note: GapsView['notes'][number]) => {
    if (!window.confirm(`删掉补充「${note.title}」？它回答的问题会回到待处理。`)) return
    void run(() => sendJson('DELETE', `/api/knowledge/notes/${note.id}`))
  }
  return (
    <div className="border-t px-5 py-3">
      <h3 className="mb-2 text-xs font-semibold text-muted-foreground">补充的回答（{notes.length}）</h3>
      <ul className="space-y-2">
        {notes.map((note) => (
          <li key={note.id} className="flex gap-2 text-sm">
            <div className="min-w-0 flex-1">
              <p className="font-medium">{note.title}</p>
              <p className="text-xs whitespace-pre-wrap text-muted-foreground">{note.body}</p>
            </div>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => remove(note)} aria-label={`删掉补充「${note.title}」`}>
              <Trash2 />
            </Button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
