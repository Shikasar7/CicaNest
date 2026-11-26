import { useState, type ComponentType, type CSSProperties, type ReactNode } from 'react'
import { Activity, Camera, Cpu, Drama, MessageCircle, MessageCircleHeart, Monitor, Plug, Users } from 'lucide-react'

import { AddBot } from '@/components/bots/AddBot'
import { AddGroup } from '@/components/bots/AddGroup'
import { AddPrivate } from '@/components/bots/AddPrivate'
import { AvatarPicker } from '@/components/bots/AvatarPicker'
import { BotForm } from '@/components/bots/BotForm'
import { ChannelRow } from '@/components/bots/ChannelRow'
import { ModelPanel } from '@/components/bots/ModelPanel'
import { MoveIn } from '@/components/bots/MoveIn'
import { PersonaSets } from '@/components/bots/PersonaSets'
import { BotGlyph } from '@/components/BotGlyph'
import { MODES, OUTCOMES, botForm, isPrivate, useAction } from '@/components/bots/shared'
import { StatusDot, type Tone as Dot } from '@/components/StatusDot'
import { Button } from '@/components/ui/button'
import { sendJson, type AdapterState, type Bot, type BotChannel, type BotMode, type Ok, type Persona, type ProviderView, type Tone } from '@/lib/api'
import { useDraft, useEditFocus } from '@/lib/drafts'
import { formatAgo } from '@/lib/format'
import { useLive, usePlatforms } from '@/lib/live'
import { botLook } from '@/lib/persona'
import { platformName, platformOf } from '@/lib/platforms'
import { cn } from '@/lib/utils'

function confirmLive(bot: Bot): boolean {
  if (bot.platform === 'desk') return true // only you hear her on the desk
  return window.confirm(
    `让「${bot.name}」真的在群里发言？\n\n` +
      '请先确认别的程序已经不再用这个号回复，否则两边会重复回复。' +
      '随时可以点「暂停」或左下角的「全部暂停」停下来。',
  )
}

/** The chosen mode's colour: the same for every bot, so a colour always means the same state. */
const MODE_TEXT: Record<BotMode, string> = { live: 'text-ok', observe: 'text-foreground', paused: 'text-warn' }

const CONNECTION: Record<AdapterState, { text: string; tone: Dot }> = {
  connected: { text: '在线', tone: 'ok' },
  connecting: { text: '正在连接', tone: 'wait' },
  reconnecting: { text: '重连中', tone: 'wait' },
  offline: { text: '离线', tone: 'wait' },
  deaf: { text: '收不到消息', tone: 'bad' },
  auth_failed: { text: '令牌错误', tone: 'bad' },
  misconfigured: { text: '配置不对', tone: 'bad' },
  stopped: { text: '已停止', tone: 'off' },
}

/** "2 个群，1 个关着" / "1 个人". */
function countText(channels: BotChannel[], unit: string): string {
  const off = channels.filter((c) => !c.enabled).length
  return `${channels.length} ${unit}` + (off ? `，${off} 个关着` : '')
}

function ForeignWarning({ bot }: { bot: Bot }) {
  if (!bot.foreign_at) return null // the backend only reports sightings from the last 30 minutes
  return (
    <p
      className={cn(
        'rounded-md border px-3 py-2 text-xs',
        bot.mode === 'live' ? 'border-destructive/40 bg-destructive/10 text-destructive' : 'border-amber-500/40 bg-amber-500/10',
      )}
    >
      {formatAgo(bot.foreign_at)}有别的程序用这个号发过言（可能是别的 bot 程序 还在运行）。
      {bot.mode === 'live' ? '现在是正式发言，两边可能会重复回复。' : '现在是只看不说，不影响。'}
    </p>
  )
}

