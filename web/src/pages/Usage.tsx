import { useCallback, useEffect, useRef, useState } from 'react'

import { OUTCOMES } from '@/components/bots/shared'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { ModelUsageCard } from '@/components/usage/ModelUsageCard'
import { modelLabel, niceTicks } from '@/components/usage/helpers'
import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/native-select'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { getJson, type Usage as UsageData, type UsageBot, type UsageDay, type UsageDays, type UsageError, type UsagePurpose } from '@/lib/api'
import { formatCount, formatTime } from '@/lib/format'
import { useLive } from '@/lib/live'
import { cn } from '@/lib/utils'

const RANGES: { days: UsageDays; label: string }[] = [
  { days: 1, label: '今天' },
  { days: 7, label: '7 天' },
  { days: 14, label: '14 天' },
]

/** New decisions refetch at most this often; the last one in a burst still gets its (trailing) refetch. */
const THROTTLE_MS = 10_000

/** GET /api/usage for `days`: now, on every range change, after live decisions (throttled) and on reconnect. */
function useUsage(days: UsageDays) {
  const { state, subscribe } = useLive()
  const [data, setData] = useState<UsageData | null>(null)
  const [error, setError] = useState('')
  // The range the newest answer (data or error) was for; null while a retry is on its way. Set only when an
  // answer lands, so the effects below never set state themselves.
  const [settled, setSettled] = useState<UsageDays | null>(null)
  const latest = useRef(0) // only the newest request may land: a slow 14-day answer must not replace "today"
  const lastAt = useRef(0)
  const trailing = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    if (trailing.current) clearTimeout(trailing.current)
    trailing.current = null
    lastAt.current = Date.now()
    const request = ++latest.current
    getJson<UsageData>(`/api/usage?days=${days}`).then(
      (value) => {
        if (request !== latest.current) return
        setData(value)
        setError('')
        setSettled(days)
      },
      (e: Error) => {
        if (request !== latest.current) return
        setError(e.message)
        setSettled(days)
      },
    )
  }, [days])
  const retry = useCallback(() => {
    setSettled(null)
    load()
  }, [load])

  useEffect(load, [load])
  // Back from a dropped connection (e.g. nest restarted): fetch again instead of keeping a failed answer.
  const wasOpen = useRef(state === 'open')
  useEffect(() => {
    const open = state === 'open'
    if (open && !wasOpen.current) load()
    wasOpen.current = open
  }, [state, load])
  useEffect(
    () =>
      subscribe((item) => {
        if (item.kind !== 'decision' || trailing.current) return
        const wait = lastAt.current + THROTTLE_MS - Date.now()
        if (wait <= 0) load()
        else trailing.current = setTimeout(load, wait)
      }),
    [subscribe, load],
  )
  useEffect(
    () => () => {
      if (trailing.current) clearTimeout(trailing.current)
      trailing.current = null
      latest.current++ // drop whatever is still on its way
    },
    [],
  )
  // pending: this range has no answer yet (just switched to it, or retrying).
  return { data, error, pending: settled !== days, retry }
}

