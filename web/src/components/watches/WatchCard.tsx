import { useId, useState, type CSSProperties, type ReactNode } from 'react'
import { ChevronDown, LoaderCircle } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { useAction, useJustSaved } from '@/components/bots/shared'
import { StatusDot } from '@/components/StatusDot'
import { MessageBubble, RunList } from '@/components/watches/Runs'
import { WatchForm } from '@/components/watches/WatchForm'
import { intervalText, kindOf, lastCheckText, nextText, runSummary, runTone, watchForm } from '@/components/watches/shared'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { ApiError, sendJson, type Bot, type Ok, type WatchRun, type WatchView } from '@/lib/api'
import { useDraft, useEditFocus } from '@/lib/drafts'
import { formatLatency } from '@/lib/format'
import { botLook } from '@/lib/persona'
import { cn } from '@/lib/utils'
import { RUNS_LIMIT, useWatchRuns } from '@/lib/watches'

const MODE_CHIP = {
  push: { label: '推送', className: 'bg-ok/15 text-ok' },
  preview: { label: '试运行', className: 'ring-1 ring-border text-muted-foreground' },
} as const

const shortUrl = (url: string) => url.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '')

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  )
}

type CheckResult = { run: WatchRun } | { error: string }

/** One 监控: what it watches, where it pushes, how its last check went and what it last said; 立即检查 · 编辑 · 记录 · 删除. */
export function WatchCard({
  watch,
  bot,
  bots,
  online,
  now,
  justAdded,
  onSaved,
  onDeleted,
  reload,
}: {
  watch: WatchView
  /** The target bot from /api/bots (current name and mode), when loaded. */
  bot: Bot | undefined
  bots: Bot[] | null
  online: boolean
  /** Unix seconds, ticking twice a minute. */
  now: number
  /** Added a moment ago: offer 立即检查. */
  justAdded: boolean
  onSaved: (view: WatchView) => void
  onDeleted: (id: number) => void
  reload: () => void
}) {
  const botName = bot?.name ?? watch.bot_name
  const kind = kindOf(watch.kind)
  const runs = useWatchRuns(watch.id, watch.last_run?.id ?? null)
  const draft = useDraft(watchForm(watch))
  const { editButton } = useEditFocus(draft.editing)
  const toggle = useAction(reload)
  const enabled = useJustSaved(watch.enabled)
  const [checkingHere, setChecking] = useState(false)
  // Also true while a scheduled check of this watch runs (the backend says so), so the button cannot collide.
  const checking = checkingHere || watch.running
  const [result, setResult] = useState<CheckResult | null>(null)
  const [open, setOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const historyId = useId()
  const path = `/api/watches/${watch.id}`

  const latest = watch.last_run
  // The bubble: the newest run with a message (the list's last run may be a quiet one).
  const said = watch.last_message ?? runs.runs?.find((r) => r.message) ?? (latest?.message ? latest : null)
  const on = enabled.value
  const pushPaused = watch.mode === 'push' && bot?.mode === 'paused'

  const setEnabled = async (next: boolean) => {
    if (
      next &&
      watch.mode === 'push' &&
      !window.confirm(`打开后会按时检查，有新内容就真的发到「${watch.target_name}」。继续？`)
    ) {
      return
    }
    enabled.mark(next)
    const ok = await toggle.run(async () => onSaved(await sendJson<WatchView>('PATCH', path, { enabled: next })))
    if (!ok) enabled.drop()
  }

  const check = async () => {
    setChecking(true)
    setResult(null)
    try {
      const run = await sendJson<WatchRun>('POST', `${path}/run`)
      runs.add(run)
      setResult({ run })
    } catch (e) {
      // 409: a scheduled check of this watch is running right now; its message says so.
      const busy = e instanceof ApiError && e.status === 409
      setResult({ error: busy ? (e as Error).message : `检查失败：${(e as Error).message}` })
    } finally {
      setChecking(false)
      reload()
    }
  }

  const remove = async () => {
    if (!window.confirm(`删除监控「${watch.name}」？它的检查记录也会一起删掉。`)) return
    setDeleting(true)
    setDeleteError('')
    try {
      await sendJson<Ok>('DELETE', path)
      onDeleted(watch.id)
    } catch (e) {
      setDeleteError(`删除失败：${(e as Error).message}`)
      setDeleting(false)
    }
  }

  return (
    <article
      style={{ '--hue': botLook(botName, watch.bot_id).hue } as CSSProperties}
      className={cn('@container rounded-2xl border bg-card p-5', justAdded && 'feed-in')}
    >
      <div className="min-h-0">
        <header className="flex items-start gap-3.5">
          <span
            aria-hidden
            className={cn(
              'grid size-10 shrink-0 place-items-center rounded-xl transition-colors duration-300',
              on ? 'bg-[color-mix(in_oklch,var(--hue)_12%,var(--card))] text-[var(--hue)]' : 'bg-muted text-muted-foreground',
            )}
          >
            <kind.Icon className="size-[18px]" />
          </span>
          <div className="min-w-0 flex-1 space-y-0.5">
            <h2 className="truncate text-base font-semibold tracking-tight">{watch.name}</h2>
            <p className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
              <span className="shrink-0">{kind.label}</span>
              <a
                href={watch.url}
                target="_blank"
                rel="noreferrer"
                title={watch.url}
                className="truncate underline-offset-4 hover:text-foreground hover:underline"
              >
                {shortUrl(watch.url)}
              </a>
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3 pt-0.5">
            <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap', MODE_CHIP[watch.mode].className)}>
              {MODE_CHIP[watch.mode].label}
            </span>
            <Switch
              checked={on}
              disabled={toggle.busy}
              onCheckedChange={(next) => void setEnabled(next)}
              aria-label={`启用监控「${watch.name}」`}
            />
          </div>
        </header>
        {toggle.error && (
          <p role="alert" className="mt-2 text-xs text-destructive">
            {toggle.error}
          </p>
        )}

        {draft.editing ? (
          <div className="mt-5 border-t pt-5">
            <WatchForm watch={watch} draft={draft} bots={bots} onSaved={onSaved} />
          </div>
        ) : (
          <>
            <div className="mt-5 grid gap-5 @2xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
              <dl className="grid content-start grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-2.5 text-sm">
                <Row label="推送给">
                  <span className="flex min-w-0 items-center gap-2">
                    <BotGlyph name={botName} id={watch.bot_id} size="sm" online={online} />
                    <span className="truncate">
                      {botName}
                      <span className="mx-1.5 text-muted-foreground">→</span>
                      {watch.target_name}
                    </span>
                  </span>
                  {pushPaused && (
                    <span className="mt-1 block text-xs text-muted-foreground">{botName} 已暂停，现在不会推送</span>
                  )}
                </Row>
                <Row label="检查">
                  {intervalText(watch.interval_minutes)}检查一次，
                  <span className={cn(!on && 'text-muted-foreground')}>{nextText({ ...watch, enabled: on }, now)}</span>
                </Row>
                <Row label="上次">
                  {checking ? (
                    <span role="status" className="text-muted-foreground">
                      <StatusDot tone="wait" className="mr-2 -translate-y-px" />
                      正在检查…
                    </span>
                  ) : latest ? (
                    <span key={latest.id} className={cn('block break-words', latest.outcome === 'error' && 'text-destructive')}>
                      <StatusDot tone={runTone(latest)} className="mr-2 -translate-y-px" />
                      {lastCheckText(latest, watch.kind)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">
                      <StatusDot tone="off" className="mr-2 -translate-y-px" />
                      还没检查过
                    </span>
                  )}
                </Row>
              </dl>
              {said ? (
                <MessageBubble key={said.id} run={said} target={watch.target_name} botName={botName} clamp />
              ) : (
                <p className="self-start rounded-2xl rounded-tl-md border border-dashed px-4 py-3 text-sm text-muted-foreground">
                  {latest
                    ? '还没有要推送的内容。有新内容时，消息会出现在这里。'
                    : '还没检查过。第一次检查只记下现在的内容，之后有新内容才推送。'}
                </p>
              )}
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-2 border-t pt-4">
              <Button size="sm" variant={justAdded && !result ? 'default' : 'outline'} disabled={checking} onClick={() => void check()}>
                {checking && <LoaderCircle className="animate-spin" />}
                {checking ? '检查中…' : '立即检查'}
              </Button>
              <Button ref={editButton} size="sm" variant="outline" onClick={draft.start}>
                编辑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-expanded={open}
                aria-controls={historyId}
                onClick={() => setOpen((v) => !v)}
              >
                记录
                <ChevronDown className={cn('transition-transform duration-200', open && 'rotate-180')} />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
                disabled={deleting}
                onClick={() => void remove()}
              >
                {deleting ? '删除中…' : '删除'}
              </Button>
            </div>
            {justAdded && !result && !checking && (
              <p role="status" className="mt-2 text-xs text-muted-foreground">
                已添加。点「立即检查」看看能不能读到内容，第一次只记下现在的内容。
              </p>
            )}
            {result && (
              <div key={'run' in result ? result.run.id : 'error'} className="feed-in">
                <div className="min-h-0">
                  {'run' in result ? (
                    <p role="status" className="mt-2 text-xs break-words text-muted-foreground">
                      <StatusDot tone={runTone(result.run)} className="mr-2 -translate-y-px" />
                      <span className={cn(result.run.outcome === 'error' ? 'text-destructive' : 'text-foreground')}>
                        {runSummary(result.run, watch.kind)}
                      </span>
                      ，用时 {formatLatency(result.run.duration_ms)}
                      {result.run.detail && result.run.outcome !== 'error' && `。${result.run.detail}`}
                    </p>
                  ) : (
                    <p role="alert" className="mt-2 text-xs text-destructive">
                      {result.error}
                    </p>
                  )}
                </div>
              </div>
            )}
            {deleteError && (
              <p role="alert" className="mt-2 text-xs text-destructive">
                {deleteError}
              </p>
            )}

            {open && (
              <section id={historyId} aria-label={`「${watch.name}」的检查记录`} className="feed-in">
                <div className="min-h-0">
                  <div className="mt-4 rounded-xl bg-muted/50 px-4 py-3">
                    <p className="text-xs text-muted-foreground">
                      最近 7 天检查了 {watch.counts.runs_7d} 次，有变化 {watch.counts.changes_7d} 次，出错{' '}
                      <span className={cn(watch.counts.errors_7d > 0 && 'text-destructive')}>{watch.counts.errors_7d}</span> 次
                    </p>
                    {runs.runs === null ? (
                      <p role={runs.error ? 'alert' : 'status'} className={cn('py-3 text-sm', runs.error ? 'text-destructive' : 'text-muted-foreground')}>
                        {runs.error ? `读取记录失败：${runs.error}` : '读取中…'}
                      </p>
                    ) : runs.runs.length === 0 ? (
                      <p className="py-3 text-sm text-muted-foreground">还没有检查记录。</p>
                    ) : (
                      <div className="mt-1 max-h-[30rem] overflow-y-auto">
                        <RunList
                          runs={runs.runs}
                          fresh={runs.fresh}
                          kind={watch.kind}
                          target={watch.target_name}
                          botName={botName}
                        />
                        {runs.runs.length >= RUNS_LIMIT && (
                          <p className="pb-1 text-xs text-muted-foreground">只显示最近 {RUNS_LIMIT} 次。</p>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </article>
  )
}
