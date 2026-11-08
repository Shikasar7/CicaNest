import type { ReactNode } from 'react'

import { DailyChart } from '@/components/usage/DailyChart'
import { modelLabel } from '@/components/usage/helpers'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { UsageDay, UsageProvider } from '@/lib/api'
import { formatCount, formatLatency, formatRate, shortDay } from '@/lib/format'

function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg bg-muted/50 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-lg font-semibold break-words">{value}</div>
      {hint && <div className="text-[11px] leading-snug text-muted-foreground">{hint}</div>}
    </div>
  )
}

function tokens(provider: UsageProvider): ReactNode {
  if (provider.ok === 0) return '—'
  if (provider.token_calls === 0) return <span className="text-sm font-normal">这个模型不返回用量</span>
  return `${formatCount(provider.prompt_tokens)} / ${formatCount(provider.completion_tokens)}`
}

/** The numbers behind the chart, for reading them without hovering. */
function DayTable({ days }: { days: UsageDay[] }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">看每天的数字</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full tabular-nums">
          <thead className="text-muted-foreground">
            <tr className="border-b">
              <th className="py-1 pr-3 text-left font-normal">日期</th>
              <th className="py-1 pr-3 text-right font-normal">调用</th>
              <th className="py-1 pr-3 text-right font-normal">失败</th>
              <th className="py-1 text-right font-normal">用量（输入 / 输出）</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.day} className="border-b last:border-0">
                <td className="py-1 pr-3">{shortDay(d.day)}</td>
                <td className="py-1 pr-3 text-right">{d.calls}</td>
                <td className="py-1 pr-3 text-right">{d.errors}</td>
                <td className="py-1 text-right">
                  {formatCount(d.prompt_tokens)} / {formatCount(d.completion_tokens)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

/** One model over the chosen range: the five tiles and, for 7 or 14 days, its calls per day. */
export function ModelUsageCard({
  provider,
  days,
  ticks,
  multiDay,
}: {
  provider: UsageProvider
  /** This model's rows of `daily`, oldest first; empty when it had no calls in range. */
  days: UsageDay[]
  /** Shared y-axis ticks across every model's chart. */
  ticks: number[]
  multiDay: boolean
}) {
  const label = modelLabel(provider)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="break-all">{label}</CardTitle>
        <CardDescription>
          {provider.calls === 0 ? '这段时间没有调用' : `成功 ${formatCount(provider.ok)} 次，失败 ${formatCount(provider.errors)} 次`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Tile label="调用次数" value={formatCount(provider.calls)} />
          <Tile label="失败率" value={formatRate(provider.error_rate)} hint="按每次请求算，含自动重试" />
          <Tile label="平均耗时" value={formatLatency(provider.avg_latency_ms)} />
          <Tile label="慢的时候耗时" value={formatLatency(provider.p95_latency_ms)} hint="95% 的调用比这快" />
          <Tile label="用量（输入 / 输出）" value={tokens(provider)} hint="单位是 token，模型按它计费" />
        </div>
        {multiDay && days.length > 0 && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">每天的调用次数</span>
              <span className="flex items-center gap-1">
                <span aria-hidden className="inline-block size-2.5 rounded-[3px] bg-primary" />
                成功
              </span>
              <span className="flex items-center gap-1">
                <span aria-hidden className="inline-block size-2.5 rounded-[3px] bg-destructive" />
                失败
              </span>
            </div>
            <DailyChart label={label} days={days} ticks={ticks} />
            <DayTable days={days} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
