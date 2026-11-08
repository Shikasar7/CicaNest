import { useId, useState } from 'react'
import { Check, ClipboardCopy, Download, EyeOff, RotateCcw, Send, Sparkles, Trash2 } from 'lucide-react'

import { useAction } from '@/components/bots/shared'
import { FirstLoad, ResultText, StaleNotice, type Result } from '@/components/Feedback'
import { Segmented } from '@/components/Segmented'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect } from '@/components/ui/native-select'
import { sendJson, type Issue, type IssueKind, type IssueStatus, type IssuesRun, type IssuesView } from '@/lib/api'
import { useLive } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { RECIPIENT_HINT } from '@/lib/platforms'
import { cn } from '@/lib/utils'

const KIND: Record<IssueKind, string> = {
  unsolved: '没解决',
  wrong: '答错了',
  bug: '故障',
  request: '想要的功能',
  feedback: '意见',
}
const KIND_TONE: Record<IssueKind, string> = {
  unsolved: 'bg-warn/15 text-foreground',
  wrong: 'bg-destructive/10 text-destructive',
  bug: 'bg-destructive/10 text-destructive',
  request: 'bg-primary/10 text-primary',
  feedback: 'bg-muted text-muted-foreground',
}
const STATUS_LABEL: Record<IssueStatus, string> = { open: '待处理', done: '已解决', ignored: '已忽略' }
const WEEKDAYS = '日一二三四五六'

const pad = (n: number) => String(n).padStart(2, '0')
const dayOf = (seconds: number) => {
  const d = new Date(seconds * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}（周${WEEKDAYS[d.getDay()]}）`
}
const clockOf = (seconds: number) => {
  const d = new Date(seconds * 1000)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}
const groupLabel = (issue: Pick<Issue, 'group_name' | 'group_id'>) => issue.group_name || `群 ${issue.group_id}`

/** The issues by day, newest day first and in order of time within a day: the 问题集 as a timeline. */
function byDay(issues: Issue[]): [string, Issue[]][] {
  const days = new Map<string, Issue[]>()
  for (const issue of issues) {
    const day = dayOf(issue.said_at)
    days.set(day, [...(days.get(day) ?? []), issue])
  }
  return [...days].map(([day, list]) => [day, [...list].sort((a, b) => a.said_at - b.said_at)])
}

function markdown(issues: Issue[], title: string): string {
  const lines = [`# ${title}`, '']
  for (const [day, list] of byDay(issues)) {
    lines.push(`## ${day}`, '')
    for (const issue of list) {
      lines.push(
        `- **${clockOf(issue.said_at)}** ${groupLabel(issue)}，${issue.speaker || '有人'}【${KIND[issue.kind]}】${issue.summary}` +
          (issue.status === 'open' ? '' : `（${STATUS_LABEL[issue.status]}）`),
      )
      if (issue.quote) lines.push(`  - 原话：${issue.quote}`)
      if (issue.ai_reply) lines.push(`  - bot 回：${issue.ai_reply}`)
    }
    lines.push('')
  }
  return lines.join('\n')
}

