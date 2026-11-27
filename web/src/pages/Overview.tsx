import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { CircleAlert, MessageCircleHeart, Settings2, Sparkles } from 'lucide-react'

import { BotGlyph } from '@/components/BotGlyph'
import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { StatusDot, type Tone } from '@/components/StatusDot'
import { Button } from '@/components/ui/button'
import { getJson, type Account, type AdapterState, type Bot, type Decision, type Status, type SystemView } from '@/lib/api'
import { useRecentDecisions } from '@/lib/activity'
import { formatAgo, formatTime, formatUptime } from '@/lib/format'
import type { LifeView } from '@/lib/life'
import { useBots, useLive, useProviders } from '@/lib/live'
import { useLiveData } from '@/lib/live-data'
import { botLook } from '@/lib/persona'
import { platformName } from '@/lib/platforms'
import { cn } from '@/lib/utils'
import { useWatches } from '@/lib/watches'

/** On a platform: what she does with what she hears there. */
const MODE = {
  live: { label: '正式发言', className: 'bg-ok/15 text-ok' },
  observe: { label: '只看不说', className: 'ring-1 ring-border text-muted-foreground' },
  paused: { label: '已暂停', className: 'bg-warn/15 text-warn' },
} as const

const CONNECTION: Record<AdapterState, { text: string; tone: Tone }> = {
  connected: { text: '在线', tone: 'ok' },
  connecting: { text: '正在连接', tone: 'wait' },
  reconnecting: { text: '重连中', tone: 'wait' },
  offline: { text: '离线', tone: 'wait' },
  deaf: { text: '收不到消息', tone: 'bad' },
  auth_failed: { text: '令牌错误', tone: 'bad' },
  misconfigured: { text: '配置不对', tone: 'bad' },
  stopped: { text: '已停止', tone: 'off' },
}

const speaks = (d: Decision) => (d.outcome === 'replied' || d.outcome === 'would_reply') && !!d.reply

const onDesk = (bot: Bot) => bot.platform === 'desk'

/** The newest line of her chat with you on the desk (GET /api/desk, only what this page needs). */
type DeskLast = { bot_id: string; last: { text: string; from_self: boolean; sent_at: number; sticker: boolean } | null }

/** What the card quotes: her newest line, from the desk or from a group, whichever is newer. */
type Line = { text: string; where: string; at: number; real: boolean }

function latestLine(decision: Decision | undefined, desk: DeskLast | undefined, groupOf: Map<number, string>): Line | null {
  const fromDesk =
    desk?.last && desk.last.from_self
      ? { text: desk.last.sticker ? '（发了一个表情）' : desk.last.text, where: '和你聊天时', at: desk.last.sent_at, real: true }
      : null
  const fromGroup = decision?.reply
    ? {
        text: decision.reply,
        where: `${decision.outcome === 'replied' ? '发到' : '本来会发到'}「${groupOf.get(decision.channel_id) ?? '群里'}」`,
        at: decision.created_at,
        real: decision.outcome === 'replied',
      }
    : null
  if (!fromDesk) return fromGroup
  if (!fromGroup) return fromDesk
  return fromDesk.at >= fromGroup.at ? fromDesk : fromGroup
}

/** Her latest line: solid when it really went out, dashed when it was only what she would have said. */
function LastLine({ line, name }: { line: Line | null; name: string }) {
  if (!line) {
    return (
      <p className="rounded-2xl rounded-tl-md border border-dashed px-4 py-3 text-sm text-muted-foreground">
        还没开口。去「聊天」跟{name}打个招呼吧。
      </p>
    )
  }
  return (
    <figure
      className={cn(
        'animate-in fade-in-0 zoom-in-95 duration-300 rounded-2xl rounded-tl-md px-4 py-3',
        line.real
          ? 'bg-[color-mix(in_oklch,var(--hue)_10%,var(--card))]'
          : 'border border-dashed border-[color-mix(in_oklch,var(--hue)_45%,transparent)]',
      )}
    >
      <blockquote className="line-clamp-3 text-[15px] leading-relaxed break-words whitespace-pre-line">{line.text}</blockquote>
      <figcaption className="mt-1.5 text-xs text-muted-foreground">
        {line.where}，{formatAgo(line.at)}
      </figcaption>
    </figure>
  )
}