/** 用在哪: what the calls were for, every bot's together or one's. */
function PurposeTable({ rows, bots }: { rows: UsagePurpose[]; bots: UsageBot[] }) {
  const [botId, setBotId] = useState('')
  const sums = new Map<string, UsagePurpose>()
  for (const row of rows) {
    if (botId && row.bot_id !== botId) continue
    const sum = sums.get(row.purpose)
    sums.set(row.purpose, {
      ...row,
      calls: (sum?.calls ?? 0) + row.calls,
      prompt_tokens: (sum?.prompt_tokens ?? 0) + row.prompt_tokens,
      completion_tokens: (sum?.completion_tokens ?? 0) + row.completion_tokens,
    })
  }
  const tokens = (r: UsagePurpose) => r.prompt_tokens + r.completion_tokens
  const listed = [...sums.values()].sort((a, b) => tokens(b) - tokens(a) || b.calls - a.calls)
  const most = Math.max(1, ...listed.map(tokens))
  return (
    <div className="space-y-3">
      <NativeSelect aria-label="伙伴" value={botId} onChange={(e) => setBotId(e.target.value)}>
        <option value="">所有 bot 加起来</option>
        {bots.map((b) => (
          <option key={b.bot_id} value={b.bot_id}>
            {b.name}
          </option>
        ))}
      </NativeSelect>
      {listed.length === 0 ? (
        <p className="text-sm text-muted-foreground">这段时间还没有调用过模型。</p>
      ) : (
        <ul className="space-y-2 text-sm tabular-nums">
          {listed.map((row) => (
            <li key={row.purpose}>
              <div className="flex items-baseline justify-between gap-3">
                <span>{row.label}</span>
                <span className="text-xs text-muted-foreground">
                  {formatCount(row.calls)} 次 · {formatCount(row.prompt_tokens)} / {formatCount(row.completion_tokens)}
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary" style={{ width: `${(tokens(row) / most) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">右边是调用次数和用量（输入 / 输出）。更新前的调用分不出来，算在「其他」里。</p>
    </div>
  )
}

function BotTable({ bots, tasks }: { bots: UsageBot[]; tasks: UsageData['tasks'] }) {
  if (bots.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        还没有 bot。用 nest bot-add 创建 bot 后，这里会显示每个 bot 回了多少、调用了几次模型。
      </p>
    )
  }
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-sm tabular-nums">
          <thead className="text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="py-2 pr-3 text-left font-normal">Bot</th>
              {OUTCOMES.map((o) => (
                <th key={o.key} className="py-2 pr-3 text-right font-normal whitespace-nowrap">
                  {o.label}
                </th>
              ))}
              <th className="py-2 pr-3 text-right font-normal">调用</th>
              <th className="py-2 pr-3 text-right font-normal whitespace-nowrap">用量（输入 / 输出）</th>
              <th className="py-2 text-right font-normal whitespace-nowrap">其中后台任务</th>
            </tr>
          </thead>
          <tbody>
            {bots.map((bot) => (
              <tr key={bot.bot_id} className="border-b last:border-0">
                <td className="py-2 pr-3 font-medium whitespace-nowrap">{bot.name}</td>
                {OUTCOMES.map((o) => (
                  <td
                    key={o.key}
                    className={cn(
                      'py-2 pr-3 text-right',
                      o.key === 'failed' && bot.outcomes.failed > 0 && 'text-destructive',
                    )}
                  >
                    {formatCount(bot.outcomes[o.key] ?? 0)}
                  </td>
                ))}
                <td className="py-2 pr-3 text-right">{formatCount(bot.calls)}</td>
                <td className="py-2 pr-3 text-right whitespace-nowrap">
                  {formatCount(bot.prompt_tokens)} / {formatCount(bot.completion_tokens)}
                </td>
                <td className="py-2 text-right whitespace-nowrap text-muted-foreground">
                  {tasks?.[bot.bot_id]
                    ? `${formatCount(tasks[bot.bot_id].tasks)} 个 · ${formatCount(tasks[bot.bot_id].tokens)}`
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        出错 也包括发送失败、模型暂停等，原因在「消息记录」页的判断卡片里。调用按每次请求算，含自动重试。
        「其中后台任务」是这段时间开始的任务一共用的 token，已经算在左边的用量里；每个任务的明细在「任务」页。
      </p>
    </div>
  )
}

function RecentErrors({ errors, deleted }: { errors: UsageError[]; deleted: Set<string> }) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">最新的在最上面，最多 20 条；自动重试后成功的也会列出。</p>
      {errors.length === 0 ? (
        <p className="text-sm text-muted-foreground">这段时间没有模型出错。出错时这里会列出时间、模型、bot 和原因。</p>
      ) : (
        <ul className="divide-y text-sm">
          {errors.map((e, i) => (
            <li key={`${e.at}-${i}`} className="space-y-0.5 py-2">
              <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                <span className="tabular-nums">{formatTime(e.at)}</span>
                <span className="text-foreground">
                  {modelLabel({ provider_id: e.provider_id, deleted: deleted.has(e.provider_id) })}
                </span>
                <span>{e.bot_name ?? (e.bot_id ? e.bot_id : '测试')}</span>
              </div>
              <p className="break-words text-destructive">{e.error}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const sinceText = (data: UsageData) => {
  if (data.days === 1) return '今天 0 点到现在'
  const since = new Date(data.since * 1000)
  return `${since.getMonth() + 1} 月 ${since.getDate()} 日 0 点到现在（${data.days} 天）`
}

export function Usage() {
  const [days, setDays] = useState<UsageDays>(7)
  const { data, error, pending, retry } = useUsage(days)

  if (!data) return <FirstLoad error={error} what="用量" />
  const stale = data.days !== days // still showing another range: loading it, or it failed (StaleNotice says so)
  const switching = pending && stale
  const multiDay = data.days > 1
  const byModel = new Map<string, UsageDay[]>()
  for (const row of data.daily) byModel.set(row.provider_id, [...(byModel.get(row.provider_id) ?? []), row])
  for (const rows of byModel.values()) rows.sort((a, b) => a.day.localeCompare(b.day))
  const ticks = niceTicks(Math.max(0, ...data.daily.map((d) => d.calls)))
  const anyCalls = data.providers.some((p) => p.calls > 0)
  const deleted = new Set(data.providers.filter((p) => p.deleted).map((p) => p.provider_id))

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div role="group" aria-label="时间范围" className="flex gap-1">
          {RANGES.map((r) => (
            <Button
              key={r.days}
              size="sm"
              variant={days === r.days ? 'default' : 'outline'}
              aria-pressed={days === r.days}
              onClick={() => (r.days === days ? retry() : setDays(r.days))}
            >
              {r.label}
            </Button>
          ))}
        </div>
        <span role="status" className="text-xs text-muted-foreground">
          {switching ? '正在读取…' : sinceText(data)}
        </span>
      </div>
      <StaleNotice error={error} />
      <div className={cn('flex flex-col gap-4 transition-opacity', stale && 'opacity-60')}>
        {data.providers.length === 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>还没有模型</CardTitle>
              <CardDescription>
                在「模型」页添加模型、在「伙伴」页给伙伴选用后，这里会显示每个模型的调用次数、失败率、耗时和用量。
              </CardDescription>
            </CardHeader>
          </Card>
        ) : (
          <>
            {!anyCalls && (
              <p className="text-sm text-muted-foreground">
                这段时间还没有调用过模型。bot 被点名或主动插话时会调用模型，「模型」页的「测试一下」也算在这里。
              </p>
            )}
            {!multiDay && anyCalls && <p className="text-sm text-muted-foreground">选 7 天或 14 天看每天的变化。</p>}
            {multiDay && byModel.size > 0 && (
              <p className="text-xs text-muted-foreground">
                每天的图里纵轴都一样，最高 {formatCount(ticks[ticks.length - 1])} 次，可以直接比较高低。
              </p>
            )}
            <div className="grid gap-4 lg:grid-cols-2">
              {data.providers.map((provider) => (
                <ModelUsageCard
                  key={provider.provider_id}
                  provider={provider}
                  days={byModel.get(provider.provider_id) ?? []}
                  ticks={ticks}
                  multiDay={multiDay}
                />
              ))}
            </div>
          </>
        )}
        {data.purposes && (
          <Card>
            <CardHeader>
              <CardTitle>用在哪</CardTitle>
              <CardDescription>聊天、碎碎念、主动找你、她们的日子、后台任务各用了多少</CardDescription>
            </CardHeader>
            <CardContent>
              <PurposeTable rows={data.purposes} bots={data.bots} />
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader>
            <CardTitle>每个 bot</CardTitle>
            <CardDescription>判断结果和它们调用模型的次数（{data.days === 1 ? '今天' : `最近 ${data.days} 天`}）</CardDescription>
          </CardHeader>
          <CardContent>
            <BotTable bots={data.bots} tasks={data.tasks} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>最近的模型错误</CardTitle>
          </CardHeader>
          <CardContent>
            <RecentErrors errors={data.recent_errors} deleted={deleted} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
