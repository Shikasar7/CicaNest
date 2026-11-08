import { useEffect, useMemo, useState, type CSSProperties } from 'react'

import { BotGlyph } from '@/components/BotGlyph'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { StatusDot, type Tone } from '@/components/StatusDot'
import { getJson, type Account, type AdapterState, type Bot, type Decision, type Status, type SystemView } from '@/lib/api'
import { useRecentDecisions } from '@/lib/activity'
import { formatAgo, formatTime, formatUptime } from '@/lib/format'
import { useBots, useLive } from '@/lib/live'
import { botLook } from '@/lib/persona'
import { platformName } from '@/lib/platforms'
import { cn } from '@/lib/utils'
import { useWatches } from '@/lib/watches'

const MODE = {
  live: { label: '正式发言', className: 'bg-ok/15 text-ok' },
  observe: { label: '只看不说', className: 'ring-1 ring-border text-muted-foreground' },
  paused: { label: '已暂停', className: 'bg-warn/15 text-warn' },
} as const

const CONNECTION: Record<AdapterState, { text: string; tone: Tone }> = {
  connected: { text: '在线', tone: 'ok' },
  connecting: { text: '正在连接', tone: 'wait' },
  reconnecting: { text: '重连中', tone: 'wait' },
  offline: { text: 'QQ 离线', tone: 'wait' },
  deaf: { text: '收不到消息', tone: 'bad' },
  auth_failed: { text: '令牌错误', tone: 'bad' },
  misconfigured: { text: '配置不对', tone: 'bad' },
  stopped: { text: '已停止', tone: 'off' },
}

const speaks = (d: Decision) => (d.outcome === 'replied' || d.outcome === 'would_reply') && !!d.reply