/** Where she is: always on the desk, and on a platform if one is connected. */
function Places({ bot, account }: { bot: Bot; account: Account | undefined }) {
  const connection = account ? (CONNECTION[account.status.state] ?? { text: account.status.state, tone: 'off' as const }) : null
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs whitespace-nowrap">
      <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">桌面</span>
      {!onDesk(bot) && (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
          <StatusDot tone={connection?.tone ?? 'off'} className="size-1.5" />
          {platformName(bot.platform ?? 'qq')}
          <span className={cn(connection?.tone !== 'ok' && 'text-foreground')}>{connection?.text ?? '没有连接'}</span>
        </span>
      )}
      {!onDesk(bot) && <span className={cn('rounded-full px-2 py-0.5 font-medium', MODE[bot.mode].className)}>{MODE[bot.mode].label}</span>}
    </div>
  )
}

function Companion({
  bot,
  account,
  line,
  now,
}: {
  bot: Bot
  account: Account | undefined
  line: Line | null
  /** What she is doing in 小窝 right now, if her days are on. */
  now: string
}) {
  const look = botLook(bot.name, bot.id)
  const awake = bot.mode !== 'paused' && !!bot.provider
  const groups = bot.channels.filter((c) => c.enabled && c.kind !== 'private').length
  return (
    <article
      style={{ '--hue': look.hue } as CSSProperties}
      className="@container flex flex-col gap-4 rounded-2xl border bg-card p-5 shadow-[var(--shadow-lift)]"
    >
      <header className="flex items-center gap-3.5">
        <BotGlyph name={bot.name} id={bot.id} size="lg" online={awake} breathing={awake} />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-baseline gap-2">
            <h2 className="truncate text-base font-semibold tracking-tight">{bot.name}</h2>
            {now && <span className="truncate text-xs text-muted-foreground">{now}</span>}
          </div>
          <Places bot={bot} account={account} />
        </div>
      </header>
      <LastLine line={line} name={bot.name} />
      <footer className="mt-auto flex flex-wrap items-center gap-2 border-t pt-3 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 truncate" title={bot.provider?.id ?? bot.provider_id ?? ''}>
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
          {!onDesk(bot) && ` · ${groups} 个群`}
        </span>
        <Button size="sm" variant="ghost" asChild>
          <a href={`#/chat?bot=${encodeURIComponent(bot.id)}`}>
            <MessageCircleHeart />
            聊天
          </a>
        </Button>
        <Button size="sm" variant="ghost" asChild>
          <a href={`#/bots?bot=${encodeURIComponent(bot.id)}`}>
            <Settings2 />
            设置
          </a>
        </Button>
      </footer>
    </article>
  )
}

type Notice = { key: string; text: string; href: string; bad: boolean }

/** 需要你看一下: what keeps a companion from talking. Nothing to show, nothing shown. */
function notices(bots: Bot[], accounts: Account[]): Notice[] {
  const list: Notice[] = []
  for (const bot of bots) {
    if (bot.provider_id && !bot.provider) {
      list.push({ key: `${bot.id}-model`, text: `${bot.name}的模型「${bot.provider_id}」配置有误，她现在说不了话`, href: '#/models', bad: true })
    } else if (!bot.provider_id) {
      list.push({ key: `${bot.id}-model`, text: `${bot.name}还没有模型，选一个她才能说话`, href: `#/bots?bot=${encodeURIComponent(bot.id)}`, bad: false })
    } else if (bot.provider && !bot.provider.available) {
      list.push({ key: `${bot.id}-model`, text: `${bot.name}的模型「${bot.provider.id}」暂时用不了，会自己重试`, href: '#/models', bad: false })
    }
  }
  for (const account of accounts) {
    const state = account.status.state
    if (state === 'connected' || state === 'connecting') continue
    const bot = bots.find((b) => b.id === account.id)
    if (bot?.mode === 'paused') continue
    const connection = CONNECTION[state] ?? { text: state, tone: 'bad' as const }
    list.push({
      key: `${account.id}-link`,
      text: `${bot?.name ?? account.name}在${platformName(account.platform)}上${connection.text}${account.status.detail ? `：${account.status.detail}` : ''}`,
      href: '#/platforms',
      bad: connection.tone === 'bad',
    })
  }
  return list
}