/** 每日汇总: who gets the digest and when, plus 立即整理 / 立即发送 (its fields start from what is saved). */
function DigestSettings({ view, reload }: { view: IssuesView; reload: () => void }) {
  const [to, setTo] = useState(view.digest.to)
  const [times, setTimes] = useState(view.digest.times.join('、'))
  const [result, setResult] = useState<Result | null>(null)
  const [running, setRunning] = useState<'review' | 'digest' | null>(null)
  const { busy, error, run } = useAction(reload)
  const id = useId()
  const dirty = to.trim() !== view.digest.to || times !== view.digest.times.join('、')

  const save = async () => {
    setResult(null)
    const list = times.split(/[、,，\s]+/).filter(Boolean)
    if (await run(() => sendJson('PUT', '/api/issues/digest', { to: to.trim(), times: list }))) {
      setResult({ ok: true, text: '已保存' })
    }
  }
  const action = async (which: 'review' | 'digest') => {
    setRunning(which)
    setResult(null)
    try {
      const outcome = await sendJson<IssuesRun>('POST', `/api/issues/${which}`)
      setResult({ ok: outcome.ok, text: outcome.detail })
      reload()
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message })
    } finally {
      setRunning(null)
    }
  }

  return (
    <section aria-labelledby={`${id}-title`} className="rounded-2xl border bg-card p-5">
      <h2 id={`${id}-title`} className="text-base font-semibold tracking-tight">
        每日汇总
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        到了设定的时间，把上次汇总以来新记下的问题私聊发给这个人；没有新问题就不发。留空表示不发。{RECIPIENT_HINT}
      </p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="grid gap-1.5">
          <Label htmlFor={`${id}-to`}>发给</Label>
          <Input
            id={`${id}-to`}
            value={to}
            maxLength={80}
            placeholder="不发"
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${id}-times`}>发送时间</Label>
          <Input
            id={`${id}-times`}
            value={times}
            placeholder="21:00"
            aria-describedby={`${id}-times-hint`}
            onChange={(e) => setTimes(e.target.value)}
          />
          <p id={`${id}-times-hint`} className="text-xs text-muted-foreground">
            24 小时制，可以填几个，用顿号隔开，比如 12:00、21:00（最多 6 个）
          </p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy || !dirty} onClick={() => void save()}>
          <Check />
          保存
        </Button>
        <Button size="sm" variant="outline" disabled={running !== null} onClick={() => void action('review')}>
          <Sparkles />
          {running === 'review' ? '整理中…' : '立即整理'}
        </Button>
        <Button size="sm" variant="outline" disabled={running !== null} onClick={() => void action('digest')}>
          <Send />
          {running === 'digest' ? '发送中…' : '立即发送汇总'}
        </Button>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {error}
        </p>
      )}
      <ResultText result={result} className="mt-2" />
    </section>
  )
}

function IssueItem({ issue, reload }: { issue: Issue; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const setStatus = (status: IssueStatus) =>
    void run(() => sendJson('PATCH', `/api/issues/${issue.id}`, { status }))
  const remove = () => {
    if (window.confirm(`删除这条记录？「${issue.summary}」`)) void run(() => sendJson('DELETE', `/api/issues/${issue.id}`))
  }
  return (
    <li className={cn('flex gap-4 py-3', issue.status !== 'open' && 'opacity-60')}>
      <time
        dateTime={new Date(issue.said_at * 1000).toISOString()}
        className="w-12 shrink-0 pt-0.5 text-sm tabular-nums text-muted-foreground"
      >
        {clockOf(issue.said_at)}
      </time>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className={cn('rounded-md px-1.5 py-0.5 text-[11px] leading-4', KIND_TONE[issue.kind])}>
            {KIND[issue.kind]}
          </span>
          <span className="font-medium">{issue.speaker || '有人'}</span>
          <span className="text-muted-foreground">在 {groupLabel(issue)}</span>
          {issue.status !== 'open' && (
            <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] leading-4 text-muted-foreground">
              {STATUS_LABEL[issue.status]}
            </span>
          )}
        </p>
        <p className="text-sm">{issue.summary}</p>
        {issue.quote && <p className="text-xs break-words text-muted-foreground">原话：{issue.quote}</p>}
        {issue.ai_reply && <p className="text-xs text-muted-foreground">bot 回：{issue.ai_reply}</p>}
        <div className="flex flex-wrap items-center gap-1 pt-1">
          {issue.status === 'open' ? (
            <>
              <Button size="xs" variant="outline" disabled={busy} onClick={() => setStatus('done')}>
                <Check />
                已解决
              </Button>
              <Button size="xs" variant="ghost" disabled={busy} onClick={() => setStatus('ignored')}>
                <EyeOff />
                忽略
              </Button>
            </>
          ) : (
            <Button size="xs" variant="ghost" disabled={busy} onClick={() => setStatus('open')}>
              <RotateCcw />
              重新打开
            </Button>
          )}
          <Button size="xs" variant="ghost" disabled={busy} onClick={remove} aria-label="删除这条记录">
            <Trash2 />
          </Button>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {issue.bot_name || '记录官'}记下{issue.reported_at ? '，已汇总' : ''}
          </span>
        </div>
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
    </li>
  )
}

/** 问题记录: what the groups' 记录官 wrote down (bot 没解决的问题、故障、意见), as a timeline by day. */
export function Issues() {
  const { issuesVersion, botsVersion } = useLive()
  const [status, setStatus] = useState<IssueStatus | 'all'>('open')
  const [group, setGroup] = useState('')
  const query = new URLSearchParams({ ...(status !== 'all' && { status }), ...(group && { group }) }).toString()
  const view = useLiveData<IssuesView>(`/api/issues${query ? `?${query}` : ''}`, [issuesVersion, botsVersion])
  const [copied, setCopied] = useState<Result | null>(null)
  const groupId = useId()
  const data = view.data

  if (view.missing && !data) {
    return (
      <p className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm">
        正在运行的 nest 版本还没有问题记录功能。重启 nest 后再打开这一页。
      </p>
    )
  }
  if (!data) return <FirstLoad error={view.error} what="问题记录" />

  const title = `nest 问题记录${status === 'all' ? '' : `（${STATUS_LABEL[status]}）`}`
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(markdown(data.issues, title))
      setCopied({ ok: true, text: `已复制 ${data.issues.length} 条，Markdown 格式` })
    } catch {
      setCopied({ ok: false, text: '浏览器不让复制，请改用「下载」' })
    }
  }
  const download = () => {
    const blob = new Blob([markdown(data.issues, title)], { type: 'text/markdown;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `nest-问题记录-${new Date().toISOString().slice(0, 10)}.md`
    link.click()
    URL.revokeObjectURL(link.href)
  }
  const statusOptions = [
    { value: 'open' as const, label: `待处理 ${data.counts.open}` },
    { value: 'done' as const, label: `已解决 ${data.counts.done}` },
    { value: 'ignored' as const, label: `已忽略 ${data.counts.ignored}` },
    { value: 'all' as const, label: '全部' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        每个群可以指定一个 bot 当问题记录官（「Bot」页，群的设置 → 记录 → 问题记录官）。它每小时看一遍群里的新消息，把 bot
        没解决的问题、答错的、故障、想要的功能和意见记在这里，按设定的时间私聊汇总给你。
      </p>
      <StaleNotice error={view.error} />
      <p className="text-sm">
        {data.recorders.length > 0 ? (
          <>
            记录官：
            {data.recorders.map((r) => `${r.bot_name}（${r.group_name || `群 ${r.group_id}`}）`).join('、')}
          </>
        ) : (
          <span className="text-warn">还没有 bot 当记录官，不会记下新问题。</span>
        )}
      </p>
      {/* Keyed by what is saved: a change from elsewhere starts the fields over from it. */}
      <DigestSettings key={`${data.digest.to}|${data.digest.times.join()}`} view={data} reload={view.reload} />
      <section aria-label="问题集" className="rounded-2xl border bg-card p-5">
        <div className="flex flex-wrap items-center gap-3">
          <Segmented options={statusOptions} value={status} onChange={setStatus} label="状态" />
          {data.groups.length > 1 && (
            <div className="flex items-center gap-2">
              <Label htmlFor={groupId} className="text-sm text-muted-foreground">
                群
              </Label>
              <NativeSelect id={groupId} value={group} onChange={(e) => setGroup(e.target.value)}>
                <option value="">全部</option>
                {data.groups.map((g) => (
                  <option key={g.group_id} value={g.group_id}>
                    {g.name || `群 ${g.group_id}`}
                  </option>
                ))}
              </NativeSelect>
            </div>
          )}
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="outline" disabled={!data.issues.length} onClick={() => void copy()}>
              <ClipboardCopy />
              复制
            </Button>
            <Button size="sm" variant="outline" disabled={!data.issues.length} onClick={download}>
              <Download />
              下载 .md
            </Button>
          </div>
        </div>
        <ResultText result={copied} className="mt-2" />
        {data.issues.length === 0 ? (
          <p className="mt-6 mb-2 text-center text-sm text-muted-foreground">
            {status === 'open' ? '没有待处理的问题。' : '这里还没有记录。'}
          </p>
        ) : (
          <div className="mt-4 space-y-6">
            {byDay(data.issues).map(([day, list]) => (
              <section key={day} aria-label={day}>
                <h3 className="border-b pb-1.5 text-sm font-semibold">{day}</h3>
                <ol className="divide-y">
                  {list.map((issue) => (
                    <IssueItem key={issue.id} issue={issue} reload={view.reload} />
                  ))}
                </ol>
              </section>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