/** Today's figures, the ones that tell whether a bot is doing its job. */
function Today({ bot }: { bot: Bot }) {
  const quiet = (bot.today.skipped ?? 0) + (bot.today.suppressed ?? 0)
  const items = [
    { label: '已发送', value: bot.today.replied ?? 0 },
    { label: '本来会回', value: bot.today.would_reply ?? 0 },
    { label: '没开口', value: quiet },
    { label: '出错', value: bot.today.failed ?? 0, bad: true },
  ]
  return (
    <dl className="grid grid-cols-2 gap-x-2 gap-y-3 @[17rem]:grid-cols-4">
      {items.map((i) => (
        <div key={i.label} className="flex flex-col-reverse">
          <dt className="text-xs whitespace-nowrap text-muted-foreground">{i.label}</dt>
          <dd
            className={cn(
              'text-xl font-semibold tabular-nums tracking-tight',
              i.bad && i.value > 0 ? 'text-destructive' : i.value === 0 && 'text-muted-foreground/60',
            )}
          >
            {i.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** The bot's latest line: solid when it really went out, dashed when it was only what it would have said. */
function LastLine({ line, groupOf }: { line: Decision | undefined; groupOf: Map<number, string> }) {
  if (!line) {
    return (
      <p className="rounded-2xl rounded-tl-md border border-dashed px-4 py-3 text-sm text-muted-foreground">
        还没开口
      </p>
    )
  }
  const real = line.outcome === 'replied'
  return (
    <figure
      key={line.id}
      className={cn(
        'animate-in fade-in-0 zoom-in-95 duration-500 rounded-2xl rounded-tl-md px-4 py-3',
        real
          ? 'bg-[color-mix(in_oklch,var(--hue)_10%,var(--card))]'
          : 'border border-dashed border-[color-mix(in_oklch,var(--hue)_45%,transparent)]',
      )}
    >
      <blockquote className="text-[15px] leading-relaxed break-words whitespace-pre-line">{line.reply}</blockquote>
      <figcaption className="mt-1.5 text-xs text-muted-foreground">
        {real ? '发到' : '本来会发到'}「{groupOf.get(line.channel_id) ?? '群里'}」，{formatAgo(line.created_at)}
      </figcaption>
    </figure>
  )
}

function Presence({ account, bot, line, groupOf }: { account: Account; bot: Bot; line?: Decision; groupOf: Map<number, string> }) {
  const look = botLook(bot.name, bot.id)
  const connection = CONNECTION[account.status.state] ?? { text: account.status.state, tone: 'off' as const }
  const online = account.status.state === 'connected'
  const mode = MODE[bot.mode]
  return (
    <article
      style={{ '--hue': look.hue } as CSSProperties}
      className="@container flex flex-col gap-5 rounded-2xl border bg-card p-5 shadow-[var(--shadow-lift)]"
    >
      <header className="space-y-3">
        <div className="flex items-center gap-3.5">
          <BotGlyph name={bot.name} id={bot.id} size="lg" online={online} breathing={online && bot.mode !== 'paused'} />
          <div className="min-w-0 flex-1 space-y-1">
            <h2 className="truncate text-base font-semibold tracking-tight">{bot.name}</h2>
            <div className="flex items-center gap-2 text-xs whitespace-nowrap text-muted-foreground">
              <span className={cn('rounded-full px-2 py-0.5 font-medium', mode.className)}>{mode.label}</span>
              <StatusDot tone={connection.tone} />
              <span className={cn('truncate', !online && 'text-foreground')}>
                {online ? `在线，上线于 ${formatAgo(account.status.since)}` : connection.text}
              </span>
            </div>
          </div>
        </div>
        {!online && account.status.detail && (
          <p className="line-clamp-2 text-xs leading-snug text-muted-foreground" title={account.status.detail}>
            {account.status.detail}
          </p>
        )}
      </header>
      <LastLine line={line} groupOf={groupOf} />
      <Today bot={bot} />
      <footer className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs text-muted-foreground">
        <span>
          {bot.provider ? (
            <>
              模型 {bot.provider.id}
              {!bot.provider.available && <span className="text-warn">（暂停中）</span>}
            </>
          ) : bot.provider_id ? (
            <span className="text-destructive">模型 {bot.provider_id} 配置有误</span>
          ) : (
            '还没有模型'
          )}
        </span>
        <span>在 {bot.channels.filter((c) => c.enabled).length} 个群</span>
      </footer>
    </article>
  )
}

function Services({ accounts }: { accounts: Account[] }) {
  const [system, setSystem] = useState<SystemView | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const { channelsVersion } = useLive()
  const { data: watches } = useWatches()
  useEffect(() => {
    getJson<SystemView>('/api/system').then(setSystem, () => setSystem(null))
    getJson<Status>('/api/status').then(setStatus, () => setStatus(null))
  }, [channelsVersion])
  const rows: { name: string; tone: Tone; text: string }[] = [
    {
      name: 'nest',
      tone: status ? 'ok' : 'wait',
      text: status ? `已运行 ${formatUptime(status.uptime_seconds)}` : '读取中',
    },
  ]
  const boxes = system?.snowluma.mode === 'docker' ? system.containers : undefined
  // SnowLuma and QQ's containers mean nothing to whoever only runs other platforms.
  const hasQQ = accounts.length === 0 || accounts.some((a) => a.platform === 'qq')
  if (!hasQQ) {
    // nothing to show
  } else if (boxes && boxes.length > 1) {
    const up = boxes.filter((b) => b.running).length
    rows.push({
      name: 'QQ 容器',
      tone: up === boxes.length ? 'ok' : up === 0 ? 'bad' : 'wait',
      text: `${up} / ${boxes.length} 个在运行`,
    })
  } else if (system) {
    rows.push({
      name: boxes?.length === 1 ? boxes[0].name : 'SnowLuma',
      tone: !system.snowluma.configured ? 'off' : system.snowluma.running ? 'ok' : 'bad',
      text: !system.snowluma.configured ? '没有设置位置' : system.snowluma.running ? '运行中' : '没有运行',
    })
  }
  const platforms = accounts.length === 0 ? ['qq'] : [...new Set(accounts.map((a) => a.platform))]
  for (const key of platforms) {
    const own = accounts.filter((a) => a.platform === key)
    const up = own.filter((a) => a.status.state === 'connected').length
    rows.push({
      name: platformName(key),
      tone: own.length === 0 ? 'off' : up === own.length ? 'ok' : up === 0 ? 'bad' : 'wait',
      text: `${up} / ${own.length} 个号在线`,
    })
  }
  if (watches && watches.length > 0) {
    const running = watches.filter((w) => w.enabled)
    const failing = running.filter((w) => w.last_run?.outcome === 'error').length
    rows.push({
      name: '监控',
      tone: running.length === 0 ? 'off' : failing > 0 ? 'wait' : 'ok',
      text:
        running.length === 0
          ? '都停用了'
          : `${running.length} 个在运行${failing > 0 ? `，${failing} 个上次出错` : ''}`,
    })
  }
  return (
    <section aria-labelledby="services" className="rounded-2xl border bg-card p-5">
      <h2 id="services" className="mb-3 text-sm font-semibold">
        运行状态
      </h2>
      <ul className="space-y-2.5">
        {rows.map((r) => (
          <li key={r.name} className="flex items-center gap-2.5 text-sm">
            <StatusDot tone={r.tone} />
            <span className="w-20 font-medium">{r.name}</span>
            <span className="text-muted-foreground">{r.text}</span>
          </li>
        ))}
      </ul>
      <a href="#/system" className="mt-4 inline-block text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
        在「系统」页启动、重启或看日志
      </a>
    </section>
  )
}

const OUTCOME_TEXT: Record<Decision['outcome'], string> = {
  replied: '回了',
  would_reply: '本来会回',
  skipped: '想了想没回',
  suppressed: '没回',
  failed: '出错了',
}

function Feed({ decisions, groupOf, fresh, loaded }: ReturnType<typeof useRecentDecisions>) {
  return (
    <section aria-labelledby="feed" className="rounded-2xl border bg-card p-5">
      <h2 id="feed" className="mb-2 text-sm font-semibold">
        最近动态
      </h2>
      {decisions.length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">
          {loaded ? '还没有动态。有人在群里说话或点名 bot 后，它们的判断会出现在这里。' : '读取中…'}
        </p>
      ) : (
        <ol className="divide-y">
          {decisions.slice(0, 12).map((d) => (
            <li key={d.id} className={cn(fresh.has(d.id) && 'feed-in')}>
              <div className="flex gap-3 py-2.5">
                <BotGlyph name={d.bot_name} id={d.bot_id} size="sm" className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm">
                    <span className="font-medium">{d.bot_name}</span>
                    <span className={cn('mx-1.5', d.outcome === 'failed' ? 'text-destructive' : 'text-muted-foreground')}>
                      {OUTCOME_TEXT[d.outcome]}
                    </span>
                    {d.trigger_sender && <span className="text-muted-foreground">{d.trigger_sender}</span>}
                  </p>
                  <p className={cn('truncate text-sm', speaks(d) ? 'text-foreground' : 'text-muted-foreground')}>
                    {speaks(d) ? d.reply : d.reason}
                  </p>
                </div>
                <div className="shrink-0 text-right text-xs text-muted-foreground">
                  <p>{formatTime(d.created_at)}</p>
                  <p className="max-w-28 truncate">{groupOf.get(d.channel_id) ?? ''}</p>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

export function Overview() {
  const { accounts, state } = useLive()
  const { data: bots, error } = useBots<Bot[]>('/api/bots')
  const activity = useRecentDecisions()
  const botsByAccount = useMemo(() => new Map((bots ?? []).map((b) => [b.id, b])), [bots])
  const lastLine = useMemo(() => {
    // The live feed keeps only the latest few decisions of all bots: a quiet bot's line drops out of it, so
    // start from the one the server remembers for each bot (newer ones arrive through the feed).
    const latest = new Map<string, Decision>()
    for (const b of bots ?? []) if (b.last_line) latest.set(b.id, b.last_line)
    for (const d of activity.decisions) {
      const known = latest.get(d.bot_id)
      if (speaks(d) && (!known || d.id > known.id)) latest.set(d.bot_id, d)
    }
    return latest
  }, [activity.decisions, bots])
  // Re-render twice a minute so "上线于 3 分钟前" stays current (local timer, no network).
  const [, setTick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 30_000)
    return () => clearInterval(timer)
  }, [])

  if (state === 'open' && accounts.length === 0) {
    return (
      <section className="rounded-2xl border bg-card p-6">
        <h2 className="text-base font-semibold">还没有账号</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          先到「模型」页加一个模型，再到「Bot」页点「增加 bot」，选平台、填账号；连上以后，在这个 bot 的设置里打开它要说话的群。
        </p>
      </section>
    )
  }

  const withBot = accounts.filter((a) => botsByAccount.has(a.id))
  const withoutBot = accounts.filter((a) => bots !== null && !botsByAccount.has(a.id))
  return (
    <div className="flex flex-col gap-6">
      {bots === null ? error ? <FirstLoad error={error} what=" bot " /> : null : <StaleNotice error={error} />}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {withBot.map((account) => {
          const bot = botsByAccount.get(account.id)!
          return <Presence key={account.id} account={account} bot={bot} line={lastLine.get(bot.id)} groupOf={activity.groupOf} />
        })}
      </div>
      {withoutBot.length > 0 && (
        <p className="text-sm text-muted-foreground">
          还没有 bot 的账号：{withoutBot.map((a) => a.name || a.self_id).join('、')}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Feed {...activity} />
        <div className="space-y-4">
          <Services accounts={accounts} />
          {bots && (
            <p className="px-1 text-xs leading-relaxed text-muted-foreground">
              {bots.some((b) => b.mode === 'live')
                ? '有 bot 在「正式发言」：判断后会真的发到群里。需要时点左下角「全部暂停」。'
                : '所有 bot 都是「只看不说」或暂停：判断结果显示在「消息」页，不会真的发到群里。'}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