function Attention({ items }: { items: Notice[] }) {
  if (items.length === 0) return null
  return (
    <section aria-labelledby="attention" className="rounded-2xl border border-warn/40 bg-warn/5 p-5">
      <h2 id="attention" className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <CircleAlert className="size-4 text-warn" />
        需要你看一下
      </h2>
      <ul className="space-y-2">
        {items.map((n) => (
          <li key={n.key} className="text-sm">
            <a href={n.href} className={cn('underline-offset-4 hover:underline', n.bad && 'text-destructive')}>
              {n.text}
            </a>
          </li>
        ))}
      </ul>
    </section>
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
  // SnowLuma and QQ's containers only mean something once a QQ account is connected.
  if (!accounts.some((a) => a.platform === 'qq')) {
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
  for (const key of [...new Set(accounts.map((a) => a.platform))]) {
    const own = accounts.filter((a) => a.platform === key)
    const up = own.filter((a) => a.status.state === 'connected').length
    rows.push({
      name: platformName(key),
      tone: up === own.length ? 'ok' : up === 0 ? 'bad' : 'wait',
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
      <a
        href={accounts.length > 0 ? '#/platforms' : '#/system'}
        className="mt-4 inline-block text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
      >
        {accounts.length > 0 ? '各平台的连接在「平台接入」页，日志在「系统」页' : '日志和重启在「系统」页'}
      </a>
    </section>
  )
}

/** 她们的近况: the latest little thing that happened in 小窝 (what each is doing is on her card). */
function Lately({ life }: { life: LifeView | null }) {
  if (!life?.on || life.moments.length === 0) return null
  const nameOf = new Map(life.people.map((p) => [p.bot_id, p.name]))
  return (
    <section aria-labelledby="lately" className="rounded-2xl border bg-card p-5">
      <h2 id="lately" className="mb-3 text-sm font-semibold">
        她们的近况
      </h2>
      <ul className="space-y-2">
        {life.moments.slice(0, 3).map((m) => (
          <li key={m.id} className="flex items-start gap-2.5 text-sm">
            <BotGlyph name={nameOf.get(m.bot_id) ?? ''} id={m.bot_id} size="sm" className="mt-0.5" />
            <span className="min-w-0 text-muted-foreground">
              <span className="font-medium text-foreground">{nameOf.get(m.bot_id) ?? ''}</span> {m.text}
            </span>
          </li>
        ))}
      </ul>
      <a href="#/chat" className="mt-3 inline-block text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
        在「聊天」页的小窝里看看
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

function Feed({ decisions, groupOf, fresh }: ReturnType<typeof useRecentDecisions>) {
  if (decisions.length === 0) return null
  return (
    <section aria-labelledby="feed" className="rounded-2xl border bg-card p-5">
      <h2 id="feed" className="mb-2 text-sm font-semibold">
        群里的动态
      </h2>
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
    </section>
  )
}

/** A nest with no companion yet: a model first if there is none, then her. Nothing about platforms. */
function Welcome({ hasModel }: { hasModel: boolean }) {
  // In the middle of the page (the header, the page's bottom margin and a notice above take about 12rem).
  return (
    <div className="flex min-h-[calc(100svh-12rem)] items-center justify-center py-6">
      <section className="flex w-full max-w-2xl flex-col items-center gap-5 rounded-3xl border bg-card px-8 py-12 text-center shadow-[var(--shadow-lift)] md:px-12">
        <BotGlyph name="nest" id="welcome" size="lg" />
        <h2 className="text-2xl font-semibold tracking-tight">欢迎来到 nest</h2>
        <p className="max-w-lg text-[15px] leading-relaxed text-muted-foreground">
          nest 里住着你的伙伴：可以在桌面上和她聊天、让她帮你做事、一起玩小游戏。
          {hasModel ? '先请一位伙伴住进来吧。' : '她要靠一个大模型说话，先加一个模型，再请她住进来。'}
        </p>
        <ol className="flex w-full max-w-md flex-col gap-2.5 text-left text-[15px]">
          <li className={cn('flex items-center gap-3 rounded-xl border px-4 py-3.5', hasModel && 'text-muted-foreground')}>
            <span className={cn('grid size-6 shrink-0 place-items-center rounded-full text-xs', hasModel ? 'bg-ok/15 text-ok' : 'bg-primary text-primary-foreground')}>
              {hasModel ? '✓' : '1'}
            </span>
            <span className="flex-1">{hasModel ? '已经有模型了' : '加一个模型'}</span>
            {!hasModel && (
              <Button size="sm" asChild>
                <a href="#/models">去加</a>
              </Button>
            )}
          </li>
          <li className={cn('flex items-center gap-3 rounded-xl border px-4 py-3.5', !hasModel && 'text-muted-foreground')}>
            <span className={cn('grid size-6 shrink-0 place-items-center rounded-full text-xs', hasModel ? 'bg-primary text-primary-foreground' : 'bg-muted')}>
              2
            </span>
            <span className="flex-1">创建一个伙伴，或者先看看示例伙伴</span>
            {hasModel && (
              <Button size="sm" asChild>
                <a href="#/chat">
                  <Sparkles />
                  开始
                </a>
              </Button>
            )}
          </li>
        </ol>
        <p className="max-w-lg text-[13px] leading-relaxed text-muted-foreground">
          想让她也去 QQ、Discord 这些地方说话，以后在「伙伴」页给她接一个平台就行。已经有 bot 号了？
          <a href="#/bots" className="underline underline-offset-4 hover:text-foreground">
            从号开始加
          </a>
        </p>
      </section>
    </div>
  )
}

export function Overview() {
  const { accounts, state, botsVersion } = useLive()
  const { data: bots, error } = useBots<Bot[]>('/api/bots')
  const { data: desk } = useBots<DeskLast[]>('/api/desk')
  const { data: providers } = useProviders()
  const life = useLiveData<LifeView>('/api/life', [botsVersion]).data
  const activity = useRecentDecisions()
  const lastDecision = useMemo(() => {
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
  // Re-render twice a minute so "3 分钟前" stays current (local timer, no network).
  const [, setTick] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 30_000)
    return () => clearInterval(timer)
  }, [])

  if (bots === null) return error ? <FirstLoad error={error} what="伙伴" /> : null
  if (state === 'open' && bots.length === 0) return <Welcome hasModel={(providers?.length ?? 0) > 0} />

  const nowOf = (botId: string) => {
    const now = life?.on ? life.people.find((p) => p.bot_id === botId)?.now : null
    return now ? `在${now.room}${now.doing}` : ''
  }
  const onPlatforms = bots.some((b) => !onDesk(b))
  const orphans = accounts.filter((a) => !bots.some((b) => b.id === a.id))
  return (
    <div className="flex flex-col gap-6">
      <StaleNotice error={error} />
      <Attention items={notices(bots, accounts)} />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,19rem),1fr))] gap-4">
        {bots.map((bot) => (
          <Companion
            key={bot.id}
            bot={bot}
            account={accounts.find((a) => a.id === bot.id)}
            line={latestLine(lastDecision.get(bot.id), desk?.find((d) => d.bot_id === bot.id), activity.groupOf)}
            now={nowOf(bot.id)}
          />
        ))}
      </div>
      {orphans.length > 0 && (
        <p className="text-sm text-muted-foreground">
          还没有伙伴的号：{orphans.map((a) => a.name || a.self_id).join('、')}
        </p>
      )}
      <div className={cn('grid gap-4', activity.decisions.length > 0 && 'lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]')}>
        <Feed {...activity} />
        <div className={cn('grid content-start gap-4', activity.decisions.length === 0 && 'md:grid-cols-2')}>
          <Services accounts={accounts} />
          <Lately life={life} />
          {onPlatforms && (
            <p className="px-1 text-xs leading-relaxed text-muted-foreground">
              {bots.some((b) => !onDesk(b) && b.mode === 'live')
                ? '有伙伴在平台上「正式发言」：判断后会真的发到群里。需要时点左下角「全部暂停」。'
                : '平台上的伙伴都是「只看不说」或暂停：判断结果显示在「消息记录」页，不会真的发到群里。'}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
