import { StatusDot } from '@/components/StatusDot'
import { deliveryText, runSummary, runTone } from '@/components/watches/shared'
import type { WatchKind, WatchRun } from '@/lib/api'
import { formatLatency, formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * What the bot pushed, or would have, in the bot's colour (var(--hue) set by the card): solid when it really
 * went out, dashed when it did not (试运行, paused, failed) — the overview's rule. Key it by run id so a new
 * message pops in.
 */
export function MessageBubble({
  run,
  target,
  botName,
  size = 'md',
  clamp = false,
  animate = true,
}: {
  run: WatchRun
  target: string
  botName: string
  size?: 'md' | 'sm'
  /** Cut long messages short (the card); the history shows them in full. */
  clamp?: boolean
  /** Pop in on mount (off in the history, where new rows already open their own space). */
  animate?: boolean
}) {
  if (!run.message) return null
  const sent = run.delivered === 'sent'
  const caption = deliveryText(run, target, botName)
  return (
    <figure
      className={cn(
        'rounded-2xl rounded-tl-md',
        animate && 'animate-in fade-in-0 zoom-in-95 duration-500',
        size === 'md' ? 'px-4 py-3' : 'px-3.5 py-2.5',
        sent
          ? 'bg-[color-mix(in_oklch,var(--hue)_10%,var(--card))]'
          : 'border border-dashed border-[color-mix(in_oklch,var(--hue)_45%,transparent)]',
      )}
    >
      <blockquote
        className={cn(
          'whitespace-pre-wrap [overflow-wrap:anywhere]',
          size === 'md' ? 'text-[15px] leading-relaxed' : 'text-sm leading-relaxed',
          clamp && 'line-clamp-8',
        )}
      >
        {run.message}
      </blockquote>
      <figcaption className={cn('mt-1.5 text-xs', caption.bad ? 'text-destructive' : 'text-muted-foreground')}>
        {caption.text}
      </figcaption>
    </figure>
  )
}

/** The detail line only when it says more than the summary. */
function extraDetail(run: WatchRun, summary: string): string {
  const detail = run.detail.trim()
  if (!detail || summary.includes(detail)) return ''
  return detail
}

/** A watch's checks, newest first. Runs that arrived while the list was open open their own space. */
export function RunList({
  runs,
  fresh,
  kind,
  target,
  botName,
}: {
  runs: WatchRun[]
  fresh: Set<number>
  kind: WatchKind
  target: string
  botName: string
}) {
  return (
    <ol className="divide-y">
      {runs.map((run) => {
        const summary = runSummary(run, kind)
        const detail = extraDetail(run, summary)
        return (
          <li key={run.id} className={cn(fresh.has(run.id) && 'feed-in')}>
            <div className="flex gap-4 py-3">
              <time
                dateTime={new Date(run.started_at * 1000).toISOString()}
                className="w-20 shrink-0 pt-px text-xs text-muted-foreground tabular-nums"
              >
                {formatTime(run.started_at)}
              </time>
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex items-baseline justify-between gap-3">
                  <p className={cn('min-w-0 text-sm break-words', run.outcome === 'error' && 'text-destructive')}>
                    <StatusDot tone={runTone(run)} className="mr-2 -translate-y-px" />
                    {summary}
                  </p>
                  <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                    {formatLatency(run.duration_ms)}
                  </span>
                </div>
                {detail && (
                  <p className="pl-4 text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground [overflow-wrap:anywhere]">
                    {detail}
                  </p>
                )}
                {run.message && (
                  <div className="pl-4">
                    <MessageBubble run={run} target={target} botName={botName} size="sm" animate={false} />
                  </div>
                )}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