/** 状态: what she does with what she hears, and today's figures where she speaks in groups. */
function StatePanel({ bot, reload }: { bot: Bot; reload: () => void }) {
  const { busy, error, run } = useAction(reload)
  const setMode = (mode: BotMode) => run(() => sendJson<Ok>('PATCH', `/api/bots/${encodeURIComponent(bot.id)}`, { mode }))
  const deskOnly = bot.platform === 'desk'
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <div role="radiogroup" aria-label={`${bot.name} 的模式`} className="inline-flex rounded-lg bg-muted p-0.5">
          {MODES.map((m) => {
            const current = bot.mode === m.mode
            return (
              <button
                key={m.mode}
                type="button"
                role="radio"
                aria-checked={current}
                disabled={busy || current || (m.mode === 'live' && !bot.provider)}
                onClick={() => (m.mode !== 'live' || confirmLive(bot)) && void setMode(m.mode)}
                className={cn(
                  'rounded-md px-3 py-1 text-sm transition-all duration-200 disabled:cursor-default',
                  current
                    ? cn('bg-card font-medium shadow-[var(--shadow-lift)]', MODE_TEXT[m.mode])
                    : 'text-muted-foreground enabled:hover:text-foreground disabled:opacity-40',
                )}
              >
                {m.label}
              </button>
            )
          })}
        </div>
        <p className="text-xs text-muted-foreground">{MODES.find((m) => m.mode === bot.mode)?.hint}</p>
        {bot.mode !== 'live' && !bot.provider && (
          <p className="text-xs text-muted-foreground">没有可用的模型，不能正式发言：先在「模型」里给她选一个。</p>
        )}
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
      <ForeignWarning bot={bot} />
      {!deskOnly && (
        <dl className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          {OUTCOMES.map((o) => (
            <div key={o.key} className="flex flex-col-reverse rounded-lg bg-surface px-2.5 py-2">
              <dt className="text-xs text-muted-foreground">今天{o.label}</dt>
              <dd className={cn('text-lg font-semibold tabular-nums', o.key === 'failed' && (bot.today.failed ?? 0) > 0 && 'text-destructive')}>
                {bot.today[o.key] ?? 0}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

function Place({ Icon, title, children }: { Icon: ComponentType<{ className?: string }>; title: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-2 rounded-xl border bg-surface/60 p-4">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Icon className="size-4 text-muted-foreground" />
        {title}
      </div>
      <div className="space-y-2 text-sm text-muted-foreground">{children}</div>
    </div>
  )
}

/** 接入: where she can be reached. The desk always; a platform when one is connected, or the way to connect one. */
function PlacesPanel({ bot, bots, providers, reload, onMoved }: {
  bot: Bot
  bots: Bot[]
  providers: ProviderView[] | null
  reload: () => void
  /** She moved into a new account: her id is now that account's; container: QQ's, to scan the login code of. */
  onMoved: (botId: string, container: string) => void
}) {
  const { accounts } = useLive()
  const { data: platforms } = usePlatforms()
  const deskOnly = bot.platform === 'desk'
  const status = accounts.find((a) => a.id === bot.id)?.status
  const connection = status ? (CONNECTION[status.state] ?? { text: status.state, tone: 'off' as const }) : null
  const label = platformOf(bot, platforms)?.label ?? platformName(bot.platform ?? 'qq')
  const rooms = bots.filter((b) => b.id !== bot.id && b.platform !== 'desk')
  return (
    <div className={cn('grid gap-3', !deskOnly && 'lg:grid-cols-2')}>
      <Place Icon={Monitor} title="桌面">
        <p>在「聊天」里和你说话；小窝、桌宠和小游戏也都在桌面上。一直都在，不用设置。</p>
        <Button size="sm" variant="outline" asChild>
          <a href={`#/chat?bot=${encodeURIComponent(bot.id)}`}>
            <MessageCircleHeart />
            和她聊天
          </a>
        </Button>
      </Place>
      {deskOnly ? (
        <Place Icon={Plug} title="接一个平台">
          <p>
            想让{bot.name}也去 QQ、Discord 这些地方说话，就给她接一个号。一个伙伴同时接一个平台；她的名字、头像、人设、记忆都不变。
          </p>
          <AddBot providers={providers} reload={reload} companion={bot} onMoved={onMoved} />
          {rooms.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer">号已经加好了？搬进一个现有的号</summary>
              <div className="mt-2">
                <MoveIn bot={bot} bots={bots} reload={reload} />
              </div>
            </details>
          )}
        </Place>
      ) : (
        <Place
          Icon={Plug}
          title={
            <span className="flex flex-wrap items-center gap-2">
              {label} {bot.self_id}
              <span className="inline-flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
                <StatusDot tone={connection?.tone ?? 'off'} />
                {connection?.text ?? '没有连接'}
              </span>
            </span>
          }
        >
          {status && status.state !== 'connected' && status.detail && <p className={cn(status.state === 'deaf' && 'text-destructive')}>{status.detail}</p>}
          <p>在群里和私聊里说话，去哪些群、用什么人设在「群和私聊」里设。</p>
          <p className="text-xs">
            连接地址、令牌、扫码登录在
            <a href="#/platforms" className="mx-0.5 underline underline-offset-4 hover:text-foreground">
              平台接入
            </a>
            页。
          </p>
        </Place>
      )}
    </div>
  )
}

/** 群和私聊 (on a platform) or 和你的聊天 (on the desk only): each place she talks, with its persona and tone. */
function ChatsPanel({ bot, personas, tones, reload }: { bot: Bot; personas: Persona[] | null; tones: Tone[] | null; reload: () => void }) {
  const deskOnly = bot.platform === 'desk'
  const groups = bot.channels.filter((c) => !isPrivate(c))
  const privates = bot.channels.filter(isPrivate)
  const rows = (list: BotChannel[]) => (
    <ul className="space-y-2">
      {list.map((channel) => (
        <ChannelRow key={channel.channel_id} bot={bot} channel={channel} personas={personas} tones={tones} reload={reload} />
      ))}
    </ul>
  )
  if (deskOnly) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">和你的聊天用哪个人设、什么语气，要不要长期记忆。</p>
        {privates.length === 0 ? <p className="py-2 text-sm text-muted-foreground">还没有和你的聊天。去「聊天」里跟她说句话就有了。</p> : rows(privates)}
      </div>
    )
  }
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <Users className="size-4 text-muted-foreground" />
          群<span className="text-xs font-normal text-muted-foreground">{countText(groups, '个群')}</span>
        </h3>
        {groups.length === 0 ? <p className="py-2 text-sm text-muted-foreground">还没有在任何群启用</p> : rows(groups)}
        <AddGroup bot={bot} personas={personas} reload={reload} />
      </div>
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <MessageCircle className="size-4 text-muted-foreground" />
          私聊<span className="text-xs font-normal text-muted-foreground">{countText(privates, '个人')}</span>
        </h3>
        {privates.length === 0 ? <p className="py-2 text-sm text-muted-foreground">不回任何人的私聊</p> : rows(privates)}
        <AddPrivate bot={bot} personas={personas} reload={reload} />
      </div>
    </div>
  )
}

type Tab = 'state' | 'places' | 'chats' | 'persona' | 'model'

/** One companion on the 伙伴 page: who she is at the top, then a tab each for her state, where she can be
 * reached, the places she talks, her persona sets and her model. Every tab stays mounted (hidden when not shown),
 * so a half-typed edit survives switching. */
export function BotCard({
  bot,
  providers,
  personas,
  tones,
  bots,
  reload,
  onMoved,
}: {
  bot: Bot
  providers: ProviderView[] | null
  personas: Persona[] | null
  tones: Tone[] | null
  /** All of them: where a companion of the desk may move in. */
  bots: Bot[]
  reload: () => void
  onMoved: (botId: string, container: string) => void
}) {
  const draft = useDraft(botForm(bot))
  const { firstField, editButton } = useEditFocus<HTMLInputElement>(draft.editing)
  const names = [bot.name, ...bot.aliases].join('、')
  const { accounts } = useLive()
  const { data: platforms } = usePlatforms()
  const deskOnly = bot.platform === 'desk'
  const status = accounts.find((a) => a.id === bot.id)?.status
  const online = bot.mode !== 'paused' && (deskOnly || status?.state === 'connected')
  const [picking, setPicking] = useState(false)
  const [tab, setTab] = useState<Tab>('state')
  const modeLabel = MODES.find((m) => m.mode === bot.mode)?.label ?? bot.mode
  const where = deskOnly ? '只在桌面上' : `桌面 · ${platformOf(bot, platforms)?.label ?? platformName(bot.platform ?? 'qq')} ${bot.self_id}`
  const tabs: { key: Tab; label: string; Icon: ComponentType<{ className?: string }>; note: string }[] = [
    { key: 'state', label: '状态', Icon: Activity, note: modeLabel },
    { key: 'places', label: '接入', Icon: Plug, note: deskOnly ? '桌面' : `桌面 + ${platformName(bot.platform ?? 'qq')}` },
    {
      key: 'chats',
      label: deskOnly ? '和你的聊天' : '群和私聊',
      Icon: Users,
      note: deskOnly ? '' : `${bot.channels.filter((c) => !isPrivate(c) && c.enabled).length} 个群`,
    },
    { key: 'persona', label: '人设方案', Icon: Drama, note: (bot.persona_sets ?? []).find((s) => s.current)?.name ?? '' },
    { key: 'model', label: '模型', Icon: Cpu, note: bot.provider_id ?? '还没选' },
  ]
  return (
    <article style={{ '--hue': botLook(bot.name, bot.id).hue } as CSSProperties} className="flex min-w-0 flex-col gap-5">
      <header className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={() => setPicking((p) => !p)}
          aria-expanded={picking}
          aria-label={`换${bot.name}的头像`}
          title="换头像"
          className="group/avatar relative shrink-0 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-[var(--hue)] focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          <BotGlyph name={bot.name} id={bot.id} size="lg" online={online} breathing={online} />
          <span
            aria-hidden
            className={cn(
              'absolute -right-1 -bottom-1 grid size-[20px] place-items-center rounded-full bg-card text-muted-foreground shadow-[var(--shadow-lift)] ring-1 ring-border transition-[color,scale] duration-200 group-hover/avatar:scale-110 group-hover/avatar:text-[var(--hue)]',
              picking && 'text-[var(--hue)]',
            )}
          >
            <Camera className="size-3" />
          </span>
        </button>
        <div className="min-w-0 flex-1 space-y-0.5">
          <h2 className="truncate text-xl font-semibold tracking-tight">{bot.name}</h2>
          <p className="text-sm break-words text-muted-foreground">
            {where}。叫到这些名字算点名：{names}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="outline" asChild>
            <a href={`#/chat?bot=${encodeURIComponent(bot.id)}`}>
              <MessageCircleHeart />
              聊天
            </a>
          </Button>
          {!draft.editing && (
            <Button ref={editButton} size="sm" variant="outline" onClick={draft.start}>
              编辑
            </Button>
          )}
        </div>
      </header>
      {picking && (
        <div className="rounded-2xl border bg-card p-5">
          <AvatarPicker bot={bot} onDone={() => setPicking(false)} />
        </div>
      )}
      {draft.editing && (
        <div className="rounded-2xl border bg-card p-5">
          <BotForm bot={bot} providers={providers} draft={draft} firstField={firstField} onSaved={reload} />
        </div>
      )}
      <div role="tablist" aria-label={`${bot.name}的设置`} className="flex flex-wrap gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`${bot.id}-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`${bot.id}-panel-${t.key}`}
            onClick={() => setTab(t.key)}
            className={cn(
              '-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors',
              tab === t.key
                ? 'border-[var(--hue)] font-medium text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            <t.Icon className="size-4" />
            {t.label}
            {t.note && <span className="hidden max-w-28 truncate text-xs font-normal text-muted-foreground sm:inline">{t.note}</span>}
          </button>
        ))}
      </div>
      {tabs.map((t) => (
        <section
          key={t.key}
          role="tabpanel"
          id={`${bot.id}-panel-${t.key}`}
          aria-labelledby={`${bot.id}-tab-${t.key}`}
          hidden={tab !== t.key}
          className="rounded-2xl border bg-card p-5"
        >
          {t.key === 'state' && <StatePanel bot={bot} reload={reload} />}
          {t.key === 'places' && <PlacesPanel bot={bot} bots={bots} providers={providers} reload={reload} onMoved={onMoved} />}
          {t.key === 'chats' && <ChatsPanel bot={bot} personas={personas} tones={tones} reload={reload} />}
          {t.key === 'persona' && <PersonaSets bot={bot} personas={personas} reload={reload} />}
          {/* Keyed by model: a 测试一下 result belongs to the model it tested. */}
          {t.key === 'model' && <ModelPanel key={bot.provider_id ?? ''} bot={bot} />}
        </section>
      ))}
    </article>
  )
}
